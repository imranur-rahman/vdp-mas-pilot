"""deepsec adapter: run side (ticket 06). Needs Docker.

deepsec runs inside the CyberGym-E2E task container (D1). The container is
prepared exactly as CyberGym prepares its own agents: started on Docker's
bridge for the install phase (task source, `prepare.sh`, then deepsec and its
npm dependencies), then moved to `cybergym-internal`, whose only exit is the
LiteLLM proxy at the gateway IP.

Roles (D2, "deepsec per-command"): each deepsec command runs with its own
proxy key whose metadata carries the role. One command may hold several
agents (setup's repository analysis, matcher repair, investigation); the
proxy's system-prompt hash tells those apart.

    threat-model       setup --through threat-model  (repository analysis: INFO.md + surface inventory)
    matcher-generator  setup --through coverage      (baseline scan, coverage gate, matcher repair, final scan)
    investigator       setup                         (AI investigation of scan candidates)
    revalidator        revalidate                    (re-checks every finding)

`.deepsec/` (without node_modules) is captured after every command: each
capture is a hand-off to the next role. If setup stops at its coverage gate,
the remaining roles never run, as for any deepsec user.
"""

import io
import json
import shlex
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from . import deepsec, infra, isolation, runner, state

UPSTREAM = "https://github.com/vercel-labs/deepsec"
PINNED_COMMIT = "4fa67220aff67ac93dfc840206d3d32bfa4411ec"
SYSTEM_DIR = infra.SYSTEMS / "deepsec"
# deepsec defaults to (cores - 1) parallel batches, 44 on this host. A run's
# hard cap is checked per call, so a smaller burst keeps overshoot small.
CONCURRENCY = 4
ROLES = [
    ("threat-model", ["setup", "--through", "threat-model"]),
    ("matcher-generator", ["setup", "--through", "coverage"]),
    ("investigator", ["setup"]),
    ("revalidator", ["revalidate"]),
]


def source_archive() -> Path:
    """`git archive` of deepsec at the pinned commit, from a local clone."""
    cache = infra.REPO / ".cache" / "deepsec"
    if not (cache / ".git").exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", UPSTREAM, str(cache)], check=True)
    if subprocess.run(["git", "cat-file", "-e", PINNED_COMMIT], cwd=cache).returncode != 0:
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=cache, check=True)
    out = cache.parent / f"deepsec-{PINNED_COMMIT[:12]}.tar.gz"
    if not out.exists():
        subprocess.run(["git", "archive", "--format=tar.gz", "-o", str(out), PINNED_COMMIT], cwd=cache, check=True)
    return out


def _capture_files(utils, cid: str, workspace: str) -> dict[str, bytes]:
    """`.deepsec/` without node_modules, read out of the container."""
    parent, name = workspace.rsplit("/", 1)
    r = subprocess.run(["docker", "exec", cid, "tar", "-C", parent, "-cf", "-",
                        "--exclude=node_modules", "--exclude=.pnpm-store", name],
                       capture_output=True, timeout=300)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
        for m in tar.getmembers():
            if m.isfile():
                files[m.name.split("/", 1)[1] if "/" in m.name else m.name] = tar.extractfile(m).read()
    return files


def _copy_dir_out(cid: str, src: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(["docker", "cp", f"{cid}:{src}", str(dest)], capture_output=True).returncode == 0


def run_deepsec(task: str, kind: str, budget_usd: float | None, timeout_s: int) -> Path:
    budget = budget_usd if budget_usd is not None else runner.DEFAULT_BUDGET[kind]
    model = runner.MODELS[kind]
    run_id = runner.new_run_id("deepsec", task, kind)
    run_dir = infra.RUNS / run_id
    run_dir.mkdir(parents=True)
    run_dir.chmod(0o777)  # the proxy container writes proxy.jsonl here

    gateway = infra.ensure_firewall()
    try:  # the shared stack is already up: do not touch it
        import httpx
        healthy = httpx.get(f"http://127.0.0.1:{infra.PROXY_PORT}/health/liveliness", timeout=5).status_code == 200
    except Exception:
        healthy = False
    if not healthy:
        infra.ensure_proxy(gateway)
    base_url = f"http://{gateway}:{infra.PROXY_PORT}"

    infra._import_cybergym()
    import utils as cg  # CyberGym-E2E scripts/utils.py
    from firewall import FirewallProxyManager

    project, task_name = task.split("/")
    script_path = infra.CYBERGYM / "projects" / project / task_name
    data_path = infra.REPO / "data" / "projects" / project / task_name
    import tomli
    config = tomli.loads((infra.CYBERGYM / "projects" / project / "project.toml").read_text())
    target = f"/src/{config['repo_to_patch']}"
    workspace = f"{target}/.deepsec"
    patches = sorted((SYSTEM_DIR / "patches").glob("*.patch"))

    meta = {
        "run_id": run_id, "system": "deepsec", "task": task, "kind": kind,
        "requested_model": model, "budget_usd": budget, "timeout_s": timeout_s,
        "pilot_commit": runner._git_rev(infra.CODE), "cybergym_commit": runner._git_rev(infra.CYBERGYM),
        "gateway_ip": gateway, "started_at": time.time(),
        "adapter": {"upstream": UPSTREAM, "commit": PINNED_COMMIT,
                    "patches": [str(p.relative_to(infra.CODE)) for p in patches],
                    "placement": "task container", "backend": "claude (Claude Agent SDK)",
                    "model_auth": "local (no Vercel link); the Claude Agent SDK reads ANTHROPIC_BASE_URL = proxy, ANTHROPIC_API_KEY = per-role key",
                    "roles": {role: " ".join(args) for role, args in ROLES}},
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    log_dir = run_dir / "deepsec"
    log_dir.mkdir()

    fw = FirewallProxyManager()
    fw.connect()
    minted: list[str] = []
    cid, error, commands = None, None, []
    deadline = time.time() + timeout_s
    events = isolation.ContainerEventLog(run_dir)
    events.start()
    try:
        cid = cg.start_container(config["build_image"], container_name=f"pilot-{run_id}"[:63], workdir="/src")
        meta["container"] = cid
        cg.setup_workspace(cid, data_path, script_path, "e2e", copy_gt_poc=False,
                           scripts_dir=infra.CYBERGYM_SCRIPTS, run_prepare=True)
        cg.copy_to_container(cid, source_archive(), "/opt/deepsec-src.tar.gz")
        cg.exec_run(cid, "mkdir -p /opt/deepsec-patches", verbose=False, check=True)
        for p in patches:
            cg.copy_to_container(cid, p, f"/opt/deepsec-patches/{p.name}")
        cg.copy_to_container(cid, SYSTEM_DIR / "install.sh", "/opt/deepsec-install.sh")
        code, out, err = cg.exec_run(
            cid, "bash /opt/deepsec-install.sh", "Installing deepsec", timeout=1800,
            env={"DEEPSEC_TARGET": target, "DEEPSEC_ID": config["repo_to_patch"],
                 "DEEPSEC_MODEL": model})
        (log_dir / "00-install.log").write_text(out + "\n--- stderr ---\n" + err)
        if code != 0:
            raise RuntimeError(f"deepsec install failed ({code}): {err[-800:]}")
        cg.switch_network(cid, "bridge", fw.network_name)
        events.mark(isolation.SYSTEM_STARTED)  # install-phase networking is over
        meta["install_finished_at"] = time.time()

        state.capture(run_dir, "after-install", _capture_files(cg, cid, workspace),
                      from_role=None, to_role=ROLES[0][0], note="Workspace scaffolded; no model calls yet.")
        cli = f"node {workspace}/node_modules/deepsec/dist/cli.mjs"
        common = ["--headless", "--no-tui", "--output", "jsonl"]
        for i, (role, args) in enumerate(ROLES, 1):
            remaining_time = int(deadline - time.time())
            spent = sum((infra.key_spend(k).get("spend") or 0) for k in minted)
            remaining_budget = round(budget - spent, 4)
            if remaining_time <= 60 or remaining_budget <= 0:
                commands.append({"role": role, "remaining_budget": remaining_budget,
                                 "skipped": "budget exhausted" if remaining_budget <= 0 else "time exhausted"})
                break
            key = infra.mint_key(run_id=run_id, kind=kind, system="deepsec", budget_usd=remaining_budget, role=role)
            minted.append(key)
            argv = args + (common if args[0] == "setup" else []) + [
                "--concurrency", str(CONCURRENCY), "--project-id", config["repo_to_patch"]]
            started = time.time()
            code, out, err = cg.exec_run(
                cid, f"{cli} {shlex.join(argv)}", f"deepsec {role}", timeout=remaining_time,
                workdir=workspace, env={**fw.env_vars(), "ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_API_KEY": key})
            finished = time.time()
            (log_dir / f"{i:02d}-{role}.log").write_text(out + "\n--- stderr ---\n" + err)
            stop = _setup_stop(out) if code != 0 else None
            commands.append({"role": role, "argv": argv, "exit_code": code, "started_at": started,
                             "finished_at": finished, "log": f"deepsec/{i:02d}-{role}.log", "setup_stop": stop})
            nxt = ROLES[i][0] if i < len(ROLES) and code == 0 else None
            state.capture(run_dir, f"after-{role}", _capture_files(cg, cid, workspace),
                          from_role=role, to_role=nxt, ts=finished,
                          note=f"`deepsec {' '.join(argv)}` exited {code}"
                               + (f"; setup stopped: {stop['code']}" if stop else ""))
            if code != 0:
                break
        # Native traces: the Claude Agent SDK's session transcripts.
        meta["native_traces"] = _copy_dir_out(cid, "/root/.claude/projects", run_dir / "native" / "claude-projects")
    except Exception as e:  # recorded, not swallowed silently
        error = repr(e)
    finally:
        if cid:
            cg.cleanup_container(cid)
        events.stop()

    meta["commands"] = commands
    _write_findings(run_dir, data_path / "src.tgz", config["repo_to_patch"])
    # The known patch travels with the run, so the tp_target check never
    # depends on where the run folder is exported (verdicts.py reads it first).
    (run_dir / "ground_truth").mkdir(exist_ok=True)
    shutil.copyfile(script_path / "patch.diff", run_dir / "ground_truth" / "patch.diff")
    outcome = runner._outcome(run_dir, minted, budget, error)
    _, setup_state = _records(run_dir, config["repo_to_patch"])
    (run_dir / "decisions.jsonl").write_text(
        "".join(json.dumps(d) + "\n" for d in deepsec.code_decisions(setup_state)))
    evidence = _scope(run_dir, config["repo_to_patch"])
    meta["scope_evidence"] = evidence
    outcome["outcome"] = deepsec.decide_outcome(outcome["outcome"], commands, evidence, error)
    meta.update(finished_at=time.time(), error=error, outcome=outcome, isolation=events.verdict())
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def _setup_stop(stdout: str) -> dict | None:
    """deepsec's own stop reason, from its jsonl output (a SetupProtocolError)."""
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        err = event.get("error") if isinstance(event, dict) else None
        if isinstance(err, dict) and err.get("code"):
            return {"code": err["code"], "message": err.get("message")}
        if isinstance(event, dict) and event.get("code") and event.get("message"):
            return {"code": event["code"], "message": event["message"]}
    return None


def _records(run_dir: Path, project_id: str) -> tuple[list[dict], dict | None]:
    latest = run_dir / "state" / "latest" / "data" / project_id
    records = [json.loads(p.read_text()) for p in sorted((latest / "files").rglob("*.json"))] \
        if (latest / "files").exists() else []
    setup_state = latest / "setup" / "setup-state.json"
    return records, json.loads(setup_state.read_text()) if setup_state.exists() else None


def _scope(run_dir: Path, project_id: str) -> dict:
    records, setup_state = _records(run_dir, project_id)
    return deepsec.scope_evidence(records, setup_state)


def _write_findings(run_dir: Path, src_tgz: Path, repo: str) -> None:
    """`findings.json` in verdicts.py's format; the exporter adds the checks.
    Functions are named from the task's own source (src.tgz)."""
    records, _ = _records(run_dir, repo)
    with tarfile.open(src_tgz) as tar:
        def source_of(path: str) -> str | None:
            try:
                f = tar.extractfile(f"{repo}/{path}")
            except KeyError:
                return None
            return f.read().decode(errors="replace") if f else None
        normalized = deepsec.normalize_findings(records, source_of)
    (run_dir / "findings.json").write_text(json.dumps({"findings": normalized}, indent=2))

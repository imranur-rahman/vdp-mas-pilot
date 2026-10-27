"""AutoCVE adapter (ticket 10). AutoCVE runs in a runner container on
`cybergym-internal` (D1), without its web UI, Redis or Postgres.

- Install: the runner image (infra/runner/autocve) holds AutoCVE's backend at a
  pinned commit with the patches in `systems/autocve/patches/` (P3), plus
  `aiosqlite` so that it can run on SQLite. Its own sandbox image is built from
  its `docker/sandbox/Dockerfile` at the same commit.
- Run: `autocve_start.py` (the headless start script) creates the project and
  the agent task and runs the pipeline to completion.
- Roles: one proxy key per role through AutoCVE's per-agent model settings
  (`agentConfigs`), all keys in one LiteLLM team whose budget is the run's
  cap (D2, D6).
- State: the start script copies the SQLite database and the workspace's
  changed files at every orchestrator hand-off and at the end; `build_state`
  turns them into `state/manifest.json` with row-level diffs.
- Output: AutoCVE writes PoCs but no patch. Findings come from its
  `agent_findings` rows; PoC-only findings are checked with CyberGym stage 1
  and stack-trace overlap with `patch.diff` (F-1, verdicts.py).
"""

import json
import sqlite3
from pathlib import Path

from .. import state

SYSTEM = "autocve"
CAPTURES = "captures"


# --- state ------------------------------------------------------------------------

def _rows(db: Path) -> dict[str, bytes]:
    """One JSON-lines file per table (empty for an empty table), rows in insertion order."""
    out = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        for t in tables:
            cur = con.execute(f'select * from "{t}" order by rowid')
            cols = [c[0] for c in cur.description]
            lines = [json.dumps(dict(zip(cols, (_plain(v) for v in row))), sort_keys=True, ensure_ascii=False)
                     for row in cur]
            out[f"db/{t}.jsonl"] = "".join(l + "\n" for l in lines).encode()
    finally:
        con.close()
    return out


def _plain(v):
    return v.hex() if isinstance(v, (bytes, bytearray, memoryview)) else v


def build_state(run_dir: Path) -> list[dict]:
    """`state/manifest.json` from the start script's captures (pilot.state format).

    Each capture's files are the database rows (`db/<table>.jsonl`) and the
    workspace (`workspace/<path>`), so each diff shows the rows and files that
    changed since the previous hand-off. Each entry also names its database
    copy (`db`).
    """
    run_dir = Path(run_dir)
    raw = run_dir / CAPTURES
    markers = [json.loads(l) for l in (raw / "captures.jsonl").read_text().splitlines() if l.strip()] \
        if (raw / "captures.jsonl").exists() else []
    workspace: dict[str, bytes] = {}
    db_of = {}
    for m in markers:
        for rel in m.get("removed_files") or []:
            workspace.pop(rel, None)
        for rel in m.get("changed_files") or []:
            workspace[rel] = (raw / m["files_dir"] / rel).read_bytes()
        files = {f"workspace/{k}": v for k, v in workspace.items()}
        if m.get("db"):
            files.update(_rows(raw / m["db"]))
        entry = state.capture(run_dir, m["label"], files, from_role=m.get("from_role"),
                              to_role=m.get("to_role"), ts=m.get("ts"), note=m.get("note"))
        db_of[entry["seq"]] = f"{CAPTURES}/{m['db']}" if m.get("db") else None
    manifest = state.load_manifest(run_dir)
    for e in manifest:
        e["db"] = db_of.get(e["seq"])
    (run_dir / "state" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


# --- output adapter ------------------------------------------------------------------

def _json(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _rejected(row: dict) -> bool:
    return row.get("status") == "false_positive"


def dropped_findings(rows: list[dict]) -> list[dict]:
    """Rows AutoCVE itself marked false positive: kept in the captured state, not findings."""
    return [{"id": r["id"], "title": r.get("title"), "status": r.get("status")} for r in rows if _rejected(r)]


def normalize_findings(rows: list[dict], run_dir: Path) -> list[dict]:
    """One normalized finding per `agent_findings` row AutoCVE did not reject.

    AutoCVE's PoC is text (`poc_code`); it is written verbatim to
    `autocve/findings/<id>/poc.bin` and given to CyberGym as the fuzz input.
    AutoCVE writes no patch.
    """
    out = []
    for r in rows:
        if _rejected(r):
            continue
        meta = _json(r.get("finding_metadata")) or {}
        raw = meta.get("raw_finding") or {} if isinstance(meta, dict) else {}
        verification = _json(r.get("verification_result")) or {}
        fid = f"autocve-{r['id']}"
        poc = None
        if r.get("has_poc") and r.get("poc_code"):
            path = Path("autocve") / "findings" / fid / "poc.bin"
            (run_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (run_dir / path).write_bytes(str(r["poc_code"]).encode())
            poc = str(path)
        out.append({
            "finding_id": fid,
            "system": SYSTEM,
            "location": {"file": r.get("file_path"),
                         "function": r.get("function_name") or raw.get("function_name") or raw.get("function"),
                         "line_start": r.get("line_start"), "line_end": r.get("line_end") or r.get("line_start")},
            "title": r.get("title"),
            "description": "\n\n".join(x for x in (r.get("title"), r.get("description")) if x),
            "vuln_class": r.get("vulnerability_type"),
            "severity": r.get("severity"),
            "system_verdict": {"status": r.get("status"), "report_status": meta.get("report_status"),
                               "verdict": verification.get("verdict") if isinstance(verification, dict) else None},
            "poc_description": r.get("poc_description"),
            "poc": poc,
            "patch": None,
            "stack_trace": None,
            "cybergym": None,  # set by validate() once the PoC has been run
        })
    return out


def record_poc_check(finding: dict, run_dir: Path, task: str, results: dict, log: str, attempt: int = 1) -> None:
    """Record CyberGym's stage-1 result for one finding's PoC (F-1, PoC only).

    Writes `cybergym/<task>/<finding>/summary.json`, the PoC it checked under
    `output/`, and the stage-1 output (the sanitizer report) as the finding's
    stack trace. Each PoC is its own CyberGym attempt, numbered per finding,
    so that the bundle keeps every checked PoC.
    """
    out = run_dir / "cybergym" / task.replace("/", "_") / finding["finding_id"]
    (out / "output").mkdir(parents=True, exist_ok=True)
    if finding.get("poc"):
        (out / "output" / "poc.bin").write_bytes((run_dir / finding["poc"]).read_bytes())
    (out / "stage1.log").write_text(log)
    (out / "summary.json").write_text(json.dumps(
        {"mode": "poc-only", "finding_id": finding["finding_id"],
         "attempts": [{"attempt": attempt, "stage1": results.get("stage1")}]}, indent=2))
    finding["stack_trace"] = str((out / "stage1.log").relative_to(run_dir))
    finding["cybergym"] = {"summary": str((out / "summary.json").relative_to(run_dir)), "attempt": attempt}


def write_findings(run_dir: Path, findings: list[dict]) -> None:
    (Path(run_dir) / "findings.json").write_text(json.dumps({"findings": findings}, indent=2))


# --- install ------------------------------------------------------------------------

UPSTREAM = "https://github.com/larlarua/AutoCVE"
COMMIT = "b26025f"  # HEAD of main on 2026-10-27 (merge of PR #42)
PATCHES_DIR = Path(__file__).resolve().parents[3] / "systems" / SYSTEM / "patches"
RUNNER_DOCKERFILE = Path(__file__).resolve().parents[3] / "infra" / "runner" / SYSTEM / "Dockerfile"
START_SCRIPT = Path(__file__).with_name("autocve_start.py")
RUNNER_IMAGE = f"pilot-runner-autocve:{COMMIT}"
SANDBOX_IMAGE = f"autocve-sandbox:{COMMIT}"
TIKTOKEN_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"


def _sh(*cmd, **kw):
    import subprocess
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def upstream_checkout() -> Path:
    """AutoCVE at the pinned commit with the pilot's patches applied (cached)."""
    from .. import infra
    src = infra.REPO / ".cache" / "autocve" / COMMIT
    if not (src / ".pilot-patched").exists():
        import shutil
        shutil.rmtree(src, ignore_errors=True)
        src.parent.mkdir(parents=True, exist_ok=True)
        _sh("git", "clone", "-q", UPSTREAM, str(src))
        _sh("git", "-C", str(src), "checkout", "-q", COMMIT)
        for p in sorted(PATCHES_DIR.glob("*.patch")):
            _sh("git", "-C", str(src), "apply", str(p))
        (src / ".pilot-patched").write_text("\n".join(p.name for p in sorted(PATCHES_DIR.glob("*.patch"))))
    return src


def runner_context(dest: Path) -> Path:
    """Build context for infra/runner/autocve/Dockerfile."""
    import hashlib
    import shutil
    import httpx
    src = upstream_checkout()
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "autocve").mkdir(parents=True)
    shutil.copytree(src / "backend", dest / "autocve" / "backend",
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", "tests"))
    shutil.copyfile(RUNNER_DOCKERFILE, dest / "Dockerfile")
    shutil.copyfile(START_SCRIPT, dest / "autocve_start.py")
    (dest / "tiktoken").mkdir()
    name = hashlib.sha1(TIKTOKEN_URL.encode()).hexdigest()  # tiktoken's cache file name
    (dest / "tiktoken" / name).write_bytes(httpx.get(TIKTOKEN_URL, timeout=120, follow_redirects=True).content)
    return dest


def ensure_images(log_dir: Path) -> dict:
    """The runner image (registry allowlist, logged) and AutoCVE's sandbox image.

    The sandbox image's Dockerfile downloads Node, Go, Gitleaks, TruffleHog and
    OSV-Scanner from mirrors and GitHub, outside the registry allowlist, so it is
    built beforehand (`docker build docker/sandbox` at the pinned commit) and
    only checked here.
    """
    from .. import isolation, runner
    built = {}
    if not runner._image_exists(RUNNER_IMAGE):
        ctx = runner_context(Path(log_dir) / "runner-context")
        built["runner"] = isolation.build_image(ctx, RUNNER_IMAGE, log_dir)
        import shutil
        shutil.rmtree(ctx, ignore_errors=True)
        if built["runner"]["returncode"] != 0:
            raise RuntimeError(f"runner image build failed: {built['runner']['output_tail'][-800:]}")
    if not runner._image_exists(SANDBOX_IMAGE):
        raise RuntimeError(f"sandbox image {SANDBOX_IMAGE} missing: build AutoCVE's docker/sandbox at {COMMIT}")
    return built


# --- run ------------------------------------------------------------------------------

ROLES = ("orchestrator", "recon", "scan", "triage", "finding", "verification", "analysis", "default")


def finding_rows(db: Path) -> list[dict]:
    if not Path(db).exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("select * from agent_findings order by created_at, rowid")]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def stage1(poc: Path, task: str) -> tuple[dict, str]:
    """CyberGym stage 1 alone (the agent's PoC on the unpatched build), the way
    `run_agent.run_final_validation` runs a stage; returns its result and output."""
    from .. import infra
    infra._import_cybergym()
    import tomli
    from utils import cleanup_container, copy_to_container, exec_run, setup_workspace, start_container
    project, name = task.split("/")
    script_path = infra.CYBERGYM / "projects" / project / name
    data_path = infra.REPO / "data" / "projects" / project / name
    config = tomli.loads((script_path / "../project.toml").read_text())
    config.update(tomli.loads((script_path / "config.toml").read_text()))
    cid, out = None, ""
    result = {"stage1": None}
    try:
        cid = start_container(config["build_image"])
        setup_workspace(cid, data_path, script_path, "e2e", scripts_dir=infra.CYBERGYM_SCRIPTS)
        copy_to_container(cid, poc, "/output/poc.bin")
        cmd = ("/scripts/.venv/bin/python /scripts/validate.py --src-dir /src --config-dir /config --data-dir /data"
               " --json-output /output/validation_results.json --only-stage 1 --poc-file /output/poc.bin --run-prepare")
        code, stdout, stderr = exec_run(cid, cmd, "Running stage 1", timeout=7200, workdir="/")
        out = (stdout or "") + "\n--- stderr ---\n" + (stderr or "")
        _, js, _ = exec_run(cid, "cat /output/validation_results.json", verbose=False)
        result["stage1"] = json.loads(js).get("stage1") if js.strip() else "error"
    except Exception as e:  # recorded with the result
        result["stage1"], out = "error", out + f"\nadapter error: {e!r}"
    finally:
        if cid:
            cleanup_container(cid)
    return result, out


def run(task: str, kind: str, budget_usd: float | None, timeout_s: int) -> Path:
    import os
    import shutil
    import time
    from .. import infra, isolation, runner

    budget = budget_usd if budget_usd is not None else runner.DEFAULT_BUDGET[kind]
    model = runner.MODELS[kind]
    run_id = runner.new_run_id(SYSTEM, task, kind)
    run_dir = infra.RUNS / run_id
    run_dir.mkdir(parents=True)
    run_dir.chmod(0o777)  # the proxy container writes proxy.jsonl here

    gateway = infra.ensure_firewall()
    _ensure_proxy(gateway)
    base_url = f"http://{gateway}:{infra.PROXY_PORT}"
    meta = {
        "run_id": run_id, "system": SYSTEM, "task": task, "kind": kind, "placement": "runner_container",
        "requested_model": model, "budget_usd": budget, "timeout_s": timeout_s,
        "pilot_commit": runner._git_rev(infra.CODE), "cybergym_commit": runner._git_rev(infra.CYBERGYM),
        "system_commit": COMMIT, "system_patches": [p.name for p in sorted(PATCHES_DIR.glob("*.patch"))],
        "runner_image": RUNNER_IMAGE, "sandbox_image": SANDBOX_IMAGE,
        "system_config": "AutoCVE defaults: all agents on (comprehensive audit), finding runtime stack default,"
                         " verification_level sandbox; per-role proxy keys",
        "role_labeling": "per-role key (agentConfigs) + prompt hash",
        "gateway_ip": gateway, "started_at": time.time(),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))

    work = run_dir / "autocve"
    projects_root = work / "projects"
    source = projects_root / "src"
    runner._prepare_source(task, source)
    for d in ("db", "tmp"):
        (work / d).mkdir(parents=True)
    keys = infra.mint_role_keys(run_id=run_id, kind=kind, system=SYSTEM, budget_usd=budget, roles=list(ROLES))
    keys_file = work / "keys.json"
    keys_file.write_text(json.dumps(keys))
    subprocess_chmod(run_dir)

    cmd = ["/app/.venv/bin/python", "/opt/pilot/autocve_start.py", "--run-dir", str(run_dir),
           "--source", str(source), "--projects-root", str(projects_root), "--db", str(work / "db" / "autocve.db"),
           "--keys", str(keys_file), "--base-url", base_url, "--model", model, "--timeout", str(timeout_s)]
    env = {"SANDBOX_IMAGE": SANDBOX_IMAGE, "TMPDIR": str(work / "tmp"), "HOME": str(work / "tmp")}
    error, exit_code = None, None
    with isolation.ContainerEventLog(run_dir) as events:
        try:
            meta["image_builds"] = ensure_images(run_dir)
            events.mark(isolation.SYSTEM_STARTED)
            meta["agent_started_at"] = time.time()
            # The run folder is mounted at its host path, so the bind mounts AutoCVE asks
            # the Docker daemon for (its sandbox containers) name real host paths.
            container = isolation.start_runner_container(
                run_id=run_id, system=SYSTEM, image=RUNNER_IMAGE, command=cmd, gateway_ip=gateway, env=env,
                workdir="/app", user="root",
                volumes={str(run_dir): {"bind": str(run_dir), "mode": "rw"},
                         "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}})
            meta["runner_container"] = container.name
            try:
                exit_code = container.wait(timeout=timeout_s + 900)["StatusCode"]
            except Exception as e:  # timeout or daemon error: recorded, container stopped
                error = repr(e)
                container.stop(timeout=10)
            (work / "runner_stdout.log").write_bytes(container.logs(stdout=True, stderr=False))
            (work / "runner_stderr.log").write_bytes(container.logs(stdout=False, stderr=True))
            container.remove(force=True)
        except Exception as e:  # recorded, not swallowed silently
            error = (error + "; " if error else "") + repr(e)
        meta["agent_finished_at"] = time.time()
    keys_file.unlink(missing_ok=True)
    subprocess_chmod(run_dir)

    findings = []
    try:
        manifest = build_state(run_dir)
        last_db = run_dir / manifest[-1]["db"] if manifest and manifest[-1].get("db") else None
        rows = finding_rows(last_db) if last_db else []
        findings = normalize_findings(rows, run_dir)
        meta["autocve_dropped_findings"] = dropped_findings(rows)
        for n, f in enumerate([f for f in findings if f.get("poc")], 1):
            results, log = stage1(run_dir / f["poc"], task)
            record_poc_check(f, run_dir, task, results, log, attempt=n)
    except Exception as e:
        error = (error + "; " if error else "") + f"output adapter: {e!r}"
    write_findings(run_dir, findings)

    result_path = work / "result.json"
    meta["autocve_result"] = json.loads(result_path.read_text()) if result_path.exists() else None
    outcome = runner._outcome(run_dir, list(keys.values()), budget, error)
    spend = {role: infra.key_spend(k) for role, k in keys.items()}
    meta.update(finished_at=time.time(), error=error, runner_exit_code=exit_code, key_spend=spend,
                spend_usd=round(sum((s.get("spend") or 0) for s in spend.values()), 4),
                isolation=events.verdict())
    meta["outcome"], meta["scope_evidence"] = scope_outcome(outcome, findings, run_dir)
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def subprocess_chmod(path: Path) -> None:
    import subprocess
    subprocess.run(["chmod", "-R", "a+rwX", str(path)], capture_output=True)


def _ensure_proxy(gateway: str) -> None:
    """Use the running proxy stack; start it only if it is down (other runs share it)."""
    import httpx
    from .. import infra
    try:
        if httpx.get(f"http://127.0.0.1:{infra.PROXY_PORT}/health/liveliness", timeout=5).status_code == 200:
            return
    except httpx.HTTPError:
        pass
    infra.ensure_proxy(gateway)


# Read from AutoCVE's source at COMMIT (systems/autocve/NOTES.md).
SOURCE_FACTS = {
    "run_code_languages": ["python", "php", "javascript", "ruby", "go", "java", "bash"],
    "sandbox_allowlist_lacks_compilers": "SandboxTool checks only the first token; `bash -c 'gcc ...'` passes",
    "sandbox_image_toolchain": "build-essential, cmake, clang, llvm (no OSS-Fuzz or sanitizer build of the target)",
    "semgrep_rules": "registry packs (p/default, p/security-audit, ...), unreachable without internet (P1)",
    "poc_format": "text in agent_findings.poc_code, given to CyberGym verbatim as poc.bin",
}


def scope_outcome(outcome: dict, findings: list[dict], run_dir: Path) -> tuple[dict, dict | None]:
    """D8: a run that completed but produced nothing on the C task is `out_of_scope`.

    Budget exhaustion and refusals keep their outcome. The evidence goes into
    run.json (`scope_evidence`) and the rendered trajectory.
    """
    if outcome.get("outcome") != "completed" or findings:
        return outcome, None
    result = Path(run_dir) / "autocve" / "result.json"
    evidence = {
        "findings": 0,
        "model_calls": outcome.get("model_calls"),
        "autocve_result": json.loads(result.read_text()) if result.exists() else None,
        "source_facts": SOURCE_FACTS,
    }
    return {**outcome, "outcome": "out_of_scope", "outcome_from_log": outcome["outcome"]}, evidence

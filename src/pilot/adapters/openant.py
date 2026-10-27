"""OpenAnt adapter (ticket 09). OpenAnt runs on the host in a runner container
on `cybergym-internal` (D1, P7), with its default dynamic testing on.

OpenAnt (github.com/knostic/OpenAnt) is a Go CLI driving a Python 3.11+ core.
Its pipeline has seven phases, each routed through its own (provider, model)
pair in `~/.config/openant/config.json` (`$schema_version: 2`). The pilot
points every phase's provider `base_url` at the LiteLLM proxy and gives each
phase its own per-run key, so the proxy labels each call with the phase as its
role (ADR 0002: a per-role key, never the model name). Env-var overrides were
removed upstream, so routing is the config file alone.

The pipeline is run with `openant scan --verify` (Stage-2 attacker simulation
on; dynamic testing on by default). Dynamic testing builds a per-finding Docker
image and runs the exploit in a container with no internet; for a C/C++ target
(`md4c`) OpenAnt has no Docker template, so every dynamic test is `SKIPPED`
(no image build) — the oracle step records that. Image builds that do happen
go through the logged registry allowlist (`isolation.build_image`).

Fetching modes are off: `init` is local-in-place (no clone, no `--commit`,
no `--pr`), and `openant patch --cve` (the known-CVE remediation that reads a
public advisory) is never invoked. A plain `openant patch` (no `--cve`)
generates a candidate remediation from the pipeline output only.

This module holds the install/run recipe and the pure seam-2 transforms
(phase routing config, finding normalization, dynamic-test oracle steps); the
state collector is `openant_collector.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

SYSTEM = "openant"
UPSTREAM = "https://github.com/knostic/OpenAnt"
COMMIT = "9143061"  # re-checked at HEAD 2026-10-27 (NOTES.md)
RUNNER_IMAGE = "pilot-runner-openant:9143061-p1-r3"  # -pN: N pilot patches; -rN: image recipe revision

# The seven pipeline phases (README "hand-authored config"; each is a role the
# proxy labels by its own key). Order is the pipeline's rough execution order.
PHASES = (
    "app_context",
    "llm_reach",
    "enhance",
    "analyze",
    "verify",
    "dynamic_test",
    "report",
)
CONFIG_NAME = "pilot"  # the llm-config name; selected with `--llm-config pilot`


# --- phase routing / role labeling ------------------------------------------

def build_config(model: str, base_url: str, phase_keys: dict[str, str]) -> dict:
    """OpenAnt's `config.json` (v2) routing every phase to the proxy.

    Each phase gets its own `anthropic`-type provider whose `base_url` is the
    LiteLLM proxy and whose `api_key` is that phase's per-run key, so the proxy
    labels the call with the phase as its role (ADR 0002). Every phase runs the
    one study model; the per-phase recommended defaults are overridden.
    """
    missing = [p for p in PHASES if p not in phase_keys]
    if missing:
        raise ValueError(f"no proxy key for phase(s): {', '.join(missing)}")
    providers = {p: {"type": "anthropic", "api_key": phase_keys[p], "base_url": base_url}
                 for p in PHASES}
    phase_map = {p: {"provider": p, "model": model} for p in PHASES}
    return {
        "$schema_version": 2,
        "default_llm": CONFIG_NAME,
        "llm_providers": providers,
        "llm_configs": {CONFIG_NAME: phase_map},
    }


# --- finding normalization --------------------------------------------------

def normalize_findings(pipeline_output: dict, system: str = SYSTEM,
                       patch_of: dict[str, str] | None = None,
                       poc_of: dict[str, str] | None = None) -> list[dict]:
    """One normalized finding per confirmed finding in `pipeline_output.json`.

    OpenAnt's `findings` are the confirmed vulnerabilities; `false_positives`
    are the candidates it rejected and are not findings (they stay visible in
    the captured state). OpenAnt's `location` carries a file and a function but
    no line numbers. `patch_of` / `poc_of` link a finding's own artifact by id
    when the output adapter has written one; OpenAnt writes neither for a C
    target, so both default to empty.
    """
    patch_of = patch_of or {}
    poc_of = poc_of or {}
    out = []
    for rank, f in enumerate(pipeline_output.get("findings") or [], 1):
        fid = f.get("id") or f"openant-{rank}"
        loc = f.get("location") or {}
        dyn = f.get("dynamic_testing")
        dyn_status = dyn.get("status") if isinstance(dyn, dict) else None
        out.append({
            "finding_id": fid,
            "system": system,
            "rank": rank,
            "location": {"file": loc.get("file"), "function": loc.get("function"),
                         "line_start": None, "line_end": None},
            "title": f.get("name"),
            "description": f.get("description"),
            "vuln_class": f.get("cwe_name"),
            "cwe": f.get("cwe_id"),
            "severity": f.get("severity"),
            "suggested_fix": f.get("suggested_fix"),
            "system_verdict": {"stage1": f.get("stage1_verdict"),
                               "stage2": f.get("stage2_verdict"),
                               "dynamic": dyn_status},
            "poc": poc_of.get(fid),
            "patch": patch_of.get(fid),
            "stack_trace": None,
            "cybergym": None,  # set by validate() once the patch has been checked
        })
    return out


# --- dynamic tests as oracle_run records ------------------------------------

def dynamic_oracle_records(dynamic_results: dict | list | None, ts: float | None = None) -> list[dict]:
    """`oracle_runs.jsonl` records (the collector format the exporter reads),
    one per OpenAnt dynamic-test result; each becomes an `oracle_run` step.

    OpenAnt's own oracle (a per-finding Docker exploit test) is kept distinct
    from CyberGym's four-stage validation: `source` is `openant_dynamic_test`.
    A result with a generated Dockerfile means an image was built (through the
    logged registry allowlist); an empty one, e.g. a C finding with no Docker
    template, means `SKIPPED` with nothing built. Accepts the whole
    `dynamic_test_results.json` object or a bare results list.
    """
    if not dynamic_results:
        return []
    results = dynamic_results if isinstance(dynamic_results, list) else dynamic_results.get("results") or []
    return [{
        "ts": ts,
        "role": "dynamic_test",
        "kind": "openant_dynamic_test",
        "source": "openant_dynamic_test",
        "result": r.get("status"),
        "poc_ref": None,
        "patch_ref": None,
        "note": f"finding {r.get('finding_id')}: {r.get('details') or ''}".strip(),
        "detail": {
            "finding_id": r.get("finding_id"),
            "status": r.get("status"),
            "built_image": bool(r.get("dockerfile")),
            "evidence_types": [e.get("type") for e in (r.get("evidence") or [])],
            "retry_count": r.get("retry_count", 0),
            "elapsed_seconds": r.get("elapsed_seconds"),
        },
    } for r in results]


# --- scan state at hand-offs ------------------------------------------------

def capture_state(run_dir: Path, captures_dir: Path) -> list[dict]:
    """`state/` (pilot.state format) and `handoffs.jsonl` from the collector's
    captures: one hand-off per `<step>.report.json` OpenAnt wrote, plus the end.

    Each capture's files are OpenAnt's scan directory at that point
    (`scan/<path>`) and the target tree's changes (`files.diff`), so each
    state diff shows what the finished phase produced. The phase that just
    finished is the hand-off's `from_role`; the next capture's phase is its
    `to_role`.
    """
    from .. import state
    from ..deepsec import handoff_records
    path = Path(captures_dir) / "handoffs.jsonl"
    records = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    for i, r in enumerate(records):
        cap = Path(captures_dir) / r["state_ref"]
        files = {f"scan/{p.relative_to(cap / 'scan').as_posix()}": p.read_bytes()
                 for p in sorted((cap / "scan").rglob("*")) if p.is_file()} if (cap / "scan").is_dir() else {}
        if (cap / "files.diff").is_file():
            files["target.diff"] = (cap / "files.diff").read_bytes()
        nxt = records[i + 1]["step"] if i + 1 < len(records) else None
        state.capture(run_dir, r["step"], files, from_role=r["step"],
                      to_role=None if nxt == "end" else nxt, ts=r.get("t"),
                      note=f"OpenAnt `{r['step']}` step report written" if r["step"] != "end" else "end of run")
    manifest = state.load_manifest(run_dir)
    out = handoff_records(manifest, [])
    (Path(run_dir) / "handoffs.jsonl").write_text("".join(json.dumps(h) + "\n" for h in out))
    return out


# --- install / run / output (runtime) ---------------------------------------
# These need Docker and the pilot infrastructure; the pure transforms above are
# tested at seam 2, the run itself is accepted by a dev then a data run.

COLLECTOR = Path(__file__).with_name("openant_collector.py")
CODE_ROOT = Path(__file__).resolve().parents[3]   # the code checkout (not PILOT_HOME)
PATCHES = CODE_ROOT / "systems" / "openant" / "patches"
IN = "/pilot"            # adapter files inside the runner container
SCAN_DIR = "/output/scan"
STATE_DIR = "/output/state"


def ensure_image(run_dir: Path) -> dict:
    """Build the OpenAnt runner image if absent (setup, with internet: git +
    Go modules + PyPI are not on the dynamic-test registry allowlist). Done
    before the run's isolation window, so its build containers are not
    attributed to the run. The per-finding dynamic-test image builds that DO
    happen during a run go through `isolation.build_image` (allowlist)."""
    from .. import infra, runner
    if runner._image_exists(RUNNER_IMAGE):
        return {"built": False, "image": RUNNER_IMAGE}
    # The build context: the Dockerfile plus this adapter's patch files (P3).
    context = run_dir / "openant" / "image_context"
    shutil.copytree(CODE_ROOT / "infra" / "runner" / "openant", context, dirs_exist_ok=True)
    shutil.copytree(PATCHES, context / "patches", dirs_exist_ok=True)
    started = time.time()
    r = subprocess.run(["docker", "build", "-t", RUNNER_IMAGE,
                        "--build-arg", f"OPENANT_COMMIT={COMMIT}", str(context)],
                       capture_output=True, text=True)
    (run_dir / "openant").mkdir(parents=True, exist_ok=True)
    (run_dir / "openant" / "image_build.log").write_text(r.stdout + "\n--- stderr ---\n" + r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"OpenAnt image build failed: {r.stderr[-1500:]}")
    return {"built": True, "image": RUNNER_IMAGE, "seconds": round(time.time() - started, 1)}


def scan_argv(repo: str, model: str) -> list[str]:
    """`openant scan` with default dynamic testing and Stage-2 verification on,
    every phase on the pilot llm-config. No fetching flags (no --pr/--diff-base,
    no `patch --cve`)."""
    return ["openant", "scan", f"/src/{repo}", "-l", "c", "--verify",
            "--llm-config", CONFIG_NAME, "-o", SCAN_DIR, "--repo-name", repo, "--workers", "4"]


def run(task: str, kind: str, budget_usd: float | None, timeout_s: int) -> Path:
    from .. import infra, isolation, runner

    budget = budget_usd if budget_usd is not None else runner.DEFAULT_BUDGET[kind]
    model = runner.MODELS[kind]
    run_id = runner.new_run_id(SYSTEM, task, kind)
    run_dir = infra.RUNS / run_id
    run_dir.mkdir(parents=True)
    run_dir.chmod(0o777)  # the proxy container writes proxy.jsonl here
    oa = run_dir / "openant"
    oa.mkdir()

    gateway = infra.ensure_firewall()
    _ensure_proxy(gateway)
    proxy_url = f"http://{gateway}:{infra.PROXY_PORT}"

    # D2/ADR 0002: one key per phase (role), all in one team whose budget is the run cap (D6).
    phase_keys = infra.mint_role_keys(run_id=run_id, kind=kind, system=SYSTEM, budget_usd=budget,
                                      roles=list(PHASES))
    config = build_config(model, proxy_url, phase_keys)
    config_path = oa / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    config_path.chmod(0o600)  # OpenAnt restricts ~/.config/openant/config.json

    project, task_name = task.split("/")
    config_toml = tomllib.loads((infra.CYBERGYM / "projects" / project / "project.toml").read_text())
    repo = config_toml["repo_to_patch"]

    workspace = run_dir / "workspace"
    runner._prepare_source(task, workspace / "src")  # src.tgz unpacked, target .git removed (P2, P6)
    (workspace / "output").mkdir()
    subprocess.run(["chmod", "-R", "a+rwX", str(workspace)], check=True)

    meta = {
        "run_id": run_id, "system": SYSTEM, "task": task, "kind": kind, "placement": "runner_container",
        "requested_model": model, "budget_usd": budget, "timeout_s": timeout_s,
        "pilot_commit": runner._git_rev(infra.REPO), "cybergym_commit": runner._git_rev(infra.CYBERGYM),
        "system_commit": COMMIT, "system_patches": sorted(p.name for p in PATCHES.glob("*.patch")),
        "system_argv": scan_argv(repo, model),
        "role_labeling": "per_phase_proxy_key", "phases": list(PHASES),
        "gateway_ip": gateway, "runner_image": RUNNER_IMAGE, "started_at": time.time(),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))

    meta["image_build"] = ensure_image(run_dir)  # setup, before the isolation window

    env = {
        "OPENANT_PYTHON": "/root/.openant/venv/bin/python",
        "HOME": "/root",
    }
    cmd = ["bash", "-lc",
           f"install -m600 {IN}/config.json /root/.config/openant/config.json && "
           f"python3 {IN}/collector.py --state-dir {STATE_DIR} --repo /src/{repo} "
           f"--scan-dir {SCAN_DIR} --timeout {timeout_s} -- " + " ".join(scan_argv(repo, model))
           + "; rc=$?; chmod -R a+rwX /output; exit $rc"]  # root-written output, readable on the host

    error, exit_code = None, None
    with isolation.ContainerEventLog(run_dir) as events:
        events.mark(isolation.SYSTEM_STARTED)
        container = isolation.start_runner_container(
            run_id=run_id, system=SYSTEM, image=RUNNER_IMAGE, command=cmd, gateway_ip=gateway, env=env,
            workdir="/src",
            volumes={str(workspace / "src"): {"bind": "/src", "mode": "rw"},
                     str(workspace / "output"): {"bind": "/output", "mode": "rw"},
                     str(config_path): {"bind": f"{IN}/config.json", "mode": "ro"},
                     str(COLLECTOR): {"bind": f"{IN}/collector.py", "mode": "ro"}})
        meta["runner_container"] = container.name
        try:
            exit_code = container.wait(timeout=timeout_s + 300)["StatusCode"]
        except Exception as e:  # timeout or daemon error: recorded, container stopped
            error = repr(e)
            container.stop(timeout=10)
        (run_dir / "runner_stdout.log").write_bytes(container.logs(stdout=True, stderr=False))
        (run_dir / "runner_stderr.log").write_bytes(container.logs(stdout=False, stderr=True))
        container.remove(force=True)

    meta.update(finished_at=time.time(), error=error, runner_exit_code=exit_code,
                key_spend={p: infra.key_spend(k) for p, k in phase_keys.items()})
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return finish(run_dir)


def finish(run_dir: Path) -> Path:
    """Post-run steps, re-runnable on a run folder: copy OpenAnt's output out of
    the workspace, run the output adapter, and record outcome and isolation."""
    from .. import isolation, runner
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "run.json").read_text())
    oa, out = run_dir / "openant", run_dir / "workspace" / "output"
    _copy_out(out / "scan", oa / "scan")
    _copy_out(out / "state", oa / "captures")
    error = meta.get("error")
    findings = []
    try:
        findings = collect_outputs(run_dir)
    except Exception as e:
        error = (error + "; " if error else "") + f"output adapter: {e!r}"
    spends = meta.get("key_spend") or {}
    meta.update(finished_at=meta.get("finished_at") or time.time(), error=error, findings=len(findings),
                total_spend_usd=round(sum((s.get("spend") or 0) for s in spends.values()), 4) if spends else None,
                outcome=runner._outcome(run_dir, list(spends), meta["budget_usd"], error),
                isolation=isolation.isolation_verdict(isolation.read_events(run_dir)))
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def collect_outputs(run_dir: Path) -> list[dict]:
    """Output adapter: `findings.json`, `state/` + `handoffs.jsonl`, and
    `oracle_runs.jsonl` (OpenAnt's dynamic tests) in the run folder."""
    run_dir = Path(run_dir)
    oa = run_dir / "openant"
    pipeline = _find_one(oa / "scan", "pipeline_output.json")
    findings = normalize_findings(json.loads(pipeline.read_text())) if pipeline else []
    (run_dir / "findings.json").write_text(json.dumps({"findings": findings}, indent=2))

    capture_state(run_dir, oa / "captures")

    dynamic = _find_one(oa / "scan", "dynamic_test_results.json")
    dyn = json.loads(dynamic.read_text()) if dynamic else None
    ts = dynamic.stat().st_mtime if dynamic else None
    (run_dir / "oracle_runs.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in dynamic_oracle_records(dyn, ts)))
    return findings


def _find_one(root: Path, name: str) -> Path | None:
    if not root.exists():
        return None
    hits = sorted(root.rglob(name))
    return hits[0] if hits else None


def _copy_out(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _ensure_proxy(gateway: str) -> None:
    import httpx
    from .. import infra
    try:
        if httpx.get(f"http://127.0.0.1:{infra.PROXY_PORT}/health/liveliness", timeout=5).status_code == 200:
            return
    except httpx.HTTPError:
        pass
    infra.ensure_proxy(gateway)


if __name__ == "__main__":
    import sys
    print(run(sys.argv[1], sys.argv[2], None, 5400))

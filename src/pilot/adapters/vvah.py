"""Visa VVAH adapter (ticket 05). VVAH runs inside the task container (D1).

- Install: VVAH at a pinned commit into a venv in the task container, during
  CyberGym's install phase (full network), before the container moves to
  `cybergym-internal`. Patches in `systems/visa-vvah/patches/` are applied
  first (P3); there are none so far.
- Run: `vvaharness scan --repo /src/<repo> --remediate` on the packaged
  default profile. The default profile keeps S10 off; `--remediate` turns on
  S10 alone with the profile's own S10 settings, because a patch is VVAH's only
  output that CyberGym can check (F-1). S11 stays off as in the profile. A
  `config.local.yaml` overlay sets every role to the study model (P3(d), P4).
- Roles: VVAH has no per-role URL or key (every role on the Anthropic route
  reads one `sdk:` block), so roles are told apart by stage window (the
  stages run one after another) and the system-prompt hash (D2).
- State: the collector (vvah_collector.py) captures VVAH's state database and
  the target tree's changes at each `[ckpt] saved <step>` and at the end.
- Output: findings.json and S10's case folders are normalized into findings;
  the tree's changes are the patch, validated with CyberGym stages 3-4 (F-1),
  as a whole and per finding.
"""

import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .. import infra, isolation

SYSTEM = "visa-vvah"
UPSTREAM = "https://github.com/visa/visa-vulnerability-agentic-harness"
COMMIT = "0e6f00d2a02eb158d7874e33a006156da7186e49"  # v1.5.1
SYSTEM_DIR = infra.REPO / "systems" / SYSTEM
PATCHES = SYSTEM_DIR / "patches"
COLLECTOR = Path(__file__).with_name("vvah_collector.py")

# Every model role in the packaged default profile (vvaharness/config/profiles/default.yaml).
ROLES = ("autoexclude", "preprocess", "threatmodel", "decompose", "deepdive", "verify", "dedup", "chain", "remediate")
EV_ROLES = ("attacker", "judge", "classify", "mapper")
VALIDATE_ROLES = ("orchestrator", "security_architect", "penetration_tester", "cross_repo_analyzer")
# Stage -> the role that makes its model calls. S0, S1, S3, S5, S7 and S9 make no
# model call in the default profile; the auto-exclude survey runs before S0.
STAGE_ROLES = {"s2": "threatmodel", "s4": "deepdive", "s6": "verify", "s8": "chain", "s10": "remediate"}
OUTPUT_DIRS = ("security-scan", "security-remediation", ".vvaharness")

IN = "/pilot"                      # adapter files inside the task container
VVAH_STATE = f"{IN}/vvah-state"    # $VVAHARNESS_STATE_DIR, outside the target


def overlay(model: str) -> str:
    """config.local.yaml: the study model for every role, on the profile's own route."""
    route = f"{{id: {model}, via: deepagents, provider: anthropic}}"
    lines = ["# Pilot overlay (P3(d), P4): every role on the study model, route unchanged.", "models:"]
    lines += [f"  {r}: {route}" for r in ROLES]
    lines += ["  validate:", f"    orchestrator: {route}"]
    lines += [f"    {r}: {{id: {model}}}" for r in VALIDATE_ROLES[1:]]
    lines += ["  exploit_verification:"] + [f"    {r}: {route}" for r in EV_ROLES]
    return "\n".join(lines) + "\n"


def install_script() -> str:
    patches = sorted(PATCHES.glob("*.patch"))
    apply = "\n".join(f"git -C /opt/vvah-src apply {IN}/patches/{p.name}" for p in patches)
    return f"""
set -e
git clone -q {UPSTREAM} /opt/vvah-src
git -C /opt/vvah-src checkout -q {COMMIT}
{apply}
python3 -m venv /opt/vvah
/opt/vvah/bin/pip install -q --upgrade pip
/opt/vvah/bin/pip install -q /opt/vvah-src
/opt/vvah/bin/vvaharness --version
cp "$(/opt/vvah/bin/python -c 'import vvaharness, pathlib; print(pathlib.Path(vvaharness.__file__).parent / "config/profiles/default.yaml")')" {IN}/vvah/config.yaml
mkdir -p -m 700 {VVAH_STATE}
mkdir -p {IN}/work
"""


def scan_argv(repo_to_patch: str) -> list[str]:
    return ["/opt/vvah/bin/vvaharness", "scan", "--repo", f"/src/{repo_to_patch}",
            "--config", f"{IN}/vvah/config.yaml", "--remediate"]


# --- output adapter ---------------------------------------------------------------

def tree_patch(diff_text: str, repo_to_patch: str) -> str:
    """The `diff -ruN` of /src_backup/<repo> against /src/<repo>, as a git-style a/ b/ patch."""
    out = re.sub(rf"^--- {re.escape(repo_to_patch)}/", "--- a/", diff_text, flags=re.M)
    out = re.sub(rf"^\+\+\+ /src/{re.escape(repo_to_patch)}/", "+++ b/", out, flags=re.M)
    return re.sub(rf"^diff -ruN .* {re.escape(repo_to_patch)}/(\S+) /src/\S+$", r"diff -ruN a/\1 b/\1", out, flags=re.M)


def normalize_findings(report: dict, cases: list[dict]) -> list[dict]:
    """One normalized finding per finding in VVAH's final report (`findings.json`).

    `cases` are S10's case folders ({dir, case, diff}); a finding's patch is the
    diff its case recorded. VVAH writes no PoC. Candidates VVAH dropped are not
    findings (they stay visible in the captured state). VVAH reports no function.
    """
    patch_of = {c["case"].get("case_id"): c.get("diff") for c in cases}
    out = []
    for rank, rf in enumerate(report.get("findings") or [], 1):
        f = rf["finding"]
        finding_id = f.get("case_id") or f"vvah-{rank}"
        out.append({
            "finding_id": finding_id,
            "system": SYSTEM,
            "rank": rank,
            "location": {"file": f["file"], "function": None, "line_start": f["line_start"],
                         "line_end": f.get("line_end") or f["line_start"]},
            "title": f.get("title"),
            "description": f.get("description"),
            "vuln_class": f.get("vuln_class"),
            "cwe": f.get("cwe"),
            "severity": rf.get("severity") or f.get("severity"),
            "system_verdict": {"verdict": f.get("verdict"), "confidence": f.get("verdict_confidence")},
            "poc": None,
            "patch": patch_of.get(finding_id),
            "stack_trace": None,
            "cybergym": None,  # set by validate() once the finding's patch has been checked
        })
    return out


# --- run --------------------------------------------------------------------------

def run(task: str, kind: str, budget_usd: float | None, timeout_s: int) -> Path:
    from .. import runner

    budget = budget_usd if budget_usd is not None else runner.DEFAULT_BUDGET[kind]
    model = runner.MODELS[kind]
    run_id = runner.new_run_id(SYSTEM, task, kind)
    run_dir = infra.RUNS / run_id
    run_dir.mkdir(parents=True)
    run_dir.chmod(0o777)  # the proxy container writes proxy.jsonl here

    gateway = infra.ensure_firewall()
    _ensure_proxy(gateway)
    key = infra.mint_key(run_id=run_id, kind=kind, system=SYSTEM, budget_usd=budget)

    infra._import_cybergym()
    import tomli
    from firewall import FirewallProxyManager
    from utils import cleanup_container, copy_to_container, exec_run, setup_workspace, start_container, switch_network

    project, task_name = task.split("/")
    script_path = infra.CYBERGYM / "projects" / project / task_name
    data_path = infra.REPO / "data" / "projects" / project / task_name
    config = tomli.loads((script_path / "../project.toml").read_text())
    config.update(tomli.loads((script_path / "config.toml").read_text()))
    repo_to_patch, build_image = config["repo_to_patch"], config["build_image"]

    meta = {
        "run_id": run_id, "system": SYSTEM, "task": task, "kind": kind,
        "requested_model": model, "budget_usd": budget, "timeout_s": timeout_s,
        "pilot_commit": runner._git_rev(infra.REPO), "cybergym_commit": runner._git_rev(infra.CYBERGYM),
        "system_commit": COMMIT, "system_patches": [p.name for p in sorted(PATCHES.glob("*.patch"))],
        "system_argv": scan_argv(repo_to_patch), "system_profile": "default (packaged) + --remediate",
        "role_labeling": "stage_window+prompt_hash",
        "gateway_ip": gateway, "started_at": time.time(),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    vvah_dir = run_dir / "vvah"
    vvah_dir.mkdir()
    (vvah_dir / "config.local.yaml").write_text(overlay(model))

    fw = FirewallProxyManager()
    fw.connect()
    cid, error = None, None
    # Container events cover the system's container only; CyberGym's validation
    # containers (the oracle, on the default bridge) start after the log stops.
    events = isolation.ContainerEventLog(run_dir)
    events.start()
    try:
        cid = start_container(build_image, container_name=f"vvah-{uuid.uuid4().hex[:8]}", workdir="/src")
        meta["container_id"] = cid
        setup_workspace(cid, data_path, script_path, "e2e", copy_gt_poc=False,
                        scripts_dir=infra.CYBERGYM_SCRIPTS, run_prepare=True)
        exec_run(cid, f"mkdir -p {IN}/vvah {IN}/patches", check=True)
        for p in sorted(PATCHES.glob("*.patch")):
            copy_to_container(cid, p, f"{IN}/patches/{p.name}")
        code, out, err = exec_run(cid, install_script(), "Installing VVAH", timeout=1800)
        (vvah_dir / "install.log").write_text(out + "\n--- stderr ---\n" + err)
        if code != 0:
            raise RuntimeError(f"VVAH install failed: {err[-1000:]}")
        copy_to_container(cid, vvah_dir / "config.local.yaml", f"{IN}/vvah/config.local.yaml")
        copy_to_container(cid, COLLECTOR, f"{IN}/collector.py")
        # VVAH trusts an overlay only if the invoking user (root) owns it and no one else can write it.
        exec_run(cid, f"chown -R root:root {IN} && chmod -R go-w {IN}", check=True, verbose=False)
        _, freeze, _ = exec_run(cid, "/opt/vvah/bin/pip freeze", verbose=False)
        (vvah_dir / "pip-freeze.txt").write_text(freeze)

        switch_network(cid, "bridge", fw.network_name)  # P1: from here only the proxy
        events.mark(isolation.SYSTEM_STARTED)

        env = {**fw.env_vars(), "ANTHROPIC_API_KEY": key,
               "ANTHROPIC_BASE_URL": f"http://{gateway}:{infra.PROXY_PORT}",
               "VVAHARNESS_STATE_DIR": VVAH_STATE}
        cmd = ["python3", f"{IN}/collector.py", "--state-dir", f"{IN}/state",
               "--repo", f"/src/{repo_to_patch}", "--db", f"{VVAH_STATE}/vvaharness.db",
               "--timeout", str(timeout_s), "--"] + scan_argv(repo_to_patch)
        meta["agent_started_at"] = time.time()
        code, out, err = exec_run(cid, " ".join(cmd), "Running VVAH", timeout=timeout_s + 300,
                                  env=env, workdir=f"{IN}/work")
        meta.update(agent_finished_at=time.time(), system_exit_code=code)
        (vvah_dir / "collector.log").write_text(out + "\n--- stderr ---\n" + err)

        _copy_out(cid, f"{IN}/state/.", run_dir / "state")
        for d in ("security-scan", "security-remediation"):
            _copy_out(cid, f"/src/{repo_to_patch}/{d}/.", vvah_dir / d)
        _copy_out(cid, f"{IN}/work/.", vvah_dir / "work")
        excludes = " ".join(f"-x {d}" for d in OUTPUT_DIRS)
        _, diff, _ = exec_run(cid, f"cd /src_backup && diff -ruN -a {excludes} {repo_to_patch} /src/{repo_to_patch}",
                              verbose=False)
        out_dir = run_dir / "output"
        out_dir.mkdir()
        (out_dir / "fix.patch").write_text(tree_patch(diff, repo_to_patch))
    except Exception as e:  # recorded, not swallowed silently
        error = repr(e)
    finally:
        if cid:
            cleanup_container(cid)
        events.stop()

    findings = []
    try:
        findings = collect_findings(run_dir)
    except Exception as e:
        error = (error + "; " if error else "") + f"output adapter: {e!r}"
    if (run_dir / "output" / "fix.patch").exists():
        validate(run_dir, task, findings, data_path, script_path, build_image)
    (run_dir / "findings.json").write_text(json.dumps({"findings": findings}, indent=2))

    meta.update(finished_at=time.time(), error=error, key_spend=infra.key_spend(key),
                outcome=runner._outcome(run_dir, [key], budget, error), isolation=events.verdict())
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def collect_findings(run_dir: Path) -> list[dict]:
    report_path = run_dir / "vvah" / "security-scan" / "findings.json"
    if not report_path.exists():
        return []
    report = json.loads(report_path.read_text())
    cases = []
    for case_file in sorted((run_dir / "vvah" / "security-remediation").glob("*/finding_case.json")):
        case = json.loads(case_file.read_text())
        diff = case_file.parent / "evidence" / "diff.patch"
        cases.append({"dir": str(case_file.parent.relative_to(run_dir)), "case": case,
                      "diff": str(diff.relative_to(run_dir)) if diff.is_file() and diff.stat().st_size else None})
    return normalize_findings(report, cases)


def validate(run_dir: Path, task: str, findings: list[dict], data_path: Path, script_path: Path,
             build_image: str) -> None:
    """CyberGym stages 3-4 on the whole patch and on each finding's own patch (F-1).

    Each finding with a patch is linked to the summary of its own check.
    """
    infra._import_cybergym()
    import run_agent

    targets = [("combined", run_dir / "output" / "fix.patch")]
    targets += [(f"finding_{f['finding_id']}", run_dir / f["patch"]) for f in findings if f.get("patch")]
    for name, patch in targets:
        out = run_dir / "cybergym" / task.replace("/", "_") / name
        (out / "output").mkdir(parents=True)
        attempt = {"attempt": 1}
        if patch.is_file() and patch.read_text().strip():
            (out / "output" / "fix.patch").write_bytes(patch.read_bytes())
            results = run_agent.run_final_validation(None, out / "output" / "fix.patch", data_path,
                                                     script_path, build_image, "patch-only")
            attempt.update(stage3=results.get("stage3"), stage4=results.get("stage4"))
        else:
            attempt.update(stage3="no_patch", stage4="skipped")
        (out / "summary.json").write_text(json.dumps(
            {"mode": "patch-only", "patch_scope": name, "attempts": [attempt]}, indent=2))
        for f in findings:
            if name == f"finding_{f['finding_id']}":
                f["cybergym"] = {"summary": str((out / "summary.json").relative_to(run_dir)), "attempt": 1}


def _copy_out(cid: str, src: str, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "cp", f"{cid}:{src}", str(dst)], capture_output=True)


def _ensure_proxy(gateway: str) -> None:
    """Use the running proxy stack; start it only if it is down (other runs may share it)."""
    import httpx
    try:
        if httpx.get(f"http://127.0.0.1:{infra.PROXY_PORT}/health/liveliness", timeout=5).status_code == 200:
            return
    except httpx.HTTPError:
        pass
    infra.ensure_proxy(gateway)


if __name__ == "__main__":
    print(run(sys.argv[1], sys.argv[2], None, 5400))

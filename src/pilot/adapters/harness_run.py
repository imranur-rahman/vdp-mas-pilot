"""Anthropic reference harness run adapter (ticket 08, D1).

The harness orchestrator (trusted, runs no model-chosen code) drives the run on
the host; the find/grade/report/patch agents it spawns are gVisor containers on
`cybergym-internal`, so their only exit is the gateway forwarder (LiteLLM) and
CyberGym's run Squid (empty allowlist) — the harness's egress is LiteLLM and
nothing else (P1, P7). `--novelty` is never passed (P3(e)).

Build order matters for the isolation verdict (ticket 03): every image is built
before the `system_started` marker, so the harness itself starts no image build
afterwards. The target image is built `FROM` the compiled task image; the
harness agent image is pre-built `FROM` that target image (plus Node + Claude
Code) through the registry allowlist and tagged where the harness expects it, so
the harness finds it and builds nothing.

The `/work`-only copy question (systems/anthropic-harness/NOTES.md): the harness
default agent image is `gcc:14` + `COPY --from=<target> /work /work`, which drops
the OSS-Fuzz fuzzer (`/out`) and source (`/src`) and would run a clang/Ubuntu
binary on Debian. Resolved by pre-building the agent image `FROM` the task-derived
target image, so the binary (`/out/<fuzzer>`) and source (`/src/<repo>`) survive
and run in their native environment; no `/work` relocation is needed.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .. import infra, isolation, runner
from . import harness

SYSTEM = "anthropic-harness"
UPSTREAM = "https://github.com/anthropics/defending-code-reference-harness"
COMMIT = "d3bea6b"  # HEAD at 2026-10-27; pin recorded in run.json
SYSTEM_DIR = infra.SYSTEMS / SYSTEM
PATCHES = SYSTEM_DIR / "patches"
# Roles that make model calls; each gets its own virtual key so the proxy labels
# finder vs grader vs the rest (D2).
KEY_ROLES = ("recon", "find", "grade", "judge", "report", "patch", "patch-grade")


def _clone_and_install(workdir: Path) -> Path:
    """Clone the harness at the pinned commit, apply the pilot patches (P3), and
    install it into a venv. Returns the venv's python."""
    src = workdir / "harness-src"
    subprocess.run(["git", "clone", "-q", UPSTREAM, str(src)], check=True)
    subprocess.run(["git", "-C", str(src), "checkout", "-q", COMMIT], check=True)
    for patch in sorted(PATCHES.glob("*.patch")):
        subprocess.run(["git", "-C", str(src), "apply", str(patch)], check=True)
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    subprocess.run([uv, "venv", "-q", "-p", "3.12", str(workdir / "venv")], check=True)
    py = workdir / "venv" / "bin" / "python"
    subprocess.run([uv, "pip", "install", "-q", "-p", str(py), "-e", str(src)], check=True)
    return py, src


def _compile_task_image(task: str, run_dir: Path, gateway: str) -> tuple[str, str, str]:
    """Build and commit an image with the task compiled (OSS-Fuzz binary in /out,
    source in /src), using CyberGym's own install phase (full network, pre-marker).
    Returns (image_tag, repo_to_patch, build_image)."""
    infra._import_cybergym()
    import tomli
    from firewall import FirewallProxyManager
    from utils import exec_run, setup_workspace, start_container

    project, task_name = task.split("/")
    script_path = infra.CYBERGYM / "projects" / project / task_name
    data_path = infra.REPO / "data" / "projects" / project / task_name
    config = tomli.loads((script_path / "../project.toml").read_text())
    config.update(tomli.loads((script_path / "config.toml").read_text()))
    repo_to_patch, build_image = config["repo_to_patch"], config["build_image"]

    fw = FirewallProxyManager()
    fw.connect()
    cid = start_container(build_image, container_name=f"harness-build-{uuid.uuid4().hex[:8]}", workdir="/")
    try:
        setup_workspace(cid, data_path, script_path, "e2e", copy_gt_poc=False,
                        scripts_dir=infra.CYBERGYM_SCRIPTS, run_prepare=True)
        # Compile the OSS-Fuzz target (its binary lands in /out).
        code, out, err = exec_run(cid, "bash -eux /src/compile.sh", "Compiling target", timeout=1800)
        (run_dir / "compile.log").write_text(out + "\n--- stderr ---\n" + err)
        if code != 0:
            raise RuntimeError(f"compile.sh failed: {err[-800:]}")
        tag = f"pilot-harness-{task.replace('/', '_')}:compiled"
        subprocess.run(["docker", "commit", cid, tag], check=True, capture_output=True)
    finally:
        from utils import cleanup_container
        cleanup_container(cid)
    return tag, repo_to_patch, build_image


def _fuzzer_name(task: str) -> str:
    infra._import_cybergym()
    import tomli
    project, task_name = task.split("/")
    config = tomli.loads((infra.CYBERGYM / "projects" / project / task_name / "config.toml").read_text())
    return config["target_prog"]


def _write_target_dir(workdir: Path, compiled_tag: str, repo: str, fuzzer: str) -> tuple[Path, str]:
    """A harness target whose image is the compiled task image (binary in /out,
    source in /src). The Dockerfile is a pass-through so the harness's rebuild is
    a cache hit with no network RUN step."""
    target_dir = workdir / "target"
    target_dir.mkdir(parents=True)
    image_tag = f"pilot-harness-{repo}-target:latest"
    (target_dir / "Dockerfile").write_text(f"FROM {compiled_tag}\n")
    (target_dir / "config.yaml").write_text(
        f"image_tag: {image_tag}\n"
        f"github_url: https://github.com/mity/{repo}\n"
        f"commit: 0000000000000000000000000000000000000000\n"
        f"binary_path: /out/{fuzzer}\n"
        f"source_root: /src/{repo}\n"
        f"build_command: bash -eux /src/compile.sh\n"
        f"test_command: bash -eux /src/test.sh\n")
    return target_dir, image_tag


def _prebuild_agent_image(py: str, harness_src: Path, target_image: str, run_dir: Path) -> str:
    """Build the harness agent image FROM the task-derived target image (keeping
    /out, /src and the native toolchain) plus Node + Claude Code, through the
    registry allowlist, and tag it where the harness expects so it builds nothing."""
    version = subprocess.run(
        [str(py), "-c", "from harness.agent_image import CLAUDE_CODE_VERSION; print(CLAUDE_CODE_VERSION)"],
        cwd=harness_src, capture_output=True, text=True, check=True).stdout.strip()
    agent_tag = subprocess.run(
        [str(py), "-c", f"from harness.agent_image import agent_tag; print(agent_tag('{target_image}'))"],
        cwd=harness_src, capture_output=True, text=True, check=True).stdout.strip()
    ctx = run_dir / "agent-image"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(
        f"FROM {target_image}\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm ca-certificates xxd gdb "
        "&& rm -rf /var/lib/apt/lists/* "
        f"&& npm install -g @anthropic-ai/claude-code@{version}\n")
    isolation.build_image(ctx, agent_tag, run_dir, dockerfile=str(ctx / "Dockerfile"))
    return agent_tag


def run(task: str, kind: str, budget_usd: float | None, timeout_s: int) -> Path:
    budget = budget_usd if budget_usd is not None else runner.DEFAULT_BUDGET[kind]
    model = runner.MODELS[kind]
    run_id = runner.new_run_id(SYSTEM, task, kind)
    run_dir = infra.RUNS / run_id
    run_dir.mkdir(parents=True)
    run_dir.chmod(0o777)  # the proxy container writes proxy.jsonl here

    gateway = infra.ensure_firewall()
    infra.ensure_proxy(gateway)
    keys = {role: infra.mint_key(run_id=f"{run_id}/{role}", kind=kind, system=SYSTEM,
                                 budget_usd=budget, role=role) for role in KEY_ROLES}

    meta = {
        "run_id": run_id, "system": SYSTEM, "task": task, "kind": kind,
        "requested_model": model, "budget_usd": budget, "timeout_s": timeout_s,
        "pilot_commit": runner._git_rev(infra.REPO), "cybergym_commit": runner._git_rev(infra.CYBERGYM),
        "system_commit": COMMIT, "system_patches": [p.name for p in sorted(PATCHES.glob("*.patch"))],
        "role_labeling": "per-role proxy key + system-prompt hash", "novelty": False,
        "placement": "orchestrator on host; agents in gVisor on cybergym-internal",
        "gateway_ip": gateway, "started_at": time.time(),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))

    workdir = run_dir / "harness"
    workdir.mkdir()
    batch = None
    error = None
    events = isolation.ContainerEventLog(run_dir)
    events.start()
    try:
        py, harness_src = _clone_and_install(workdir)
        compiled, repo, build_image = _compile_task_image(task, run_dir, gateway)
        fuzzer = _fuzzer_name(task)
        target_dir, target_image = _write_target_dir(workdir, compiled, repo, fuzzer)
        subprocess.run(["docker", "build", "-t", target_image, str(target_dir)], check=True, capture_output=True)
        _prebuild_agent_image(py, harness_src, target_image, run_dir)

        env = {**os.environ,
               "VULN_PIPELINE_AGENT_RUNTIME": "runsc",
               "VULN_PIPELINE_AGENT_NETWORK": isolation.RUN_NETWORK,
               "PILOT_PROXY_URL": f"http://{gateway}:{infra.PROXY_PORT}",
               "PILOT_ROLE_KEYS": json.dumps(keys),
               "ANTHROPIC_API_KEY": keys["find"],  # satisfies the harness auth gate; per-role key overrides
               "VULN_PIPELINE_MODEL": model, "VULN_PIPELINE_NO_TELEMETRY": "1"}

        events.mark(isolation.SYSTEM_STARTED)
        results_root = workdir / "results"
        meta["agent_started_at"] = time.time()
        rc = subprocess.run(
            [str(py), "-m", "harness.cli", "run", str(target_dir), "--model", model,
             "--runs", "1", "--results-dir", str(results_root), "--max-turns", "200"],
            cwd=harness_src, env=env, capture_output=True, text=True, timeout=timeout_s)
        (run_dir / "harness_run.log").write_text(rc.stdout + "\n--- stderr ---\n" + rc.stderr)
        batches = sorted(results_root.glob(f"{Path(target_dir).name}/*")) or sorted(results_root.glob("*/*"))
        batch = batches[-1] if batches else None
        if batch is not None:
            # Patch the confirmed crashes (PoC + patch is what CyberGym validates).
            pr = subprocess.run(
                [str(py), "-m", "harness.cli", "patch", str(batch), "--model", model,
                 "--max-iterations", "3", "--targets-dir", str(target_dir.parent)],
                cwd=harness_src, env=env, capture_output=True, text=True, timeout=timeout_s)
            (run_dir / "harness_patch.log").write_text(pr.stdout + "\n--- stderr ---\n" + pr.stderr)
        meta["agent_finished_at"] = time.time()
    except subprocess.TimeoutExpired as e:
        error = f"harness timed out: {e!r}"
    except Exception as e:  # recorded, not swallowed
        error = repr(e)
    finally:
        events.stop()

    findings = []
    try:
        if batch is not None:
            data = harness.collect(batch, run_dir)
            findings = harness.normalize_findings(data)
    except Exception as e:
        error = (error + "; " if error else "") + f"collector: {e!r}"
    try:
        _validate(run_dir, task, findings)
    except Exception as e:
        error = (error + "; " if error else "") + f"validate: {e!r}"
    (run_dir / "findings.json").write_text(json.dumps({"findings": findings}, indent=2))

    spend = {role: infra.key_spend(k) for role, k in keys.items()}
    meta.update(finished_at=time.time(), error=error, key_spend=spend,
                outcome=runner._outcome(run_dir, list(keys.values()), budget, error),
                isolation=events.verdict())
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def _validate(run_dir: Path, task: str, findings: list[dict]) -> None:
    """CyberGym's four-stage oracle on each finding that has a PoC and a patch,
    and on the best combined output, writing summary.json where the exporter's
    oracle steps and verdicts.build_findings read it (F-1)."""
    infra._import_cybergym()
    import run_agent

    project, task_name = task.split("/")
    data_path = infra.REPO / "data" / "projects" / task
    script_path = infra.CYBERGYM / "projects" / project / task_name
    import tomli
    config = tomli.loads((script_path / "../project.toml").read_text())
    config.update(tomli.loads((script_path / "config.toml").read_text()))
    build_image = config["build_image"]

    for f in findings:
        poc = run_dir / f["poc"] if f.get("poc") else None
        patch = run_dir / f["patch"] if f.get("patch") else None
        mode = "e2e" if poc and poc.is_file() else "patch-only"
        out = run_dir / "cybergym" / task.replace("/", "_") / f["finding_id"].replace(":", "_")
        (out / "output").mkdir(parents=True)
        attempt = {"attempt": 1}
        if (poc and poc.is_file()) or (patch and patch.is_file()):
            if poc and poc.is_file():
                (out / "output" / "poc.bin").write_bytes(poc.read_bytes())
            if patch and patch.is_file():
                (out / "output" / "fix.patch").write_bytes(patch.read_bytes())
            results = run_agent.run_final_validation(
                out / "output" / "poc.bin" if poc and poc.is_file() else None,
                out / "output" / "fix.patch" if patch and patch.is_file() else None,
                data_path, script_path, build_image, mode)
            attempt.update({f"stage{n}": results.get(f"stage{n}") for n in (1, 2, 3, 4)})
        (out / "summary.json").write_text(json.dumps({"mode": mode, "attempts": [attempt]}, indent=2))
        f["cybergym"] = {"summary": str((out / "summary.json").relative_to(run_dir)), "attempt": 1}

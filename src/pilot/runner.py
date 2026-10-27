"""One run: one system on one task, producing a run folder.

Two placements of CyberGym-E2E's built-in `claude-code` agent:

- `claude-code` (ticket 01): in the task container, through the benchmark's
  own runner, used unmodified; only its LiteLLM key minting is replaced so
  that the key carries the run's ID, kind, and budget.
- `claude-code-runner` (ticket 03): in a runner container on
  `cybergym-internal`, the placement host-side systems use (P7).

Every run gets its own budgeted proxy key, a container-event log, an
isolation verdict, and a run outcome in `run.json`.
"""

import json
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
from pathlib import Path

from . import infra, isolation, outcomes

MODELS = {"data": "claude-haiku-5-5", "dev": "claude-haiku-5-5"}  # ADR 0005
DEFAULT_BUDGET = {"data": 20.0, "dev": 2.0}


def _git_rev(path: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True).stdout.strip()


def new_run_id(system: str, task: str, kind: str) -> str:
    return f"{time.strftime('%Y%m%dT%H%M%S')}-{system}-{task.replace('/', '_')}-{kind}"


def run_claude_code(task: str, kind: str, budget_usd: float | None, timeout_s: int) -> Path:
    budget = budget_usd if budget_usd is not None else DEFAULT_BUDGET[kind]
    run_id = new_run_id("claude-code", task, kind)
    run_dir = infra.RUNS / run_id
    run_dir.mkdir(parents=True)
    run_dir.chmod(0o777)  # the proxy container writes proxy.jsonl here

    gateway = infra.ensure_firewall()
    infra.ensure_proxy(gateway)
    env = infra.load_env()

    meta = {
        "run_id": run_id, "system": "claude-code", "task": task, "kind": kind,
        "requested_model": MODELS[kind], "budget_usd": budget, "timeout_s": timeout_s,
        "pilot_commit": _git_rev(infra.REPO), "cybergym_commit": _git_rev(infra.CYBERGYM),
        "gateway_ip": gateway, "started_at": time.time(),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))

    minted: list[str] = []

    def mint(max_budget, key_alias):  # replaces run_agent.litellm_generate_api_key
        key = infra.mint_key(run_id=run_id, kind=kind, system="claude-code", budget_usd=budget)
        minted.append(key)
        return key

    import os
    os.environ.update({
        "LITELLM_BASE_URL": f"http://{gateway}:{infra.PROXY_PORT}",
        "LITELLM_MASTER_KEY": env["LITELLM_MASTER_KEY"],
    })
    infra._import_cybergym()
    import run_agent

    run_agent.litellm_generate_api_key = mint
    argv = [
        "run_agent.py", task, "--agent", "claude-code", "--mode", "e2e",
        "--timeout", str(timeout_s),
        "--model-provider", "litellm", "--litellm-model-id", MODELS[kind],
        "--data-dir", str(infra.REPO / "data" / "projects"),
        "--script-dir", str(infra.CYBERGYM / "projects"),
        "--agent-output", str(run_dir / "cybergym"),
    ]
    events = isolation.ContainerEventLog(run_dir)
    execute = run_agent._execute_claude_code

    def execute_marked(*a, **kw):  # the system starts here; install-phase networking is over
        events.mark(isolation.SYSTEM_STARTED)
        return execute(*a, **kw)

    run_agent._execute_claude_code = execute_marked
    old_argv, sys.argv = sys.argv, argv
    error = None
    events.start()
    try:
        run_agent.main()
    except SystemExit as e:
        meta["cybergym_exit_code"] = e.code  # 1 means the task failed, not the harness
    except Exception as e:  # recorded, not swallowed silently
        error = repr(e)
    finally:
        sys.argv = old_argv
        run_agent._execute_claude_code = execute
        events.stop()

    meta.update(finished_at=time.time(), error=error, outcome=_outcome(run_dir, minted, budget, error),
                isolation=events.verdict())
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


RUNNER_IMAGES = {"claude-code-runner": ("pilot-runner-claude-code:2.1.91", infra.REPO / "infra" / "runner" / "claude-code")}


def _prepare_source(task: str, dest: Path) -> str:
    """The task's source as CyberGym gives it: src.tgz unpacked, the target repo's .git removed (P2, P6)."""
    project = task.split("/")[0]
    config = tomllib.loads((infra.CYBERGYM / "projects" / project / "project.toml").read_text())
    repo = config["repo_to_patch"]
    dest.mkdir(parents=True)
    with tarfile.open(infra.REPO / "data" / "projects" / task / "src.tgz") as tar:
        tar.extractall(dest, filter="data")
    shutil.rmtree(dest / repo / ".git", ignore_errors=True)
    return repo


def run_claude_code_in_runner(task: str, kind: str, budget_usd: float | None, timeout_s: int) -> Path:
    """CyberGym's claude-code agent in a runner container on `cybergym-internal` (P7).

    The agent gets CyberGym's no-test e2e prompt (the runner image has no
    build toolchain) and writes /output/poc.bin and /output/fix.patch.
    """
    system = "claude-code-runner"
    budget = budget_usd if budget_usd is not None else DEFAULT_BUDGET[kind]
    run_id = new_run_id(system, task, kind)
    run_dir = infra.RUNS / run_id
    run_dir.mkdir(parents=True)
    run_dir.chmod(0o777)  # the proxy container writes proxy.jsonl here

    gateway = infra.ensure_firewall()
    infra.ensure_proxy(gateway)
    image, context = RUNNER_IMAGES[system]
    meta = {
        "run_id": run_id, "system": system, "task": task, "kind": kind, "placement": "runner_container",
        "requested_model": MODELS[kind], "budget_usd": budget, "timeout_s": timeout_s,
        "pilot_commit": _git_rev(infra.REPO), "cybergym_commit": _git_rev(infra.CYBERGYM),
        "gateway_ip": gateway, "runner_image": image, "started_at": time.time(),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))

    workspace = run_dir / "workspace"
    repo = _prepare_source(task, workspace / "src")
    (workspace / "output").mkdir()
    subprocess.run(["chmod", "-R", "a+rwX", str(workspace)], check=True)  # the agent user writes here
    key = infra.mint_key(run_id=run_id, kind=kind, system=system, budget_usd=budget)

    infra._import_cybergym()
    from run_agent import get_prompt_no_test

    prompt = get_prompt_no_test("e2e", f"/src/{repo}")
    cmd = ["claude", "-p", prompt, "--disallowedTools",
           "WebFetch,WebSearch,Task,MCPSearch,NotebookEdit,Skill,AskUserQuestion",
           "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions"]
    env = {
        "ANTHROPIC_BASE_URL": f"http://{gateway}:{infra.PROXY_PORT}",
        "ANTHROPIC_API_KEY": key, "ANTHROPIC_MODEL": MODELS[kind],
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
    }
    error, exit_code = None, None
    with isolation.ContainerEventLog(run_dir) as events:
        if not _image_exists(image):  # built through the logged registry allowlist
            isolation.build_image(context, image, run_dir)
        events.mark(isolation.SYSTEM_STARTED)
        container = isolation.start_runner_container(
            run_id=run_id, system=system, image=image, command=cmd, gateway_ip=gateway, env=env,
            workdir="/src", volumes={str(workspace / "src"): {"bind": "/src", "mode": "rw"},
                                     str(workspace / "output"): {"bind": "/output", "mode": "rw"}})
        meta["runner_container"] = container.name
        try:
            exit_code = container.wait(timeout=timeout_s)["StatusCode"]
        except Exception as e:  # timeout or daemon error: recorded, container stopped
            error = repr(e)
            container.stop(timeout=10)
        (run_dir / "claude_code.jsonl").write_bytes(container.logs(stdout=True, stderr=False))
        (run_dir / "runner_stderr.log").write_bytes(container.logs(stdout=False, stderr=True))
        container.remove(force=True)
    for name in ("poc.bin", "fix.patch"):
        if (workspace / "output" / name).exists():
            shutil.copy(workspace / "output" / name, run_dir / name)

    meta.update(finished_at=time.time(), error=error, runner_exit_code=exit_code,
                key_spend=infra.key_spend(key), outcome=_outcome(run_dir, [key], budget, error),
                isolation=events.verdict())
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0


def _outcome(run_dir: Path, minted: list[str], budget: float, error: str | None) -> dict:
    """Run outcome from the proxy log: refusals and budget exhaustion are not system failures."""
    return {**outcomes.run_outcome(run_dir), "harness_error": error, "keys_minted": len(minted)}

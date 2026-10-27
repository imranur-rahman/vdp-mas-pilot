"""One run: one system on one task, producing a run folder.

Ticket 01 supports CyberGym-E2E's built-in `claude-code` agent only. The
benchmark's runner is used unmodified; only its LiteLLM key minting is
replaced so that the key carries the run's ID, kind, and budget.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

from . import infra

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
    old_argv, sys.argv = sys.argv, argv
    error = None
    try:
        run_agent.main()
    except SystemExit as e:
        meta["cybergym_exit_code"] = e.code  # 1 means the task failed, not the harness
    except Exception as e:  # recorded, not swallowed silently
        error = repr(e)
    finally:
        sys.argv = old_argv

    meta.update(finished_at=time.time(), error=error, outcome=_outcome(run_dir, minted, budget, error))
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def stream_stops(records: list[dict]) -> list[dict]:
    """stop_reason and stop_details of each streamed call, from the raw events.

    LiteLLM's reassembled response does not carry Anthropic's stop details.
    """
    stops = []
    for r in records:
        if r.get("record") != "stream":
            continue
        for event in r.get("events") or []:
            for line in str(event).split("\n"):
                if not line.startswith("data:"):
                    continue
                try:
                    d = json.loads(line[5:])
                except ValueError:
                    continue
                if d.get("type") == "message_delta":
                    delta = d.get("delta") or {}
                    stops.append({"stop_reason": delta.get("stop_reason"),
                                  "stop_details": delta.get("stop_details"),
                                  "cost": (d.get("usage") or {}).get("cost")})
    return stops


def _outcome(run_dir: Path, minted: list[str], budget: float, error: str | None) -> dict:
    """Run outcome from the proxy log: refusals and budget exhaustion are not system failures."""
    log = run_dir / "proxy.jsonl"
    records = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    calls = [r for r in records if r.get("record") == "call"]
    stops = stream_stops(records)
    refusals = [s["stop_details"] for s in stops if s["stop_reason"] == "refusal"]
    budget_hit = any("budget" in (r.get("exception") or "").lower() for r in calls)
    if stops and stops[-1]["stop_reason"] == "refusal":
        outcome = "refused"
    elif budget_hit:
        outcome = "budget_exhausted"
    else:
        outcome = "completed"
    return {
        "outcome": outcome, "model_calls": len(calls), "refusals": refusals,
        "cost_usd_openrouter": round(sum(s["cost"] or 0 for s in stops), 4),
        "cost_usd_estimate": round(sum((r.get("standard_logging_object") or {}).get("response_cost") or 0
                                       for r in calls), 4),
        "harness_error": error, "keys_minted": len(minted),
    }

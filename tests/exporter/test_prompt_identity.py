"""Seam 2: the prompt a model step ran under. Claude Code and the Agent SDK put
a per-request `x-anthropic-billing-header:` block in the system prompt; it is
not part of the prompt, so it must not split one agent into many."""

import json
import time

from pilot.exporter import export


def _call(cid: str, cch: str, logged_hash: str, messages: list, t0: float) -> dict:
    system = [{"type": "text", "text": f"x-anthropic-billing-header: cc_version=2.1.91.1cd; cch={cch};"},
              {"type": "text", "text": "You are a Claude agent."}]
    return {
        "record": "call", "litellm_call_id": cid, "status": "success",
        "start_time": t0, "end_time": t0 + 1.0,
        "pilot": {"pilot_run_id": "R", "pilot_run_kind": "dev", "pilot_system": "claude-code",
                  "pilot_prompt_hash": logged_hash, "pilot_dropped_params": []},
        "upstream_request": {"model": "claude-haiku-5-5", "system": system, "messages": messages},
        "response": {"model": "claude-haiku-5-5", "stop_reason": "end_turn", "usage": {},
                     "content": [{"type": "text", "text": "ok"}]},
    }


def test_calls_that_differ_only_in_the_billing_header_share_a_prompt_and_an_agent(tmp_path):
    run_dir = tmp_path / "R"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "R", "system": "claude-code", "task": "md4c/arvo_31332", "kind": "dev",
        "outcome": {"outcome": "completed"}}))
    first = [{"role": "user", "content": "find a bug"}]
    second = first + [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "go on"}]
    t = time.time()
    records = [_call("c1", "11111", "aaaa", first, t), _call("c2", "22222", "bbbb", second, t + 5)]
    (run_dir / "proxy.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))

    bundle = export(run_dir)

    s1, s2 = [s for s in map(json.loads, (bundle / "steps.jsonl").read_text().splitlines())
              if s["step_type"] == "llm_step"]
    assert s1["prompt_id"] == s2["prompt_id"]
    assert s1["agent_instance_id"] == s2["agent_instance_id"]
    assert [m["role"] for m in s2["input_delta"]] == ["assistant", "user"]

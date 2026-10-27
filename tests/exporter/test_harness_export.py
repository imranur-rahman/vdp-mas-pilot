"""Seam 2 for the harness: a run folder with the harness collector's output
(handoffs.jsonl, oracle_runs.jsonl, per-role proxy calls, transcripts) goes into
the exporter, and the bundle carries finder/grader-labeled model steps, hand-off
steps, and the grader re-runs as oracle_run steps. No model calls, no Phoenix.
"""

import json
import time
from pathlib import Path

import pytest

from pilot.exporter import export


def _sse(events: list[dict]) -> list[str]:
    return [f"event: {e['type']}\ndata: {json.dumps(e)}" for e in events]


def _call(cid: str, role: str, prompt_hash: str, user_text: str, out_text: str,
          t0: float, system="you are an agent") -> tuple[dict, dict]:
    call = {
        "record": "call", "litellm_call_id": cid, "status": "success",
        "start_time": t0, "end_time": t0 + 1.0,
        "pilot": {"pilot_run_id": "R", "pilot_run_kind": "dev", "pilot_system": "anthropic-harness",
                  "pilot_role": role, "pilot_prompt_hash": prompt_hash, "pilot_dropped_params": []},
        "upstream_request": {"model": "claude-haiku-5-5", "system": system,
                             "messages": [{"role": "user", "content": user_text}]},
        "response": None,
    }
    stream = {"record": "stream", "litellm_call_id": cid, "events": _sse([
        {"type": "message_start", "message": {"model": "anthropic/claude-haiku-5.5",
                                              "usage": {"input_tokens": 10, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": out_text}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}},
    ])}
    return call, stream


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("run") / "R"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": "R", "system": "anthropic-harness", "task": "md4c/arvo_31332",
        "kind": "dev", "requested_model": "claude-haiku-5-5",
        "outcome": {"outcome": "completed", "model_calls": 2, "refusals": [], "cost_usd_openrouter": 0.01}}))

    t = time.time()
    records = []
    for call, stream in [
        _call("c1", "find", "aaaa1111", "find a bug in /src/md4c", "I crafted a crashing input.", t),
        _call("c2", "grade", "bbbb2222", "verify this PoC", "Reproduced 3/3. PASS.", t + 10),
    ]:
        records += [call, stream]
    (run_dir / "proxy.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))

    # Hand-off: PoC crosses find -> grade (between the two calls).
    (run_dir / "harness" / "run_000").mkdir(parents=True)
    (run_dir / "harness" / "run_000" / "poc.bin").write_bytes(b"\x00poc")
    (run_dir / "harness" / "handoffs" / "01-poc").mkdir(parents=True)
    (run_dir / "harness" / "handoffs" / "01-poc" / "poc.bin").write_bytes(b"\x00poc")
    (run_dir / "handoffs.jsonl").write_text(json.dumps({
        "seq": 1, "label": "poc", "from_role": "find", "to_role": "grade", "ts": t + 5,
        "note": "PoC bytes to the grader",
        "files": [{"name": "poc.bin", "path": "harness/handoffs/01-poc/poc.bin"}]}) + "\n")

    # Oracle: the grader re-ran the PoC.
    (run_dir / "oracle_runs.jsonl").write_text(json.dumps({
        "seq": 1, "role": "grade", "kind": "poc_reproduction", "ts": t + 11,
        "result": {"passed": True, "score": 8.0}, "poc_ref": "harness/run_000/poc.bin",
        "patch_ref": None, "note": "grader re-ran the PoC in a fresh container"}) + "\n")

    # Native traces: one Claude Code transcript per agent.
    for role, sess, ts0 in [("find", "find-sess", t), ("grade", "grade-sess", t + 10)]:
        rel = f"harness/transcripts/{role}.jsonl"
        (run_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / rel).write_text(json.dumps(
            {"type": "system", "subtype": "init", "session_id": sess}) + "\n")
    (run_dir / "transcripts.jsonl").write_text(
        json.dumps({"role": "find", "run_idx": 0, "session_id": "find-sess",
                    "path": "harness/transcripts/find.jsonl", "ts": t, "turns": 1}) + "\n"
        + json.dumps({"role": "grade", "run_idx": 0, "session_id": "grade-sess",
                      "path": "harness/transcripts/grade.jsonl", "ts": t + 10, "turns": 1}) + "\n")

    return export(run_dir)


def steps(bundle: Path) -> list[dict]:
    return [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]


def test_finder_and_grader_model_calls_are_labeled_by_role(bundle):
    by_role = {s["role"]: s for s in steps(bundle) if s["step_type"] == "llm_step"}
    assert "find" in by_role and "grade" in by_role
    assert by_role["find"]["agent_instance_id"] != by_role["grade"]["agent_instance_id"]


def test_the_poc_handoff_is_a_handoff_step_between_the_two_agents(bundle):
    ss = steps(bundle)
    handoff = next(s for s in ss if s["step_type"] == "handoff")
    assert handoff["state_ref"]["from_role"] == "find"
    assert handoff["state_ref"]["to_role"] == "grade"
    # it sits after the find call and before the grade call (joined by time)
    find_seq = next(s["seq"] for s in ss if s["role"] == "find")
    grade_seq = next(s["seq"] for s in ss if s["role"] == "grade")
    assert find_seq < handoff["seq"] < grade_seq


def test_the_grader_rerun_is_an_oracle_run_step(bundle):
    oracle = [s for s in steps(bundle) if s["step_type"] == "oracle_run"]
    grade = next(s for s in oracle if (s["oracle_ref"] or {}).get("source") == "harness_grader")
    assert grade["oracle_ref"]["kind"] == "poc_reproduction"
    assert grade["output"]["passed"] is True


def test_handoff_files_are_copied_into_the_bundle(bundle):
    handoff = next(s for s in steps(bundle) if s["step_type"] == "handoff")
    ref = handoff["state_ref"]
    assert (bundle / ref["files"][0]["path"]).read_bytes() == b"\x00poc"


def test_model_steps_are_joined_to_their_native_transcript(bundle):
    by_role = {s["role"]: s for s in steps(bundle) if s["step_type"] == "llm_step"}
    assert by_role["find"]["agent_instance_id"].startswith("find")
    assert by_role["grade"]["agent_instance_id"].startswith("grade")
    # the session id ties the step to a specific agent instance
    assert "find-sess" in by_role["find"]["agent_instance_id"]
    assert "grade-sess" in by_role["grade"]["agent_instance_id"]


def test_the_bundle_records_the_native_transcripts(bundle):
    run = json.loads((bundle / "run.json").read_text())
    traces = {t["role"]: t for t in run["native_traces"]}
    assert set(traces) == {"find", "grade"}
    assert (bundle / traces["find"]["path"]).exists()

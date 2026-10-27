"""Run outcomes from a run folder's proxy log plus what the adapter saw
(M-2, D6, `adapter_error` 2026-10-27). No proxy: the log is written by hand."""

import json

from pilot.outcomes import run_outcome


def _write(run_dir, records):
    run_dir.mkdir(exist_ok=True)
    (run_dir / "proxy.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    return run_dir


def _call(cid, t, cost=None):
    usage = {"input_tokens": 10, "output_tokens": 5, **({"cost": cost} if cost is not None else {})}
    return {"record": "call", "litellm_call_id": cid, "start_time": t,
            "response": {"stop_reason": "end_turn", "usage": usage},
            "standard_logging_object": {"response_cost": 0.01}}


def test_a_run_whose_adapter_raised_is_an_adapter_error(tmp_path):
    run_dir = _write(tmp_path / "r", [_call("c1", "1")])
    out = run_outcome(run_dir, error="RuntimeError('agent image build failed')")
    assert out["outcome"] == "adapter_error"


def test_a_run_with_no_model_calls_is_an_adapter_error(tmp_path):
    run_dir = _write(tmp_path / "r", [])
    out = run_outcome(run_dir, exit_code=2)
    assert out["outcome"] == "adapter_error"
    assert out["system_exit_code"] == 2


def test_a_system_that_ran_and_exited_nonzero_still_completed(tmp_path):
    # The system's own failure is data to code, not an adapter problem.
    run_dir = _write(tmp_path / "r", [_call("c1", "1")])
    assert run_outcome(run_dir, exit_code=1)["outcome"] == "completed"


def test_a_run_stopped_by_its_time_limit_completed(tmp_path):
    run_dir = _write(tmp_path / "r", [_call("c1", "1")])
    out = run_outcome(run_dir, error="harness timed out: TimeoutExpired(['python'], 7200)")
    assert out["outcome"] == "completed"


def test_budget_exhaustion_wins_over_an_adapter_error(tmp_path):
    run_dir = _write(tmp_path / "r", [{"record": "budget_exceeded", "pilot": {}}])
    assert run_outcome(run_dir, error="RuntimeError('crashed on the 400')")["outcome"] == "budget_exhausted"


def test_a_refusal_that_ended_the_run_wins_over_an_adapter_error(tmp_path):
    run_dir = _write(tmp_path / "r", [_call("c1", "1"), {"record": "refusal", "litellm_call_id": "c1",
                                                          "category": "cyber", "explanation": "x"}])
    assert run_outcome(run_dir, error="RuntimeError('no output')")["outcome"] == "refused"


def test_openrouter_cost_of_non_streamed_calls_is_counted(tmp_path):
    run_dir = _write(tmp_path / "r", [_call("c1", "1", cost=0.002), _call("c2", "2", cost=0.003)])
    assert run_outcome(run_dir)["cost_usd_openrouter"] == 0.005

"""Seam 2: a recorded run folder goes in, a trajectory bundle comes out.

Fixtures are the two real run folders from ticket 01 (tests/fixtures/runs).
No model calls, no Phoenix, no network.
"""

import json
import shutil
from pathlib import Path

import pytest

from pilot.exporter import STEP_FIELDS, export, load_proxy_log

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runs"
DEV = "20261026T235842-claude-code-md4c_arvo_31332-dev"
DATA = "20261027T001135-claude-code-md4c_arvo_31332-data"


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    out = {}
    for name in (DEV, DATA):
        run_dir = tmp_path_factory.mktemp("run") / name
        shutil.copytree(FIXTURES / name, run_dir)
        out[name] = export(run_dir)
    return out


def steps(bundle: Path) -> list[dict]:
    return [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]


def llm_steps(bundle):
    return [s for s in steps(bundle) if s["step_type"] in ("llm_step", "refusal")]


@pytest.mark.parametrize("name", [DEV, DATA])
def test_every_step_has_every_step_record_field(bundles, name):
    for s in steps(bundles[name]):
        assert set(STEP_FIELDS) <= set(s), set(STEP_FIELDS) - set(s)


def test_steps_are_ordered_and_uniquely_identified(bundles):
    ss = steps(bundles[DEV])
    assert [s["seq"] for s in ss] == list(range(1, len(ss) + 1))
    assert len({s["step_id"] for s in ss}) == len(ss)
    assert sum(s["step_type"] == "llm_step" for s in ss) == 48
    assert sum(s["step_type"] == "oracle_run" for s in ss) == 4


def test_each_call_stores_only_its_new_input(bundles):
    ls = llm_steps(bundles[DEV])
    assert len(ls[0]["input_delta"]) == 1 and ls[0]["input_prefix_hash"] is None
    assert all(len(s["input_delta"]) == 2 for s in ls[1:])
    assert all(s["input_prefix_hash"] for s in ls[1:])
    assert all(s["parent_step_id"] == prev["step_id"] for prev, s in zip(ls, ls[1:]))


def test_input_deltas_rebuild_the_full_conversation(bundles):
    ls = llm_steps(bundles[DEV])
    rebuilt = [m for s in ls for m in s["input_delta"]]
    last_call = [r for r in load_proxy_log(FIXTURES / DEV) if r["record"] == "call"][-1]
    assert rebuilt == last_call["upstream_request"]["messages"]


def test_thinking_summaries_are_kept_apart_from_output_and_labeled(bundles):
    with_summary = [s for s in llm_steps(bundles[DATA]) if s["thinking_summary"]]
    assert with_summary
    for s in with_summary:
        assert s["thinking_summary"]["kind"] == "model_written_summary"
        assert s["thinking_summary"]["text"] not in (s["output"] or "")


def test_tool_calls_carry_their_results_from_the_next_call(bundles):
    ls = llm_steps(bundles[DEV])
    calls = [tc for s in ls for tc in s["tool_calls"]]
    assert len(calls) == 49
    answered = [tc for tc in calls if tc["result"] is not None]
    assert len(answered) >= 48
    assert all(tc["name"] and isinstance(tc["args"], dict) and tc["id"] for tc in calls)


def test_refused_calls_are_refusal_steps_and_the_run_is_refused(bundles):
    ls = llm_steps(bundles[DATA])
    assert len(ls) == 6
    assert all(s["step_type"] == "refusal" and s["refusal_category"] == "cyber" for s in ls)
    assert json.loads((bundles[DATA] / "run.json").read_text())["outcome"]["outcome"] == "refused"


def test_validation_stages_are_oracle_steps_linked_to_what_they_checked(bundles):
    oracle = [s for s in steps(bundles[DEV]) if s["step_type"] == "oracle_run"]
    assert [s["output"] for s in oracle] == ["passed", "passed", "passed", "failed"]
    assert all(s["oracle_ref"]["source"] == "cybergym" for s in oracle)
    poc, patch = oracle[0]["oracle_ref"]["poc"], oracle[0]["oracle_ref"]["patch"]
    assert (bundles[DEV] / poc).read_bytes().endswith(b"[a](b")
    assert (bundles[DEV] / patch).read_text().startswith("--- a/src/md4c.c")
    refused = [s for s in steps(bundles[DATA]) if s["step_type"] == "oracle_run"]
    assert [s["output"] for s in refused] == ["no_poc", "skipped", "skipped", "skipped"]
    assert refused[0]["oracle_ref"]["poc"] is None


def test_step_costs_add_up_to_the_runs_actual_cost(bundles):
    run = json.loads((bundles[DEV] / "run.json").read_text())
    total = sum(s["cost_usd"] or 0 for s in steps(bundles[DEV]))
    assert total == pytest.approx(run["outcome"]["cost_usd_openrouter"], abs=1e-4)


@pytest.mark.parametrize("name", [DEV, DATA])
def test_trajectory_renders_every_step_for_a_reader(bundles, name):
    md = (bundles[name] / "trajectory.md").read_text()
    for s in steps(bundles[name]):
        assert f"Step {s['seq']} ·" in md
    assert "Outcome:" in md


def test_rendered_thinking_is_labeled_as_a_summary(bundles):
    assert "Thinking (model-written summary" in (bundles[DATA] / "trajectory.md").read_text()

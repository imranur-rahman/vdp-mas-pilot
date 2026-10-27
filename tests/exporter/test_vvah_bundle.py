"""Seam 2 for a system without per-role keys: VVAH's data run (ticket 05) goes in,
a bundle with hand-offs, code decisions, stage-window roles and state comes out.

Fixture: tests/fixtures/runs/<VVAH>, a real Claude Haiku 5.5 data run of VVAH
1.5.1 on md4c/arvo_31332 (22 non-streamed calls, no finding, no patch).
"""

import json
import shutil
from pathlib import Path

import pytest

from pilot.exporter import STEP_FIELDS, export, load_proxy_log, normalize_messages

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runs"
VVAH = "20261027T081247-visa-vvah-md4c_arvo_31332-data"


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("run") / VVAH
    shutil.copytree(FIXTURES / VVAH, run_dir)
    return export(run_dir)


def steps(bundle):
    return [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]


def calls(bundle):
    return [s for s in steps(bundle) if s["step_type"] == "llm_step"]


def test_every_step_has_every_step_record_field(bundle):
    for s in steps(bundle):
        assert set(STEP_FIELDS) <= set(s), set(STEP_FIELDS) - set(s)


def test_every_hand_off_and_code_decision_becomes_a_step_with_its_captured_state(bundle):
    hs = [s for s in steps(bundle) if s["step_type"] in ("handoff", "code_decision")]
    kinds = {(s["state_ref"]["stage_label"], s["step_type"]) for s in hs}
    assert {("s2", "handoff"), ("s4", "handoff"), ("s5", "code_decision"), ("s7", "code_decision"),
            ("end", "handoff")} <= kinds
    for s in hs:
        assert (bundle / s["state_ref"]["files_diff"]).is_file()
        assert (bundle / s["state_ref"]["db"]).is_file()
    s4 = next(s for s in hs if s["state_ref"]["stage_label"] == "s4")
    assert s4["role"] == "deepdive" and s4["output"].startswith("outcome=completed")


def test_hand_offs_sit_between_the_calls_of_the_stages_they_join(bundle):
    ss = steps(bundle)
    s2 = next(i for i, s in enumerate(ss) if (s["state_ref"] or {}).get("stage_label") == "s2")
    assert all(s["role"] in ("preflight", "autoexclude", "threatmodel")
               for s in ss[:s2] if s["step_type"] == "llm_step")
    assert all(s["role"] not in ("preflight", "autoexclude", "threatmodel")
               for s in ss[s2:] if s["step_type"] == "llm_step")


def test_roles_come_from_stage_windows_and_map_to_stages(bundle):
    cs = calls(bundle)
    assert {s["role"] for s in cs} == {"preflight", "autoexclude", "threatmodel", "deepdive"}
    assert all(s["role_source"] == "stage_window" for s in cs)
    # one role, one system prompt (the prompt hash cross-checks the window)
    by_role = {}
    for s in cs:
        by_role.setdefault(s["role"], set()).add(s["prompt_id"])
    assert len(by_role["deepdive"]) == 1 and len(by_role["threatmodel"]) == 1
    tm = next(s for s in cs if s["role"] == "threatmodel")
    assert tm["stage"] == "S1"  # coding/role-stage-map.toml


def test_system_prompts_are_kept_although_the_calls_were_not_streamed(bundle):
    for pid in {s["prompt_id"] for s in calls(bundle)}:
        prompt = json.loads((bundle / "prompts" / f"{pid}.json").read_text())
        assert prompt["system"], pid


def test_parallel_sessions_with_one_prompt_are_told_apart(bundle):
    deepdive = [s for s in calls(bundle) if s["role"] == "deepdive"]
    instances = {s["agent_instance_id"] for s in deepdive}
    assert len(instances) == 2  # VVAH batched S4's units into two sessions
    for s in deepdive:
        if s["parent_step_id"]:
            assert len(s["input_delta"]) == 2  # only the tool result and the model's reply are new


def test_each_sessions_deltas_rebuild_its_last_request(bundle):
    records = load_proxy_log(FIXTURES / VVAH)
    request = {r["litellm_call_id"]: r["body"] for r in records if r["record"] == "request"}
    by_instance = {}
    for s in calls(bundle):
        by_instance.setdefault(s["agent_instance_id"], []).append(s)
    for instance, ss in by_instance.items():
        rebuilt = [m for s in ss for m in s["input_delta"]]
        last = normalize_messages(request[ss[-1]["litellm_call_id"]]["messages"])
        assert len(rebuilt) == len(last), instance
        assert [m["role"] for m in rebuilt] == [m["role"] for m in last], instance


def test_the_whole_patch_check_is_an_oracle_run_even_when_there_is_no_patch(bundle):
    oracle = [s for s in steps(bundle) if s["step_type"] == "oracle_run"]
    assert [(s["oracle_ref"]["stage"], s["oracle_ref"]["patch_scope"], s["output"]) for s in oracle] == \
        [(3, "combined", "no_patch"), (4, "combined", "skipped")]


def test_a_run_without_findings_has_an_empty_findings_list(bundle):
    assert json.loads((bundle / "findings.json").read_text())["findings"] == []

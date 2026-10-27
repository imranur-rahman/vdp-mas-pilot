"""Seam 2 for deepsec runs (ticket 06): the adapter's captures and setup gates
become `handoffs.jsonl` (ticket 05's collector format), which the exporter
turns into `handoff` and `code_decision` steps in time order; captured state
travels with the bundle; findings get ticket 11's `tp_target` check."""

import json
import shutil
from pathlib import Path

from pilot.exporter import export
from pilot.deepsec import handoff_records
from pilot.state import capture, load_manifest


def call(n, role, start, end, text):
    return {"record": "call", "litellm_call_id": f"c{n}",
            "pilot": {"pilot_role": role, "pilot_system": "deepsec", "pilot_prompt_hash": f"p-{role}"},
            "upstream_request": {"model": "claude-haiku-5-5", "system": f"You are {role}",
                                 "messages": [{"role": "user", "content": f"go {n}"}]},
            "start_time": start, "end_time": end,
            "response": {"model": "claude-haiku-5-5",
                         "choices": [{"message": {"content": text}, "finish_reason": "end_turn"}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 2}}}


FINDING = {"finding_id": "deepsec:finding_ab12", "system": "deepsec",
           "location": {"file": "src/md4c.c", "function": "md_is_container_mark",
                        "line_start": 5650, "line_end": 5650},
           "description": "OOB read\n\nCH(off) past end.", "poc": None, "patch": None,
           "stack_trace": None, "cybergym": None,
           "title": "OOB read", "lines": [5650], "native": {"severity": "HIGH"}}
PATCH = Path(__file__).resolve().parents[1] / "findings" / "md4c_arvo_31332.patch.diff"


T0 = 1793102400.0  # 2026-10-27 12:00:00 UTC


def make_run(tmp_path, decisions=()):
    run = tmp_path / "20261027T120000-deepsec-md4c_arvo_31332-dev"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({
        "run_id": run.name, "system": "deepsec", "task": "md4c/arvo_31332", "kind": "dev",
        "requested_model": "claude-haiku-5-5",
        "scope_evidence": {"out_of_scope": False, "c_files_investigated": ["src/md4c.c"]},
        "outcome": {"outcome": "completed", "model_calls": 2, "refusals": [], "cost_usd_openrouter": 0.01}}))
    (run / "proxy.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        call(1, "threat-model", "2026-10-27 12:00:01", "2026-10-27 12:00:02", "INFO written"),
        call(2, "investigator", "2026-10-27 12:00:10", "2026-10-27 12:00:12", "found one"),
    ]))
    capture(run, "after-threat-model", {"data/md4c/INFO.md": b"# md4c\n"},
            from_role="threat-model", to_role="investigator", ts=T0 + 5)
    capture(run, "end", {"data/md4c/INFO.md": b"# md4c\n", "data/md4c/files/src/md4c.c.json": b"{}\n"},
            from_role="investigator", to_role=None, ts=T0 + 20)
    (run / "handoffs.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in handoff_records(load_manifest(run), list(decisions))))
    (run / "findings.json").write_text(json.dumps({"findings": [FINDING]}))
    (run / "ground_truth").mkdir()
    shutil.copyfile(PATCH, run / "ground_truth" / "patch.diff")
    return run


def steps(bundle):
    return [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]


def test_handoffs_sit_between_the_calls_they_separate(tmp_path):
    ss = steps(export(make_run(tmp_path)))
    assert [s["step_type"] for s in ss] == ["llm_step", "handoff", "llm_step", "handoff"]
    h = ss[1]
    assert h["role"] == "threat-model"
    assert h["state_ref"]["label"] == "after-threat-model"
    assert h["state_ref"]["to_role"] == "investigator"


def test_captured_state_travels_with_the_bundle(tmp_path):
    bundle = export(make_run(tmp_path))
    ref = steps(bundle)[3]["state_ref"]
    assert (bundle / ref["diff"]).read_text().startswith("--- /dev/null")
    assert ref["added"] == ["data/md4c/files/src/md4c.c.json"]
    assert (bundle / "state" / "manifest.json").exists()


def test_deepsec_finding_in_the_patched_function_gets_tp_target(tmp_path):
    bundle = export(make_run(tmp_path))
    [f] = json.loads((bundle / "findings.json").read_text())["findings"]
    assert f["finding_id"] == "deepsec:finding_ab12"
    assert f["tp_target_check"]["method"] == "location_overlap"
    assert f["verdict"] == "tp_target"
    assert "deepsec:finding_ab12" in (bundle / "trajectory.md").read_text()


def test_scope_evidence_travels_with_the_bundle(tmp_path):
    bundle = export(make_run(tmp_path))
    assert json.loads((bundle / "run.json").read_text())["scope_evidence"]["c_files_investigated"] == ["src/md4c.c"]


def test_setup_gates_become_code_decision_steps_in_time_order(tmp_path):
    gate = {"ts": T0 + 6, "role": "matcher-generator", "name": "coverage-gate", "output": {"passed": False}}
    ss = steps(export(make_run(tmp_path, [gate])))
    assert [s["step_type"] for s in ss] == ["llm_step", "handoff", "code_decision", "llm_step", "handoff"]
    d = ss[2]
    assert d["role"] == "matcher-generator"
    assert d["state_ref"]["decision"] == "coverage-gate" and d["state_ref"]["passed"] is False
    assert d["output"].startswith("coverage-gate")

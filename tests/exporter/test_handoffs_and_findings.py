"""Seam 2 for adapters with captured state and normalized findings (ticket 06):
hand-offs become `handoff` steps in time order with a `state_ref`, the
captured state travels with the bundle, and findings are joined."""

import json
import shutil
from pathlib import Path

from pilot.exporter import export
from pilot.state import capture


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


def make_run(tmp_path):
    run = tmp_path / "20261027T120000-deepsec-md4c_arvo_31332-dev"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({
        "run_id": run.name, "system": "deepsec", "task": "md4c/arvo_31332", "kind": "dev",
        "requested_model": "claude-haiku-5-5",
        "outcome": {"outcome": "completed", "model_calls": 2, "refusals": [], "cost_usd_openrouter": 0.01}}))
    (run / "proxy.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        call(1, "threat-model", "2026-10-27 12:00:01", "2026-10-27 12:00:02", "INFO written"),
        call(2, "investigator", "2026-10-27 12:00:10", "2026-10-27 12:00:12", "found one"),
    ]))
    t0 = 1793102400.0  # 2026-10-27 12:00:00 UTC
    capture(run, "after-threat-model", {"data/md4c/INFO.md": b"# md4c\n"},
            from_role="threat-model", to_role="investigator", ts=t0 + 5)
    capture(run, "end", {"data/md4c/INFO.md": b"# md4c\n", "data/md4c/files/src/md4c.c.json": b"{}\n"},
            from_role="investigator", to_role=None, ts=t0 + 20)
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
    assert h["parent_step_id"] == ss[0]["step_id"]


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
    md = (bundle / "trajectory.md").read_text()
    assert "deepsec:finding_ab12" in md and "handoff" in md and "after-threat-model" in md


def test_out_of_scope_evidence_is_rendered(tmp_path):
    run = make_run(tmp_path)
    meta = json.loads((run / "run.json").read_text())
    meta["outcome"]["outcome"] = "out_of_scope"
    meta["scope_evidence"] = {"out_of_scope": True, "c_files_investigated": [],
                              "setup_stop": {"code": "NO_SCAN_CANDIDATES", "message": "paused"}}
    (run / "run.json").write_text(json.dumps(meta))
    md = (export(run) / "trajectory.md").read_text()
    assert "out_of_scope" in md and "NO_SCAN_CANDIDATES" in md


def test_code_decisions_become_steps_in_time_order(tmp_path):
    run = make_run(tmp_path)
    t0 = 1793102400.0
    (run / "decisions.jsonl").write_text(json.dumps(
        {"ts": t0 + 6, "role": "matcher-generator", "name": "coverage-gate", "output": {"passed": False}}) + "\n")
    ss = steps(export(run))
    assert [s["step_type"] for s in ss] == ["llm_step", "handoff", "code_decision", "llm_step", "handoff"]
    d = ss[2]
    assert d["role"] == "matcher-generator"
    assert d["output"] == {"decision": "coverage-gate", "passed": False}
    assert "coverage-gate" in (run / "bundle" / "trajectory.md").read_text()

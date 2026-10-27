"""Seam 2: OpenAnt scan-state capture at hand-offs.

OpenAnt writes `<step>.report.json` into the scan directory as each pipeline
step finishes (`core/step_report.py`). The collector treats each such file as
a hand-off, captures the scan state there, and records it in `handoffs.jsonl`.
`handoff_steps` turns those records into `handoff` step records for the bundle.
Two pure pieces are tested here: which report files are hand-offs (and the
phase each names), and the record -> step transform. The expected step
basenames are read from OpenAnt source.
"""

from pilot.adapters import openant, openant_collector as collector


def test_phase_of_report_strips_the_suffix():
    assert collector.phase_of_report("analyze.report.json") == "analyze"
    assert collector.phase_of_report("enhance.report.json") == "enhance"
    assert collector.phase_of_report("test.report.json") == "test"


def test_scan_aggregate_report_is_not_a_handoff():
    # scan.report.json is the whole-run summary, not a per-step hand-off.
    assert collector.is_handoff_report("analyze.report.json") is True
    assert collector.is_handoff_report("scan.report.json") is False
    assert collector.is_handoff_report("pipeline_output.json") is False


def test_handoff_steps_carry_the_phase_and_its_captured_state():
    records = [
        {"seq": 1, "step": "parse", "t": 1000.0, "report": "01_parse/parse.report.json",
         "state_ref": "01_parse", "files_changed": []},
        {"seq": 2, "step": "analyze", "t": 1100.0, "report": "02_analyze/analyze.report.json",
         "state_ref": "02_analyze", "files_changed": ["a.c"]},
    ]
    steps = openant.handoff_steps(records)
    assert [s["step_type"] for s in steps] == ["handoff", "handoff"]
    assert steps[0]["role"] == "parse"
    assert steps[0]["output"] == "parse"
    assert steps[0]["state_ref"] == "01_parse"
    assert steps[1]["role"] == "analyze"
    assert steps[1]["ts_start"] == 1100.0


def test_no_handoffs_yields_no_steps():
    assert openant.handoff_steps([]) == []

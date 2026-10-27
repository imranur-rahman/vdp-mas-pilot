"""Seam 2: OpenAnt scan-state capture at hand-offs.

OpenAnt writes `<step>.report.json` into the scan directory as each pipeline
step finishes (`core/step_report.py`). The collector treats each such file as
a hand-off, captures the scan state there, and records it in `handoffs.jsonl`.
`capture_state` turns those captures into the shared `state/` +
`handoffs.jsonl` the exporter reads; the bundle then shows one `handoff` step
per finished phase. The expected step
basenames are read from OpenAnt source.
"""

import json

from pilot.adapters import openant, openant_collector as collector
from pilot.exporter import export


def test_phase_of_report_strips_the_suffix():
    assert collector.phase_of_report("analyze.report.json") == "analyze"
    assert collector.phase_of_report("enhance.report.json") == "enhance"
    assert collector.phase_of_report("test.report.json") == "test"


def test_scan_aggregate_report_is_not_a_handoff():
    # scan.report.json is the whole-run summary, not a per-step hand-off.
    assert collector.is_handoff_report("analyze.report.json") is True
    assert collector.is_handoff_report("scan.report.json") is False
    assert collector.is_handoff_report("pipeline_output.json") is False


def _captures(tmp_path):
    """A collector capture tree: OpenAnt's scan dir after `parse`, then after `analyze`, then the end."""
    cap = tmp_path / "captures"
    recs = []
    for seq, (step, files) in enumerate([
            ("parse", {"parse.report.json": "{}"}),
            ("analyze", {"parse.report.json": "{}", "analyze.report.json": '{"vulnerable": 1}'}),
            ("end", {"parse.report.json": "{}", "analyze.report.json": '{"vulnerable": 1}'})], 1):
        name = f"{seq:02d}_{step}"
        for rel, text in files.items():
            (cap / name / "scan" / rel).parent.mkdir(parents=True, exist_ok=True)
            (cap / name / "scan" / rel).write_text(text)
        (cap / name / "files.diff").write_text("")
        recs.append({"seq": seq, "step": step, "t": 1000.0 + seq, "state_ref": name})
    (cap / "handoffs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return cap


def test_capture_state_writes_one_handoff_per_phase_with_its_scan_diff(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    records = openant.capture_state(run, _captures(tmp_path))
    assert [(r["role"], r["state_ref"]["to_role"]) for r in records] == \
        [("parse", "analyze"), ("analyze", None), ("end", None)]
    analyze_diff = (run / records[1]["state_ref"]["diff"]).read_text()
    assert "analyze.report.json" in analyze_diff      # what the analyze phase produced
    assert "parse.report.json" not in analyze_diff    # already there at the parse hand-off
    assert (run / "handoffs.jsonl").exists()


def test_bundle_shows_handoffs_and_openant_dynamic_tests(tmp_path):
    run = tmp_path / "20261027T120000-openant-md4c_arvo_31332-dev"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({
        "run_id": run.name, "system": "openant", "task": "md4c/arvo_31332", "kind": "dev",
        "requested_model": "claude-haiku-5-5", "outcome": {"outcome": "completed"}}))
    openant.capture_state(run, _captures(tmp_path))
    (run / "oracle_runs.jsonl").write_text(json.dumps(openant.dynamic_oracle_records({"results": [
        {"finding_id": "F-001", "status": "SKIPPED", "details": "no Docker template for c",
         "dockerfile": "", "evidence": []}]}, ts=1010.0)[0]) + "\n")
    bundle = export(run)
    steps = [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]
    assert [s["role"] for s in steps if s["step_type"] == "handoff"] == ["parse", "analyze", "end"]
    oracle = [s for s in steps if s["step_type"] == "oracle_run"]
    assert oracle[0]["output"] == "SKIPPED"
    assert oracle[0]["oracle_ref"]["source"] == "openant_dynamic_test"
    assert oracle[0]["oracle_ref"]["finding_id"] == "F-001"
    assert oracle[0]["oracle_ref"]["built_image"] is False
    assert "openant_dynamic_test" in (bundle / "trajectory.md").read_text()

"""Seam 2: adapter-emitted steps reach the bundle.

An adapter (OpenAnt) writes extra step records to `<run>/extra_steps.jsonl`
(hand-offs and its own dynamic-test oracle runs). The exporter appends them
after the model-call and CyberGym steps, numbering them in sequence, and
renders them. Existing fixtures carry no `extra_steps.jsonl`, so their step
numbering is unchanged.
"""

import json
from pathlib import Path

from pilot.adapters import openant
from pilot.exporter import export


def _minimal_run(tmp_path: Path) -> Path:
    run = tmp_path / "20261027T120000-openant-md4c_arvo_31332-dev"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({
        "run_id": run.name, "system": "openant", "task": "md4c/arvo_31332",
        "kind": "dev", "requested_model": "claude-haiku-5-5", "outcome": {"outcome": "completed"},
    }))
    handoffs = openant.handoff_steps([
        {"seq": 1, "step": "analyze", "t": 100.0, "state_ref": "01_analyze", "files_changed": []},
    ])
    oracle = openant.dynamic_oracle_steps({"results": [
        {"finding_id": "F-001", "status": "SKIPPED", "details": "no Docker template for c",
         "dockerfile": "", "evidence": [], "retry_count": 0},
    ]})
    (run / "extra_steps.jsonl").write_text(
        "".join(json.dumps(s) + "\n" for s in handoffs + oracle))
    return run


def test_extra_steps_are_appended_and_numbered(tmp_path):
    run = _minimal_run(tmp_path)
    bundle = export(run)
    steps = [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]
    kinds = [(s["step_type"], s.get("role") or (s.get("oracle_ref") or {}).get("source")) for s in steps]
    assert ("handoff", "analyze") in kinds
    assert ("oracle_run", "openant_dynamic_test") in kinds
    # sequential, gap-free numbering across all steps
    assert [s["seq"] for s in steps] == list(range(1, len(steps) + 1))


def test_trajectory_renders_the_openant_oracle_and_handoff(tmp_path):
    run = _minimal_run(tmp_path)
    bundle = export(run)
    md = (bundle / "trajectory.md").read_text()
    assert "openant_dynamic_test" in md
    assert "SKIPPED" in md
    assert "handoff" in md

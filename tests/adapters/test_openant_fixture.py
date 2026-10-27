"""Seam 2: the OpenAnt data run's bundle (trimmed fixture: one proxy call per
phase, the run's hand-offs and findings).

Each of OpenAnt's seven phases reached the proxy under its own key, so every
model call carries its phase as the role (D2); the run's eight step-report
hand-offs appear as `handoff` steps; OpenAnt confirmed no finding on md4c.
"""

import json
import shutil
from pathlib import Path

from pilot.adapters import openant
from pilot.exporter import export

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runs" / "20261027T142353-openant-md4c_arvo_31332-data"


def _bundle(tmp_path):
    run = tmp_path / FIXTURE.name
    shutil.copytree(FIXTURE, run)
    return export(run)


def test_every_phase_is_a_labeled_role(tmp_path):
    steps = [json.loads(l) for l in (_bundle(tmp_path) / "steps.jsonl").read_text().splitlines()]
    llm_roles = {s["role"] for s in steps if s["step_type"] == "llm_step"}
    assert llm_roles == set(openant.PHASES)


def test_handoffs_follow_openant_step_reports(tmp_path):
    steps = [json.loads(l) for l in (_bundle(tmp_path) / "steps.jsonl").read_text().splitlines()]
    roles = [s["role"] for s in steps if s["step_type"] == "handoff"]
    assert roles[0] == "parse" and roles[-1] == "end"
    assert {"enhance", "analyze"} <= set(roles)


def test_stage_map_covers_every_analysis_phase(tmp_path):
    run = json.loads((_bundle(tmp_path) / "run.json").read_text())
    assert run["stage_map"]["unmapped_roles"] == ["app_context", "report"]  # no VDP analysis claim

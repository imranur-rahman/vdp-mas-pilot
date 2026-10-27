"""Seam 2: stages are joined from a versioned role-to-stage map (T-5).

A recorded run folder and a map file go in; the bundle's steps carry the stage,
or the stage choices when the agent's role maps to two stages.
"""

import json
import shutil
from pathlib import Path

import pytest

from pilot.exporter import export
from pilot.stages import DEFAULT_MAP, load_map

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runs"
DEV = "20261026T235842-claude-code-md4c_arvo_31332-dev"


def steps(bundle: Path) -> list[dict]:
    return [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]


def export_with_map(tmp_path, map_text: str) -> Path:
    run_dir = tmp_path / DEV
    shutil.copytree(FIXTURES / DEV, run_dir)
    map_file = tmp_path / "role-stage-map.toml"
    map_file.write_text(map_text)
    return export(run_dir, stage_map=map_file)


def test_a_role_with_one_stage_gives_every_one_of_its_steps_that_stage(tmp_path):
    bundle = export_with_map(tmp_path, """
version = "2026-10-27.1"
[systems.claude-code]
agent = ["S5"]
""")
    model_steps = [s for s in steps(bundle) if s["step_type"] == "llm_step"]
    assert model_steps and all(s["stage"] == "S5" for s in model_steps)
    assert all(s["stage_options"] == ["S5"] for s in model_steps)


def test_a_dual_stage_role_leaves_the_choice_to_the_coder(tmp_path):
    bundle = export_with_map(tmp_path, """
version = "2026-10-27.2"
[systems.claude-code]
agent = ["S5", "S7"]
""")
    model_steps = [s for s in steps(bundle) if s["step_type"] == "llm_step"]
    assert all(s["stage"] is None and s["stage_options"] == ["S5", "S7"] for s in model_steps)


def test_the_bundle_records_which_map_version_assigned_its_stages(tmp_path):
    bundle = export_with_map(tmp_path, """
version = "2026-10-27.3"
[systems.claude-code]
agent = ["S5"]
""")
    run = json.loads((bundle / "run.json").read_text())
    assert run["stage_map"]["version"] == "2026-10-27.3"
    assert len(run["stage_map"]["sha256"]) == 64


def test_roles_missing_from_the_map_stay_unassigned(tmp_path):
    bundle = export_with_map(tmp_path, """
version = "2026-10-27.4"
[systems.some-other-system]
planner = ["S1"]
""")
    for s in steps(bundle):
        assert s["stage"] is None and s["stage_options"] == []
    assert json.loads((bundle / "run.json").read_text())["stage_map"]["unmapped_roles"] == ["agent"]


def test_oracle_runs_have_no_stage(tmp_path):
    bundle = export_with_map(tmp_path, 'version = "x"\n[systems.claude-code]\nagent = ["S5"]\n')
    assert all(s["stage"] is None for s in steps(bundle) if s["step_type"] == "oracle_run")


def test_a_map_naming_an_unknown_stage_is_rejected(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('version = "x"\n[systems.claude-code]\nagent = ["S9"]\n')
    with pytest.raises(ValueError, match="S9"):
        load_map(bad)


def test_the_repository_map_loads_and_is_versioned():
    m = load_map(DEFAULT_MAP)
    assert m.version

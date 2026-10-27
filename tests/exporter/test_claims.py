"""Seam 2: claim extraction (T-3) from a recorded extraction response.

The dev fixture carries `claims.jsonl`: the requests the exporter sent to
Claude Haiku 5.5 through the proxy and the responses it got, captured once.
Exporting replays it; no test calls a model.
"""

import json
import shutil
from pathlib import Path

import pytest

from pilot import claims
from pilot.exporter import export

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUNS = FIXTURES / "runs"
TASKS = FIXTURES / "tasks"
DEV = "20261026T235842-claude-code-md4c_arvo_31332-dev"
DATA = "20261027T001135-claude-code-md4c_arvo_31332-data"


def steps(bundle: Path) -> list[dict]:
    return [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]


def run_folder(tmp_path, name=DEV) -> Path:
    run_dir = tmp_path / name
    shutil.copytree(RUNS / name, run_dir)
    return run_dir


def no_model(request):
    raise AssertionError("tried to call the model although a recording exists")


@pytest.fixture(scope="module")
def dev_bundle(tmp_path_factory):
    return export(run_folder(tmp_path_factory.mktemp("claims")), tasks_dir=TASKS, extractor=no_model)


def test_each_model_step_gets_a_claim_from_the_recorded_extraction(dev_bundle):
    model_steps = [s for s in steps(dev_bundle) if s["step_type"] == "llm_step"]
    assert all(s["claim"] is not None for s in model_steps)
    with_text = [s for s in model_steps if s["claim"]["text"]]
    # reads and builds assert nothing; the recorded pass found 23 claims in 48 steps
    assert len(with_text) >= 15
    # the agent's own conclusion: an overflow at md4c.c line 2321
    assert "2321" in next(s for s in model_steps if s["step_id"] == "s0046")["claim"]["text"]


def test_each_claim_records_the_model_and_prompt_that_extracted_it(dev_bundle):
    claim = next(s["claim"] for s in steps(dev_bundle) if s["claim"])
    assert claim["source"] == "extracted"
    assert "haiku" in claim["model"]
    prompt = json.loads((dev_bundle / claim["prompt"]).read_text())
    assert prompt["version"] == claims.PROMPT_VERSION
    assert prompt["system"] == claims.SYSTEM_PROMPT
    assert prompt["requested_model"] == claims.MODEL


def test_steps_that_are_not_model_output_get_no_claim(tmp_path):
    bundle = export(run_folder(tmp_path, DATA), tasks_dir=TASKS, extractor=no_model)
    assert all(s["claim"] is None for s in steps(bundle))  # refusals and oracle runs


def test_without_a_recording_or_extractor_claims_stay_empty_and_the_bundle_says_so(tmp_path):
    run_dir = run_folder(tmp_path)
    (run_dir / claims.RECORDING).unlink()
    bundle = export(run_dir, tasks_dir=TASKS)
    assert all(s["claim"] is None for s in steps(bundle))
    status = json.loads((bundle / "run.json").read_text())["claims"]
    assert status["extracted_steps"] == 0 and status["missing_steps"] == 48


def fake_model(request):
    """Answers every step it is sent with a fixed claim, as the real model would in JSON."""
    ids = [line.split("]")[0].strip("[ ") for line in request["messages"][0]["content"].splitlines()
           if line.startswith("[s")]
    text = json.dumps({i: f"claim for {i}" for i in ids})
    return {"model": "fake-haiku", "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_a_live_extraction_is_recorded_so_the_next_export_replays_it(tmp_path):
    run_dir = run_folder(tmp_path)
    (run_dir / claims.RECORDING).unlink()
    first = steps(export(run_dir, tasks_dir=TASKS, extractor=fake_model))
    assert (run_dir / claims.RECORDING).exists()
    again = steps(export(run_dir, tasks_dir=TASKS, extractor=no_model))
    assert [s["claim"] for s in again] == [s["claim"] for s in first]
    assert next(s for s in first if s["step_id"] == "s0001")["claim"]["text"] == "claim for s0001"


def test_claims_are_shown_in_the_rendered_trajectory(dev_bundle):
    assert "**Claim (extracted by" in (dev_bundle / "trajectory.md").read_text()

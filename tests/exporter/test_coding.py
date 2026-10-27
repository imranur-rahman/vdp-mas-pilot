"""Seam 2: the coding kit. A bundle goes in, a per-coder label file comes out;
coders' files go in, the coded steps and Cohen's kappa come out."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from pilot import coding
from pilot.exporter import export

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DEV = "20261026T235842-claude-code-md4c_arvo_31332-dev"


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("coding") / DEV
    shutil.copytree(FIXTURES / "runs" / DEV, run_dir)
    return export(run_dir, tasks_dir=FIXTURES / "tasks")


def steps(bundle: Path) -> list[dict]:
    return [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]


def edit(path: Path, change) -> Path:
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data))
    return path


def test_a_label_file_is_keyed_by_every_step_id_and_finding_id(bundle, tmp_path):
    path = coding.init_labels(bundle, "alice", tmp_path / "alice.yaml")
    labels = yaml.safe_load(path.read_text())
    assert labels["coder"] == "alice"
    assert labels["run_id"] == DEV
    assert list(labels["steps"]) == [s["step_id"] for s in steps(bundle)]
    assert list(labels["findings"]) == ["f001"]
    assert all(v["failure_modes"] is None for v in labels["steps"].values())  # nothing coded yet
    assert labels["findings"]["f001"]["verdict"] is None


def test_the_label_file_shows_coders_the_extracted_claim_next_to_its_step(bundle, tmp_path):
    text = coding.init_labels(bundle, "alice", tmp_path / "alice.yaml").read_text()
    s46 = next(s for s in steps(bundle) if s["step_id"] == "s0046")
    assert s46["claim"]["text"][:40] in text


def test_label_files_go_under_the_coding_folder_by_run_and_coder(bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(coding, "LABELS_DIR", tmp_path / "labels")
    assert coding.init_labels(bundle, "bob") == tmp_path / "labels" / DEV / "bob.yaml"
    with pytest.raises(FileExistsError):
        coding.init_labels(bundle, "bob")  # never overwrite a coder's work


def test_a_coder_overrides_an_extracted_claim(bundle, tmp_path):
    path = edit(coding.init_labels(bundle, "alice", tmp_path / "a.yaml"),
                lambda d: d["steps"]["s0046"].update(claim="The overflow is in md_is_inline_link_spec."))
    coded = {s["step_id"]: s for s in coding.coded_steps(bundle, path)}
    assert coded["s0046"]["claim"] == {"text": "The overflow is in md_is_inline_link_spec.",
                                       "source": "coder", "coder": "alice"}
    assert coded["s0045"]["claim"]["source"] == "extracted"


def test_a_coder_chooses_the_stage_of_a_dual_stage_step(bundle, tmp_path):
    s1 = steps(bundle)[0]
    assert s1["stage"] is None and "S5" in s1["stage_options"]  # claude-code does every job
    path = edit(coding.init_labels(bundle, "alice", tmp_path / "a.yaml"),
                lambda d: d["steps"]["s0001"].update(stage="S5"))
    assert coding.coded_steps(bundle, path)[0]["stage"] == "S5"


def test_a_stage_outside_the_roles_options_is_rejected(bundle, tmp_path):
    path = edit(coding.init_labels(bundle, "alice", tmp_path / "a.yaml"),
                lambda d: d["steps"]["s0001"].update(stage="S1"))
    with pytest.raises(ValueError, match="s0001"):
        coding.coded_steps(bundle, path)


def test_a_verdict_must_be_one_left_to_coders(bundle, tmp_path):
    path = edit(coding.init_labels(bundle, "alice", tmp_path / "a.yaml"),
                lambda d: d["findings"]["f001"].update(verdict="maybe"))
    with pytest.raises(ValueError, match="f001"):
        coding.load_labels(path)


# --- kappa: expected values are worked by hand, not by the formula under test ---

def label_file(tmp_path, coder, steps=None, findings=None) -> Path:
    path = tmp_path / f"{coder}.yaml"
    path.write_text(yaml.safe_dump({"schema": coding.SCHEMA, "run_id": "r", "coder": coder,
                                    "steps": steps or {}, "findings": findings or {}}))
    return path


def test_kappa_for_one_failure_mode_matches_a_worked_two_by_two_table(tmp_path):
    # 50 steps: both code M1 on 20, only A on 5, only B on 10, neither on 15.
    # p_o = 35/50 = 0.70; p_e = 0.5*0.6 + 0.5*0.4 = 0.50; kappa = 0.20/0.50 = 0.40
    pairs = [(1, 1)] * 20 + [(1, 0)] * 5 + [(0, 1)] * 10 + [(0, 0)] * 15
    a = {f"s{i:04d}": {"failure_modes": ["M1"] if x else []} for i, (x, _) in enumerate(pairs)}
    b = {f"s{i:04d}": {"failure_modes": ["M1"] if y else []} for i, (_, y) in enumerate(pairs)}
    result = coding.kappa(label_file(tmp_path, "a", a), label_file(tmp_path, "b", b))
    assert result["failure_mode:M1"]["n"] == 50
    assert result["failure_mode:M1"]["observed_agreement"] == pytest.approx(0.70)
    assert result["failure_mode:M1"]["kappa"] == pytest.approx(0.40)


def test_kappa_for_verdicts_matches_a_worked_three_category_example(tmp_path):
    # p_o = 8/10; p_e = .4*.3 + .4*.4 + .2*.3 = .34; kappa = .46/.66
    va = ["tp_other"] * 4 + ["fp"] * 4 + ["unverified"] * 2
    vb = ["tp_other"] * 3 + ["fp"] * 4 + ["unverified"] * 3
    a = {f"f{i:03d}": {"verdict": v} for i, v in enumerate(va)}
    b = {f"f{i:03d}": {"verdict": v} for i, v in enumerate(vb)}
    result = coding.kappa(label_file(tmp_path, "a", findings=a), label_file(tmp_path, "b", findings=b))
    assert result["verdict"]["kappa"] == pytest.approx(0.46 / 0.66)


def test_kappa_counts_only_items_both_coders_coded(tmp_path):
    a = {"s1": {"failure_modes": ["M1"]}, "s2": {"failure_modes": []}, "s3": {"failure_modes": None}}
    b = {"s1": {"failure_modes": ["M1"]}, "s2": {"failure_modes": None}, "s3": {"failure_modes": []}}
    result = coding.kappa(label_file(tmp_path, "a", a), label_file(tmp_path, "b", b))
    assert result["failure_mode:M1"]["n"] == 1


def test_kappa_is_undefined_when_chance_agreement_is_total(tmp_path):
    a = {"s1": {"failure_modes": ["M1"]}, "s2": {"failure_modes": ["M1"]}}
    result = coding.kappa(label_file(tmp_path, "a", a), label_file(tmp_path, "b", a))
    assert result["failure_mode:M1"]["kappa"] is None
    assert result["failure_mode:M1"]["observed_agreement"] == 1.0


def test_kappa_refuses_files_from_different_runs(tmp_path):
    a = label_file(tmp_path, "a")
    other = tmp_path / "other.yaml"
    other.write_text(yaml.safe_dump({"schema": coding.SCHEMA, "run_id": "r2", "coder": "b",
                                     "steps": {}, "findings": {}}))
    with pytest.raises(ValueError, match="run"):
        coding.kappa(a, other)

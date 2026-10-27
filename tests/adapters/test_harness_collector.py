"""Anthropic harness collector: the harness results tree goes in, the pilot
run folder's hand-off log, oracle-run log, copied artifacts and transcript
index come out. No Docker, no model calls.

A fake results tree stands in for a real `vuln-pipeline run ... && patch` batch:
the shape (result.json, found_bugs.jsonl, reports/manifest.jsonl, reports/bug_NN,
and the stream-json transcripts) is the harness's own on-disk contract.
"""

import json
from pathlib import Path

from pilot.adapters import harness

ASAN = ("==1==ERROR: AddressSanitizer: heap-buffer-overflow\n"
        "    #0 0x4a1 in md_is_container_mark /src/md4c/src/md4c.c:5685:9\n")


def build_batch(root: Path) -> Path:
    """A single-run batch that found, verified, reported and patched one bug."""
    batch = root / "results" / "md4c" / "20261027T000000Z"
    run = batch / "run_000"
    run.mkdir(parents=True)
    (run / "poc.bin").write_bytes(b"\x00bad-markdown\n")
    (run / "result.json").write_text(json.dumps({
        "target": "md4c", "status": "crash_found",
        "crash": {"poc_path": "/work/poc.bin", "crash_type": "heap-buffer-overflow",
                  "crash_output": ASAN, "exit_code": 1,
                  "reproduction_command": "/work/entry /work/poc.bin"},
        "verdict": {"passed": True, "score": 8.0,
                    "criteria": {"criterion_1": True}, "evidence": "reproduced 3/3"},
        "find_transcript": "see find_transcript.jsonl (12 messages)",
        "grade_transcript": "see grade_transcript.jsonl (5 messages)",
    }))
    (run / "find_transcript.jsonl").write_text(
        json.dumps({"type": "system", "subtype": "init", "session_id": "find-sess"}) + "\n")
    (run / "grade_transcript.jsonl").write_text(
        json.dumps({"type": "system", "subtype": "init", "session_id": "grade-sess"}) + "\n")
    (batch / "found_bugs.jsonl").write_text(
        json.dumps({"source": "config", "summary": "none"}) + "\n"
        + json.dumps({"run_idx": 0, "asan_excerpt": ASAN}) + "\n")
    reports = batch / "reports"
    bug = reports / "bug_00"
    bug.mkdir(parents=True)
    (reports / "manifest.jsonl").write_text(
        json.dumps({"bug_id": 0, "run_idx": 0, "asan_excerpt": ASAN}) + "\n")
    (bug / "report.json").write_text(json.dumps({
        "report": "OOB read in md_is_container_mark.", "severity": "high", "grader_score": 7}))
    (bug / "patch.diff").write_text(
        "diff --git a/src/md4c.c b/src/md4c.c\n--- a/src/md4c.c\n+++ b/src/md4c.c\n"
        "@@ -5685,0 +5685,1 @@ md_is_container_mark(MD_CTX* ctx)\n+   off < ctx->size &&\n")
    (bug / "patch_result.json").write_text(json.dumps({
        "verdict": {"t0_builds": True, "t1_poc_stops": True, "t2_tests_pass": True,
                    "re_attack_clean": True, "passed": True, "evidence": {"t1": "no asan"}},
        "rationale": "add bounds check", "iterations": 1}))
    (bug / "report_transcript_run000.jsonl").write_text(
        json.dumps({"type": "system", "subtype": "init", "session_id": "report-sess"}) + "\n")
    (bug / "patch_transcript_it0.jsonl").write_text(
        json.dumps({"type": "system", "subtype": "init", "session_id": "patch-sess"}) + "\n")
    return batch


def collected(tmp_path):
    batch = build_batch(tmp_path / "harness")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    harness.collect(batch, run_dir)
    return run_dir


def jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_handoff_log_names_the_finder_to_grader_poc_handoff(tmp_path):
    run_dir = collected(tmp_path)
    handoffs = jsonl(run_dir / "handoffs.jsonl")
    poc = next(h for h in handoffs if h["label"] == "poc" and h["to_role"] == "grade")
    assert poc["from_role"] == "find"
    # the only thing that crosses the trust boundary is the PoC bytes
    assert any(f["name"] == "poc.bin" for f in poc["files"])
    assert (run_dir / poc["files"][0]["path"]).read_bytes() == b"\x00bad-markdown\n"


def test_found_bugs_and_manifest_are_captured_as_handoffs(tmp_path):
    run_dir = collected(tmp_path)
    labels = {h["label"] for h in jsonl(run_dir / "handoffs.jsonl")}
    assert "found_bugs" in labels
    assert "manifest" in labels
    fb = next(h for h in jsonl(run_dir / "handoffs.jsonl") if h["label"] == "found_bugs")
    assert (run_dir / fb["files"][0]["path"]).exists()


def test_handoffs_are_ordered_in_pipeline_sequence(tmp_path):
    run_dir = collected(tmp_path)
    seqs = [h["seq"] for h in jsonl(run_dir / "handoffs.jsonl")]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_grader_reruns_are_recorded_as_oracle_runs(tmp_path):
    run_dir = collected(tmp_path)
    oracle = jsonl(run_dir / "oracle_runs.jsonl")
    grade = next(o for o in oracle if o["role"] == "grade")
    assert grade["kind"] == "poc_reproduction"
    assert grade["result"]["passed"] is True
    assert grade["poc_ref"] == "harness/run_000/poc.bin"


def test_patch_grader_tiers_are_recorded_as_oracle_runs(tmp_path):
    run_dir = collected(tmp_path)
    oracle = jsonl(run_dir / "oracle_runs.jsonl")
    tiers = {o["kind"] for o in oracle if o["role"] == "patch-grade"}
    assert {"t0_build", "t1_poc_stops", "t2_tests_pass", "re_attack"} <= tiers


def test_collected_data_feeds_normalized_findings(tmp_path):
    batch = build_batch(tmp_path / "harness")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    harness.collect(batch, run_dir)
    findings = json.loads((run_dir / "findings.json").read_text())["findings"]
    assert len(findings) == 1
    assert findings[0]["poc"] == "harness/run_000/poc.bin"
    assert findings[0]["patch"].endswith("patch.diff")
    assert (run_dir / findings[0]["patch"]).exists()


def test_native_transcripts_are_indexed_by_role_and_copied(tmp_path):
    run_dir = collected(tmp_path)
    index = jsonl(run_dir / "transcripts.jsonl")
    by_role = {t["role"]: t for t in index}
    assert {"find", "grade", "report", "patch"} <= set(by_role)
    assert by_role["find"]["session_id"] == "find-sess"
    assert by_role["grade"]["session_id"] == "grade-sess"
    # each indexed transcript is copied into the run folder
    for t in index:
        assert (run_dir / t["path"]).exists()
    # find and grade belong to the same run instance
    assert by_role["find"]["run_idx"] == 0 and by_role["grade"]["run_idx"] == 0

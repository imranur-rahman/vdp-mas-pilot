"""deepsec output adapter: its per-file records (`.deepsec/data/<id>/files/**.json`,
schema in deepsec docs/data-layout.md) become normalized findings, and the
run's C coverage decides `out_of_scope` (D8)."""

from pilot.deepsec import normalize_findings, scope_evidence

RECORD = {
    "filePath": "src/md4c.c",
    "projectId": "md4c",
    "candidates": [{"vulnSlug": "c-buffer-index", "lineNumbers": [5688], "snippet": "CH(off)",
                    "matchedPattern": "index"}],
    "status": "analyzed",
    "findings": [
        {"findingId": "finding_ab12", "severity": "HIGH", "vulnSlug": "other-oob-read",
         "title": "Out-of-bounds read in md_is_container_mark",
         "description": "CH(off) is read when off == ctx->size.",
         "lineNumbers": [5690, 5688], "recommendation": "Check off < ctx->size.",
         "confidence": "medium", "producedByRunId": "20261027-aaaa",
         "revalidation": {"verdict": "false-positive", "reasoning": "guarded", "runId": "r2",
                          "revalidatedAt": "2026-10-27T00:00:00Z", "model": "claude-haiku-5-5"}},
    ],
    "analysisHistory": [{"runId": "20261027-aaaa", "agentType": "claude-agent-sdk",
                         "model": "claude-haiku-5-5", "findingCount": 1}],
}
PY_RECORD = {"filePath": "scripts/build_folding_map.py", "projectId": "md4c", "candidates": [],
             "status": "analyzed", "findings": [],
             "analysisHistory": [{"runId": "20261027-aaaa", "findingCount": 0}]}


def test_each_reported_finding_becomes_one_normalized_finding():
    [f] = normalize_findings([RECORD, PY_RECORD])
    assert f["finding_id"] == "deepsec:finding_ab12"
    assert f["system"] == "deepsec"
    assert f["location"] == {"file": "src/md4c.c", "function": None,
                             "line_start": 5688, "line_end": 5690, "lines": [5688, 5690]}
    assert f["title"] == "Out-of-bounds read in md_is_container_mark"
    assert f["description"] == "CH(off) is read when off == ctx->size."
    assert f["poc"] is None and f["patch"] is None


def test_the_systems_own_judgement_is_kept_but_is_not_a_verdict():
    [f] = normalize_findings([RECORD])
    assert f["native"]["severity"] == "HIGH"
    assert f["native"]["revalidation"]["verdict"] == "false-positive"
    assert f["native"]["recommendation"] == "Check off < ctx->size."
    assert "verdict" not in f  # coders decide (ADR 0004)


def test_findings_without_an_id_get_a_stable_one():
    rec = {**RECORD, "findings": [{**RECORD["findings"][0], "findingId": None}]}
    a = normalize_findings([rec])[0]["finding_id"]
    b = normalize_findings([rec])[0]["finding_id"]
    assert a == b and a.startswith("deepsec:")


def test_run_that_investigated_a_c_file_is_in_scope():
    ev = scope_evidence([RECORD, PY_RECORD], setup_state=None)
    assert ev["out_of_scope"] is False
    assert ev["c_files_investigated"] == ["src/md4c.c"]
    assert ev["files_investigated"] == ["scripts/build_folding_map.py", "src/md4c.c"]


def test_run_that_never_investigated_c_is_out_of_scope_with_evidence():
    state = {"lastError": {"code": "NO_SCAN_CANDIDATES", "message": "Setup paused"},
             "baselineScanSummary": {"languageStats": {"c": 12, "python": 4}},
             "matcherAttempts": [{"outcome": "empty-proposal", "proposedSlugs": []}]}
    ev = scope_evidence([PY_RECORD], setup_state=state)
    assert ev["out_of_scope"] is True
    assert ev["c_files_investigated"] == []
    assert ev["c_files_with_candidates"] == []
    assert ev["setup_stop"] == {"code": "NO_SCAN_CANDIDATES", "message": "Setup paused"}
    assert ev["language_stats"] == {"c": 12, "python": 4}
    assert ev["matcher_attempts"] == state["matcherAttempts"]

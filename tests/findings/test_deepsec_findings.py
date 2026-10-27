"""deepsec output adapter: its per-file records (`.deepsec/data/<id>/files/**.json`,
schema in deepsec docs/data-layout.md) become normalized findings, and the
run's C coverage decides `out_of_scope` (D8)."""

from pilot.deepsec import code_decisions, decide_outcome, normalize_findings, scope_evidence

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


SOURCE = "\n" * 5640 + "static int\nmd_is_container_mark(MD_CTX* ctx, OFF beg)\n{\n" + "    x;\n" * 50 + "}\n"


def test_each_reported_finding_becomes_one_normalized_finding():
    [f] = normalize_findings([RECORD, PY_RECORD])
    assert f["finding_id"] == "deepsec:finding_ab12"
    assert f["system"] == "deepsec"
    assert f["location"] == {"file": "src/md4c.c", "function": None, "line_start": 5688, "line_end": 5690}
    assert f["lines"] == [5688, 5690]
    assert f["title"] == "Out-of-bounds read in md_is_container_mark"
    assert f["description"] == ("Out-of-bounds read in md_is_container_mark\n\n"
                                "CH(off) is read when off == ctx->size.")
    assert f["poc"] is None and f["patch"] is None
    assert f["stack_trace"] is None and f["cybergym"] is None


def test_the_function_is_the_one_enclosing_the_reported_lines():
    [f] = normalize_findings([RECORD], source_of={"src/md4c.c": SOURCE}.get)
    assert f["location"]["function"] == "md_is_container_mark"


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


SETUP_STATE = {
    "phases": {
        "baseline-scan": {"status": "complete", "completedAt": "2026-10-27T12:00:30Z"},
        "coverage": {"status": "complete", "completedAt": "2026-10-27T12:00:31Z"},
        "matchers": {"status": "complete", "completedAt": "2026-10-27T12:01:00Z"},
        "final-scan": {"status": "complete", "completedAt": "2026-10-27T12:01:05Z"},
    },
    "baselineScanSummary": {"runId": "s1", "languageStats": [{"language": "python", "scannedFiles": 4,
                                                              "candidates": 0, "matchRate": 0}]},
    "finalScanSummary": {"runId": "s2", "languageStats": []},
    "coverageReport": {"passed": False, "reasons": ["surface md-parse has no candidates"]},
    "matcherAttempts": [{"proposedSlugs": ["c-parser-entry"], "acceptedSlugs": [],
                         "rejected": [{"slug": "c-parser-entry", "reason": "too broad"}], "outcome": "rescanned"}],
    "lastError": {"phase": "coverage", "code": "NO_SCAN_CANDIDATES", "message": "paused",
                  "at": "2026-10-27T12:01:06Z"},
}


def test_setup_gates_become_code_decisions_in_time_order():
    ds = code_decisions(SETUP_STATE)
    assert [d["name"] for d in ds] == ["baseline-scan", "coverage-gate", "matcher-attempt-1", "final-scan",
                                       "setup-stop"]
    assert [d["ts"] for d in ds] == sorted(d["ts"] for d in ds)
    assert ds[1]["output"] == {"passed": False, "reasons": ["surface md-parse has no candidates"]}
    assert ds[2]["output"]["rejected"] == [{"slug": "c-parser-entry", "reason": "too broad"}]
    assert ds[4]["output"]["code"] == "NO_SCAN_CANDIDATES"
    assert all(d["role"] == "matcher-generator" for d in ds)


def test_no_setup_state_means_no_code_decisions():
    assert code_decisions(None) == []


OUT = {"out_of_scope": True}
IN = {"out_of_scope": False}
ALL_OK = [{"role": r, "exit_code": 0} for r in ("threat-model", "matcher-generator", "investigator", "revalidator")]


def test_out_of_scope_when_deepsec_finished_without_touching_c():
    assert decide_outcome("completed", ALL_OK, OUT, error=None) == "out_of_scope"
    assert decide_outcome("completed", ALL_OK, IN, error=None) == "completed"


def test_out_of_scope_when_setups_coverage_gate_stops_the_run():
    cmds = ALL_OK[:1] + [{"role": "matcher-generator", "exit_code": 1,
                          "setup_stop": {"code": "NO_SCAN_CANDIDATES", "message": "paused"}}]
    assert decide_outcome("completed", cmds, OUT, error=None) == "out_of_scope"


def test_a_run_broken_by_setup_problems_is_not_out_of_scope():
    cmds = [{"role": "threat-model", "exit_code": 2,
             "setup_stop": {"code": "VERCEL_AUTH_REQUIRED", "message": "login"}}]
    assert decide_outcome("completed", cmds, OUT, error=None) == "adapter_error"
    assert decide_outcome("completed", [], OUT, error="RuntimeError('install')") == "adapter_error"


def test_budget_and_refusal_outcomes_win():
    assert decide_outcome("budget_exhausted", ALL_OK[:2], OUT, error=None) == "budget_exhausted"
    assert decide_outcome("refused", ALL_OK[:1], OUT, error=None) == "refused"

"""VVAH output adapter: VVAH's typed report and S10 case folders go in,
normalized findings come out (spec: Normalized finding)."""

from pilot.adapters.vvah import normalize_findings


def ranked(case_id, file, line_start, line_end=0, title="Heap overflow", **extra):
    finding = {"title": title, "file": file, "line_start": line_start, "line_end": line_end,
               "vuln_class": "heap-overflow", "cwe": "CWE-122", "severity": "high",
               "description": "reads past the end of the line buffer", "case_id": case_id,
               "verdict": "TRUE_POSITIVE", "verdict_confidence": 8, **extra}
    return {"finding": finding, "severity": "high", "exploitability_notes": ""}


REPORT = {
    "schema_version": "1.0",
    "findings": [
        ranked("vvaf1_aaa", "src/md4c.c", 6120, 6131, sink_ref="src/md4c.c:6125"),
        ranked("vvaf1_bbb", "md2html/render.c", 40, title="Format string"),
    ],
    "dropped": [{"file": "src/entity.c", "line": 9, "vuln_class": "other", "title": "noise",
                 "chunk_id": "u3", "reason": "FALSE_POSITIVE"}],
}
CASES = [{"dir": "vvah/security-remediation/01_heap_overflow",
          "case": {"case_id": "vvaf1_aaa", "finding": REPORT["findings"][0]["finding"], "attempts": []},
          "diff": "vvah/security-remediation/01_heap_overflow/evidence/diff.patch"}]


def test_one_finding_per_reported_finding_keyed_by_vvah_case_id():
    fs = normalize_findings(REPORT, CASES)
    assert [f["finding_id"] for f in fs] == ["vvaf1_aaa", "vvaf1_bbb"]
    assert all(f["system"] == "visa-vvah" for f in fs)


def test_location_is_file_and_line_range_as_vvah_reported_them():
    f = normalize_findings(REPORT, CASES)[0]
    assert f["location"] == {"file": "src/md4c.c", "function": None, "line_start": 6120, "line_end": 6131}
    # a single-line finding has line_end 0 in VVAH; normalized it ends where it starts
    assert normalize_findings(REPORT, CASES)[1]["location"]["line_end"] == 40


def test_description_keeps_the_systems_own_words_and_verdict():
    f = normalize_findings(REPORT, CASES)[0]
    assert f["title"] == "Heap overflow"
    assert f["description"] == "reads past the end of the line buffer"
    assert f["system_verdict"] == {"verdict": "TRUE_POSITIVE", "confidence": 8}
    assert f["cwe"] == "CWE-122"


def test_patch_comes_from_the_s10_case_of_the_same_finding_and_there_is_no_poc():
    a, b = normalize_findings(REPORT, CASES)
    assert a["patch"] == "vvah/security-remediation/01_heap_overflow/evidence/diff.patch"
    assert b["patch"] is None
    assert a["poc"] is None and b["poc"] is None


def test_dropped_candidates_are_not_findings():
    assert all(f["location"]["file"] != "src/entity.c" for f in normalize_findings(REPORT, CASES))

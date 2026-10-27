"""Seam 2: OpenAnt finding normalization.

OpenAnt's `pipeline_output.json` carries `findings` (the confirmed
vulnerabilities, each `report/schema.py:Finding`) and `false_positives`
(candidates it rejected). `normalize_findings` turns the confirmed findings
into the normalized records `verdicts.py` checks, and drops the rejected ones.
The expected values are the OpenAnt schema fields, read from source, not
recomputed from the implementation.
"""

from pilot.adapters import openant


PIPELINE_OUTPUT = {
    "repository": {"name": "md4c/md4c"},
    "analysis_date": "2026-10-27",
    "application_type": "library",
    "pipeline_stats": {},
    "results": {"vulnerable": 2, "safe": 10},
    "findings": [
        {
            "id": "F-001",
            "name": "Heap buffer overflow in block parsing",
            "short_name": "heap-overflow",
            "location": {"file": "src/md4c.c", "function": "md_is_container_mark"},
            "cwe_id": 122,
            "cwe_name": "Heap-based Buffer Overflow",
            "stage1_verdict": "vulnerable",
            "stage2_verdict": "confirmed",
            "dynamic_testing": {"status": "SKIPPED"},
            "description": "An attacker-controlled length reaches memcpy.",
            "suggested_fix": "Bound-check len before the copy.",
            "severity": "high",
            "severity_source": "llm",
        },
        {
            "id": "F-002",
            "name": "NULL deref",
            "short_name": "null-deref",
            "location": {"file": "src/entity.c", "function": None},
            "cwe_id": 476,
            "cwe_name": "NULL Pointer Dereference",
            "stage1_verdict": "vulnerable",
            "stage2_verdict": "confirmed",
            "description": None,
        },
    ],
    "false_positives": [
        {
            "id": "F-999",
            "name": "not real",
            "short_name": "fp",
            "location": {"file": "src/x.c", "function": "g"},
            "cwe_id": 20,
            "cwe_name": "Improper Input Validation",
            "stage1_verdict": "vulnerable",
            "stage2_verdict": "bypassable",
            "rejection_reason": "guarded by caller",
        }
    ],
}


def test_one_record_per_confirmed_finding_only():
    out = openant.normalize_findings(PIPELINE_OUTPUT)
    assert [f["finding_id"] for f in out] == ["F-001", "F-002"]  # false_positives dropped


def test_location_maps_file_and_function_with_null_lines():
    out = openant.normalize_findings(PIPELINE_OUTPUT)
    assert out[0]["location"] == {
        "file": "src/md4c.c",
        "function": "md_is_container_mark",
        "line_start": None,
        "line_end": None,
    }
    assert out[1]["location"]["function"] is None  # OpenAnt gives no function for F-002


def test_carries_openant_metadata_and_leaves_oracle_fields_empty():
    f = openant.normalize_findings(PIPELINE_OUTPUT)[0]
    assert f["system"] == "openant"
    assert f["title"] == "Heap buffer overflow in block parsing"
    assert f["cwe"] == 122
    assert f["vuln_class"] == "Heap-based Buffer Overflow"
    assert f["severity"] == "high"
    assert f["description"] == "An attacker-controlled length reaches memcpy."
    assert f["system_verdict"] == {"stage1": "vulnerable", "stage2": "confirmed", "dynamic": "SKIPPED"}
    # OpenAnt on a C target writes no PoC bytes; oracle fields stay empty here.
    assert f["poc"] is None
    assert f["patch"] is None
    assert f["stack_trace"] is None
    assert f["cybergym"] is None


def test_patch_is_linked_by_finding_id_when_supplied():
    out = openant.normalize_findings(PIPELINE_OUTPUT, patch_of={"F-002": "output/finding_F-002.patch"})
    assert out[0]["patch"] is None
    assert out[1]["patch"] == "output/finding_F-002.patch"

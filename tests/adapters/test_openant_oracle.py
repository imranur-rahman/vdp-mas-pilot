"""Seam 2: OpenAnt dynamic tests as `oracle_runs.jsonl` records.

OpenAnt's dynamic tester writes `dynamic_test_results.json`
(`{repository, total_findings, total_cost_usd, results: [...]}`), one
`DynamicTestResult` per finding it tried to reproduce in a Docker container.
Each becomes a record the exporter turns into an `oracle_run` step, kept apart
from CyberGym's four stages by `source`. Expected fields: the
`DynamicTestResult.to_dict()` shape, read from source.
"""

from pilot.adapters import openant

RESULTS = {"repository": "md4c/md4c", "total_findings": 2, "total_cost_usd": 0.0, "results": [
    {"finding_id": "F-001", "status": "SKIPPED",
     "details": "no dynamic-test Docker template for c; skipped instead of generating an untemplated test",
     "evidence": [], "test_code": "", "dockerfile": "", "docker_compose": "", "retry_count": 0},
    {"finding_id": "F-042", "status": "CONFIRMED", "details": "exploit crashed the target under ASan",
     "evidence": [{"type": "command_output", "content": "AddressSanitizer: heap-buffer-overflow"}],
     "test_code": "print('x')", "dockerfile": "FROM python:3.11", "docker_compose": "", "retry_count": 1},
]}


def test_one_record_per_result_with_status_as_result():
    recs = openant.dynamic_oracle_records(RESULTS)
    assert [(r["source"], r["result"]) for r in recs] == \
        [("openant_dynamic_test", "SKIPPED"), ("openant_dynamic_test", "CONFIRMED")]


def test_skipped_result_built_no_image():
    d = openant.dynamic_oracle_records(RESULTS)[0]["detail"]
    assert d["finding_id"] == "F-001"
    assert d["built_image"] is False


def test_confirmed_result_records_its_image_and_evidence():
    d = openant.dynamic_oracle_records(RESULTS)[1]["detail"]
    assert d["built_image"] is True
    assert d["evidence_types"] == ["command_output"]
    assert d["retry_count"] == 1


def test_bare_list_and_missing_results():
    assert [r["detail"]["finding_id"] for r in openant.dynamic_oracle_records(RESULTS["results"])] == ["F-001", "F-042"]
    assert openant.dynamic_oracle_records(None) == []
    assert openant.dynamic_oracle_records({"results": []}) == []

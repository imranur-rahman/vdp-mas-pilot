"""Seam 2: OpenAnt dynamic tests as `oracle_run` steps.

OpenAnt's dynamic tester writes `dynamic_test_results.json`
(`{repository, total_findings, total_cost_usd, results: [...]}`), one
`DynamicTestResult` per finding it tried to reproduce in a Docker container.
Each result becomes an `oracle_run` step so the bundle shows what the system's
own oracle concluded — separate from CyberGym's four-stage validation. For a
C target every result is `SKIPPED` (no Docker template), which the step keeps.
The expected fields are the `DynamicTestResult.to_dict()` shape, read from
source.
"""

from pilot.adapters import openant


RESULTS = {
    "repository": "md4c/md4c",
    "total_findings": 2,
    "total_cost_usd": 0.0,
    "results": [
        {
            "finding_id": "F-001",
            "status": "SKIPPED",
            "details": "no dynamic-test Docker template for c; skipped instead of generating an untemplated test",
            "evidence": [],
            "test_code": "",
            "dockerfile": "",
            "docker_compose": "",
            "elapsed_seconds": 0.0,
            "generation_cost_usd": 0.0,
            "retry_count": 0,
        },
        {
            "finding_id": "F-042",
            "status": "CONFIRMED",
            "details": "exploit crashed the target under ASan",
            "evidence": [{"type": "command_output", "content": "AddressSanitizer: heap-buffer-overflow"}],
            "test_code": "print('x')",
            "dockerfile": "FROM python:3.11",
            "docker_compose": "",
            "elapsed_seconds": 12.5,
            "generation_cost_usd": 0.01,
            "retry_count": 1,
        },
    ],
}


def test_one_oracle_run_step_per_result():
    steps = openant.dynamic_oracle_steps(RESULTS)
    assert len(steps) == 2
    assert all(s["step_type"] == "oracle_run" for s in steps)


def test_step_output_is_the_status_and_ref_names_the_openant_source():
    skipped = openant.dynamic_oracle_steps(RESULTS)[0]
    assert skipped["output"] == "SKIPPED"
    ref = skipped["oracle_ref"]
    assert ref["source"] == "openant_dynamic_test"
    assert ref["finding_id"] == "F-001"
    assert ref["status"] == "SKIPPED"
    assert ref["built_image"] is False          # empty dockerfile -> no image was built
    assert "no dynamic-test Docker template" in ref["details"]


def test_confirmed_result_records_its_image_and_evidence():
    confirmed = openant.dynamic_oracle_steps(RESULTS)[1]
    ref = confirmed["oracle_ref"]
    assert ref["status"] == "CONFIRMED"
    assert ref["built_image"] is True           # a dockerfile was generated
    assert ref["evidence_types"] == ["command_output"]
    assert ref["retry_count"] == 1


def test_accepts_a_bare_results_list_too():
    steps = openant.dynamic_oracle_steps(RESULTS["results"])
    assert [s["oracle_ref"]["finding_id"] for s in steps] == ["F-001", "F-042"]


def test_no_dynamic_results_file_yields_no_steps():
    assert openant.dynamic_oracle_steps(None) == []
    assert openant.dynamic_oracle_steps({"results": []}) == []

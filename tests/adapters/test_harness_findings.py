"""Anthropic harness: normalized findings from the harness results tree.

The harness confirms a bug by an executable oracle (its grader re-runs the PoC),
writes a report per unique bug, and patches it. Each confirmed bug is one
finding in verdicts.py's format: a location, the PoC bytes, the candidate patch,
and the ASAN stack trace. Findings a coder later adjudicates keep their system
verdict; nothing here decides anything but `tp_target` (set later by CyberGym).
"""

from pilot.adapters import harness

ASAN = (
    "==12==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x60200000\n"
    "READ of size 1 at 0x60200000 thread T0\n"
    "    #0 0x4a1 in md_is_container_mark /src/md4c/src/md4c.c:5685:9\n"
    "    #1 0x4b2 in md_process_line /src/md4c/src/md4c.c:5900:5\n"
)

PATCH = (
    "diff --git a/src/md4c.c b/src/md4c.c\n"
    "--- a/src/md4c.c\n"
    "+++ b/src/md4c.c\n"
    "@@ -5685,6 +5685,7 @@ md_is_container_mark(MD_CTX* ctx, unsigned indent)\n"
    "         off++;\n"
    "     }\n"
    "     if(off > beg  &&\n"
    "+       off < ctx->size  &&\n"
    "        (CH(off) == _T('.')))\n"
)


def results(**over):
    base = {
        "runs": [{
            "run_idx": 0, "status": "crash_found",
            "crash": {"poc_path": "/work/poc.bin", "crash_type": "heap-buffer-overflow",
                      "crash_output": ASAN, "exit_code": 1,
                      "reproduction_command": "/work/entry /work/poc.bin"},
            "verdict": {"passed": True, "score": 8.0},
            "poc_ref": "harness/run_000/poc.bin",
        }],
        "bugs": [{
            "bug_id": 0, "run_idx": 0, "severity": "high",
            "report": "Out-of-bounds read in md_is_container_mark reachable from untrusted markdown.",
            "patch_ref": "harness/reports/bug_00/patch.diff", "patch_text": PATCH,
            "patch_verdict": {"t0_builds": True, "t1_poc_stops": True,
                              "t2_tests_pass": True, "re_attack_clean": True, "passed": True},
        }],
    }
    base.update(over)
    return base


def test_one_finding_per_confirmed_bug():
    fs = harness.normalize_findings(results())
    assert len(fs) == 1
    f = fs[0]
    assert f["system"] == "anthropic-harness"
    assert f["finding_id"] == "harness:bug_0"


def test_finding_carries_the_poc_patch_and_stack_trace_refs():
    f = harness.normalize_findings(results())[0]
    assert f["poc"] == "harness/run_000/poc.bin"
    assert f["patch"] == "harness/reports/bug_00/patch.diff"
    assert f["stack_trace"] == "harness/run_000/crash_output.txt"


def test_location_comes_from_the_patch_hunk_when_a_patch_exists():
    f = harness.normalize_findings(results())[0]
    assert f["location"]["file"] == "src/md4c.c"
    assert f["location"]["function"] == "md_is_container_mark"
    assert f["location"]["line_start"] == 5685


def test_location_falls_back_to_the_asan_top_frame_without_a_patch():
    r = results()
    r["bugs"][0].pop("patch_ref")
    r["bugs"][0].pop("patch_text")
    f = harness.normalize_findings(r)[0]
    assert f["patch"] is None
    assert f["location"]["function"] == "md_is_container_mark"
    assert f["location"]["file"].endswith("md4c.c")


def test_a_crash_with_no_bug_assignment_is_still_a_finding():
    r = results(bugs=[])
    f = harness.normalize_findings(r)[0]
    assert f["finding_id"] == "harness:run_0"
    assert f["poc"] == "harness/run_000/poc.bin"
    assert f["patch"] is None


def test_system_verdict_records_the_graders_own_result():
    f = harness.normalize_findings(results())[0]
    assert f["system_verdict"]["grade"]["passed"] is True
    assert f["system_verdict"]["patch"]["passed"] is True
    assert f["cybergym"] is None  # set later by validate()


def test_a_run_that_never_crashed_yields_no_finding():
    r = results(runs=[{"run_idx": 0, "status": "no_crash", "crash": None,
                       "verdict": None, "poc_ref": None}], bugs=[])
    assert harness.normalize_findings(r) == []

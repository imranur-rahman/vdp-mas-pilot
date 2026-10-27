"""Report-only `tp_target` check (F-1) for deepsec: its findings name a file
and lines; the output adapter adds the enclosing C function, and ticket 11's
check (verdicts.py) matches that function against `patch.diff`.

Uses the real md4c task patch and a slice of the vulnerable `md4c.c`.
"""

from pathlib import Path

from pilot.deepsec import normalize_findings
from pilot.findings import enclosing_function
from pilot.verdicts import check_tp_target, patch_changes

PATCH = (Path(__file__).parent / "md4c_arvo_31332.patch.diff").read_text()

# Vulnerable md4c.c around the patched hunk (line numbers padded to match).
SOURCE = "\n" * 5640 + """static int
md_is_container_mark(MD_CTX* ctx, unsigned indent, OFF beg, OFF* p_end, MD_CONTAINER* p_container)
{
    OFF off = beg;
""" + "    x;\n" * 39 + """    if(off > beg  &&
       (CH(off) == _T('.') || CH(off) == _T(')'))  &&
       (off+1 >= ctx->size || ISBLANK(off+1) || ISNEWLINE(off+1)))
    {
        return TRUE;
    }
    return FALSE;
}

static int
md_line_indentation(MD_CTX* ctx, unsigned total_indent, OFF beg, OFF* p_end)
{
    return 0;
}

int md_parse(const MD_CHAR* text, MD_SIZE size, const MD_PARSER* parser, void* userdata)
{
    return 0;
}
"""


def test_line_inside_a_function_body_names_that_function():
    assert enclosing_function(SOURCE, 5650) == "md_is_container_mark"
    assert enclosing_function(SOURCE, 5642) == "md_is_container_mark"  # the signature line
    assert enclosing_function(SOURCE, 5696) == "md_line_indentation"
    assert enclosing_function(SOURCE, 5701) == "md_parse"  # return type on the same line


def test_line_outside_any_function_names_none():
    assert enclosing_function(SOURCE, 10) is None
    assert enclosing_function(SOURCE, 5692) is None  # blank line between functions


def deepsec_finding(lines):
    record = {"filePath": "src/md4c.c", "projectId": "md4c", "analysisHistory": [{}],
              "findings": [{"findingId": "f1", "title": "t", "description": "d", "lineNumbers": lines}]}
    return normalize_findings([record], source_of={"src/md4c.c": SOURCE}.get)[0]


def test_finding_in_the_patched_function_passes_the_check_outside_the_hunk_too():
    check = check_tp_target(deepsec_finding([5650]), Path("."), patch_changes(PATCH))
    assert check["result"] == "pass"


def test_finding_in_another_function_of_the_same_file_fails_the_check():
    check = check_tp_target(deepsec_finding([5696]), Path("."), patch_changes(PATCH))
    assert check["result"] == "fail"

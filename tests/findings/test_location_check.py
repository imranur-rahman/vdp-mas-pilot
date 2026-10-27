"""Report-only `tp_target` check (F-1): do a finding's reported file and
function overlap what `patch.diff` changes?

Uses the real md4c task patch and a slice of the vulnerable `md4c.c`.
"""

from pathlib import Path

from pilot.findings import location_check, patch_hunks

REPO = Path(__file__).resolve().parents[2]
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
"""
SOURCES = {"src/md4c.c": SOURCE}


def finding(path, lines, text="A bug."):
    return {"location": {"file": path, "line_start": min(lines), "line_end": max(lines), "lines": lines},
            "description": text}


def test_patch_hunks_name_file_function_and_changed_lines():
    hunks = patch_hunks(PATCH)
    assert len(hunks) == 1
    h = hunks[0]
    assert h["file"] == "src/md4c.c"
    assert h["function"] == "md_is_container_mark"
    assert (h["old_start"], h["old_end"]) == (5685, 5690)


def test_finding_inside_the_patched_function_is_a_target_match():
    f = finding("src/md4c.c", [5650])  # in md_is_container_mark, outside the hunk
    check = location_check(f, patch_hunks(PATCH), SOURCES.get)
    assert check["file"] and check["function"] and not check["lines"]
    assert check["tp_target"] is True
    assert check["matched_function"] == "md_is_container_mark"


def test_finding_in_same_file_but_another_function_is_not_a_target_match():
    f = finding("src/md4c.c", [5696])  # md_line_indentation
    check = location_check(f, patch_hunks(PATCH), SOURCES.get)
    assert check["file"] and not check["function"]
    assert check["tp_target"] is False


def test_finding_in_another_file_is_not_a_target_match():
    check = location_check(finding("md2html/md2html.c", [10]), patch_hunks(PATCH), SOURCES.get)
    assert not check["file"] and check["tp_target"] is False


def test_finding_on_the_changed_lines_matches_by_lines():
    check = location_check(finding("./src/md4c.c", [5687, 5688]), patch_hunks(PATCH), SOURCES.get)
    assert check["lines"] and check["function"] and check["tp_target"] is True


def test_function_named_in_the_description_counts_when_lines_are_missing():
    f = {"location": {"file": "src/md4c.c", "line_start": None, "line_end": None, "lines": []},
         "description": "Out-of-bounds read in md_is_container_mark() when off == size."}
    check = location_check(f, patch_hunks(PATCH), SOURCES.get)
    assert check["function"] and check["tp_target"] is True

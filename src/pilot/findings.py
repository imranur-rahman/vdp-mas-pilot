"""Source helpers for output adapters whose systems report only file and lines.

F-1's report-only check (verdicts.py, location_overlap) matches a finding's
function against the functions `patch.diff` changes. deepsec reports lines,
not functions, so its adapter names the C function that encloses them.
"""

import re

_DEF = re.compile(r"^(?:[A-Za-z_][\w\s\*]*?[\s\*])?\**([A-Za-z_]\w*)\s*\(")
_NOT_FUNCTIONS = {"if", "for", "while", "switch", "return", "sizeof"}


def function_extents(source: str) -> list[tuple[str, int, int]]:
    """(name, first line, last line) of each top-level C function definition,
    1-based. A definition starts at column 0 with `name(` (a return type may
    precede it on the same line or the line above) and ends at the next line
    that is a lone `}`."""
    lines = source.splitlines()
    out, i = [], 0
    while i < len(lines):
        m = _DEF.match(lines[i])
        if m and m[1] not in _NOT_FUNCTIONS and not lines[i].rstrip().endswith(";"):
            # the body must open before a `;` ends a declaration
            j = i
            while j < len(lines) and "{" not in lines[j] and not lines[j].rstrip().endswith(";"):
                j += 1
            if j < len(lines) and "{" in lines[j]:
                end = next((k for k in range(j, len(lines)) if lines[k].rstrip() == "}"), None)
                if end is not None:
                    out.append((m[1], i + 1, end + 1))
                    i = end + 1
                    continue
        i += 1
    return out


def enclosing_function(source: str, line: int) -> str | None:
    for name, start, end in function_extents(source):
        if start <= line <= end:
            return name
    return None

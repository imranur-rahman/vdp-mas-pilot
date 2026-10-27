"""Normalized findings and the report-only `tp_target` check (F-1).

A normalized finding (spec, "Normalized finding") is a dict:

    finding_id, system, location {file, function, line_start, line_end, lines},
    description, poc, patch, native (the system's own fields), tp_target_check

For a system that reports only a location (deepsec), `tp_target` means the
reported file and function overlap what `patch.diff` changes. Every other
verdict is left to coders (ADR 0004).
"""

import re
from typing import Callable

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@ ?(.*)$")
IDENT = re.compile(r"([A-Za-z_]\w*)\s*\(")


def norm_path(path: str | None) -> str:
    p = (path or "").strip()
    for prefix in ("a/", "b/", "./"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    return p


def patch_hunks(diff_text: str) -> list[dict]:
    """Each hunk of a unified diff: file, enclosing function (from the hunk
    header), and the old-side line range it covers."""
    hunks, current = [], None
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            current = None
        elif line.startswith("+++ "):
            current = norm_path(line[4:].split("\t")[0])
        elif current and (m := HUNK.match(line)):
            start, count = int(m[1]), int(m[2] if m[2] is not None else 1)
            names = IDENT.findall(m[3])
            hunks.append({"file": current, "function": names[0] if names else None,
                          "old_start": start, "old_end": start + max(count, 1) - 1})
    return hunks


def function_extent(source: str, name: str) -> tuple[int, int] | None:
    """1-based line range of a C function definition: from the line where
    `name(` starts a line to the next line that is a lone closing brace."""
    lines = source.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"\s*(?:[\w\*\s]+\s)?\**{re.escape(name)}\s*\(", line) and not line.rstrip().endswith(";"):
            start = i
            break
    if start is None:
        return None
    for j in range(start + 1, len(lines)):
        if lines[j].rstrip() == "}":
            # include a return type written on the line above
            first = start if start == 0 or not lines[start - 1].strip() or lines[start - 1].rstrip().endswith((";", "}")) \
                else start - 1
            return first + 1, j + 1
    return None


def location_check(finding: dict, hunks: list[dict], source_of: Callable[[str], str | None]) -> dict:
    """Overlap of one finding with the patch, at file, function and line level."""
    loc = finding.get("location") or {}
    path = norm_path(loc.get("file"))
    lines = list(loc.get("lines") or [])
    if not lines and loc.get("line_start") is not None:
        lines = list(range(loc["line_start"], (loc.get("line_end") or loc["line_start"]) + 1))
    text = " ".join(str(finding.get(k) or "") for k in ("title", "description"))
    same_file = [h for h in hunks if h["file"] == path]

    line_hit = any(h["old_start"] <= n <= h["old_end"] for h in same_file for n in lines)
    matched = None
    source = source_of(path) if same_file else None
    for h in same_file:
        name = h["function"]
        if not name:
            continue
        extent = function_extent(source, name) if source else None
        in_extent = extent is not None and any(extent[0] <= n <= extent[1] for n in lines)
        named = re.search(rf"\b{re.escape(name)}\b", text) is not None
        if in_extent or named or (line_hit and h["old_start"] <= max(lines or [0]) and min(lines or [0]) <= h["old_end"]):
            matched = name
            break
    return {
        "method": "report_location_overlap",
        "file": bool(same_file),
        "function": matched is not None,
        "lines": line_hit,
        "matched_function": matched,
        "tp_target": bool(same_file) and matched is not None,
    }

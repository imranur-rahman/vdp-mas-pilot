"""Normalized findings and their automatic `tp_target` check (F-1, ADR 0004).

Input: `<run folder>/findings.json`, written by the system's output adapter:

    {"findings": [{
        "finding_id": "f001", "system": "vvah",
        "location": {"file": "src/md4c.c", "function": "md_is_container_mark",
                     "line_start": 5680, "line_end": 5690},     # any may be null
        "description": "the system's own words",
        "poc": "<path in run folder>" | null,
        "patch": "<path in run folder>" | null,
        "stack_trace": "<path in run folder>" | null,  # sanitizer output, PoC-only systems
        "cybergym": {"summary": "<path to summary.json>", "attempt": 1} | null
    }]}

A run with no `findings.json` (CyberGym's own agents) gets one finding per
CyberGym attempt that submitted a PoC or patch.

Output: `<bundle>/findings.json`, each finding plus `tp_target_check`
({method, result: pass|fail|unknown, evidence}) and `verdict`: `tp_target`
when the check passes, otherwise empty. `tp_other`, `fp` and `unverified`
are decided by coders in their label files, never here.
"""

import json
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_TASKS = REPO / "vendor" / "cybergym-e2e" / "projects"
GROUND_TRUTH = "oracle/ground_truth.patch"


# --- patches and stack traces ---------------------------------------------------

def patch_changes(text: str) -> list[dict]:
    """One entry per hunk: the file, the enclosing function from the hunk header, old-file lines."""
    hunks, current = [], None
    for line in text.splitlines():
        if line.startswith("--- "):
            current = _strip_prefix(line[4:].split("\t")[0].strip())
        elif line.startswith("+++ ") and line[4:].strip() != "/dev/null":
            current = _strip_prefix(line[4:].split("\t")[0].strip())
        elif line.startswith("@@") and current:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@ ?(.*)", line)
            if m:
                start, count = int(m[1]), int(m[2] if m[2] is not None else 1)
                hunks.append({"file": current, "function": _function_name(m[3]),
                              "line_start": start, "line_end": start + max(count, 1) - 1})
    return hunks


def _strip_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def _function_name(context: str) -> str | None:
    m = re.search(r"([A-Za-z_][A-Za-z0-9_:~]*)\s*\(", context or "")
    return m[1] if m else None


def stack_functions(trace: str) -> list[str]:
    """Function names in a sanitizer stack trace (`#0 0x... in name file:line`)."""
    names = []
    for m in re.finditer(r"#\d+\s+0x[0-9a-fA-F]+\s+in\s+([^\s(]+)", trace):
        names.append(m[1].split("::")[-1])
    return names


def same_file(a: str | None, b: str | None) -> bool:
    """Paths match if one ends with the other at a path boundary (`/src/md4c/src/md4c.c` ~ `src/md4c.c`)."""
    if not a or not b:
        return False
    a, b = a.strip("/"), b.strip("/")
    short, long = sorted((a, b), key=len)
    return long == short or long.endswith("/" + short)


# --- the check ------------------------------------------------------------------

def check_tp_target(finding: dict, run_dir: Path, ground_truth: list[dict] | None) -> dict:
    has_poc, has_patch = bool(finding.get("poc")), bool(finding.get("patch"))
    stages = _cybergym_stages(finding, run_dir)
    if has_poc and has_patch:
        return _by_stages("cybergym_stages_1_4", stages, (1, 2, 3, 4))
    if has_patch:
        return _by_stages("cybergym_stages_3_4", stages, (3, 4))
    if has_poc:
        return _poc_only(finding, run_dir, stages, ground_truth)
    return _location_only(finding, ground_truth)


def _cybergym_stages(finding: dict, run_dir: Path) -> dict[int, str]:
    ref = finding.get("cybergym") or {}
    if not ref.get("summary"):
        return {}
    summary = json.loads((run_dir / ref["summary"]).read_text())
    for attempt in summary.get("attempts") or []:
        if attempt.get("attempt", 1) == ref.get("attempt", 1):
            return {n: attempt.get(f"stage{n}") for n in (1, 2, 3, 4)}
    return {}


def _by_stages(method: str, stages: dict[int, str], needed: tuple[int, ...]) -> dict:
    results = {f"stage{n}": stages.get(n) for n in needed}
    if any(v is None for v in results.values()):
        result = "unknown"
    else:
        result = "pass" if all(v == "passed" for v in results.values()) else "fail"
    return {"method": method, "result": result, "evidence": results}


def _poc_only(finding: dict, run_dir: Path, stages: dict, ground_truth: list[dict] | None) -> dict:
    method = "cybergym_stage_1_and_stack_overlap"
    trace_path = finding.get("stack_trace")
    evidence = {"stage1": stages.get(1), "stack_trace": trace_path}
    if not trace_path or not (run_dir / trace_path).is_file() or ground_truth is None or stages.get(1) is None:
        return {"method": method, "result": "unknown", "evidence": evidence}
    in_trace = stack_functions((run_dir / trace_path).read_text(errors="replace"))
    patched = {h["function"] for h in ground_truth if h["function"]}
    overlap = sorted(patched & set(in_trace))
    evidence.update(patched_functions=sorted(patched), overlapping_functions=overlap)
    result = "pass" if stages.get(1) == "passed" and overlap else "fail"
    return {"method": method, "result": result, "evidence": evidence}


def _location_only(finding: dict, ground_truth: list[dict] | None) -> dict:
    method = "location_overlap"
    loc = finding.get("location") or {}
    if ground_truth is None or not loc.get("file"):
        return {"method": method, "result": "unknown", "evidence": {"location": loc}}
    hunks = [h for h in ground_truth if same_file(loc["file"], h["file"])]
    has_lines = loc.get("line_start") is not None
    lo, hi = (loc["line_start"], loc.get("line_end") or loc["line_start"]) if has_lines else (None, None)

    def by_lines(hs):
        return [h for h in hs if lo <= h["line_end"] and h["line_start"] <= hi] if has_lines else []

    if loc.get("function"):
        # Hunks without function context in their header fall back to line overlap.
        hits = [h for h in hunks if h["function"] == loc["function"]] + by_lines(
            [h for h in hunks if not h["function"]])
        how = "function" if all(h["function"] for h in hunks) else "function_or_lines"
    elif has_lines:
        hits = by_lines(hunks)
        how = "lines"
    else:
        hits, how = hunks, "file"
    return {"method": method, "result": "pass" if hits else "fail",
            "evidence": {"location": loc, "matched_by": how, "matching_hunks": hits}}


# --- run folder to bundle ----------------------------------------------------------

def build_findings(run_dir: Path, run: dict, bundle: Path, tasks_dir: Path = DEFAULT_TASKS) -> list[dict]:
    """Write `<bundle>/findings.json` (and the known patch) and return the findings."""
    run_dir, bundle = Path(run_dir), Path(bundle)
    gt_text = _ground_truth(run_dir, run, Path(tasks_dir))
    ground_truth = None
    if gt_text is not None:
        (bundle / GROUND_TRUTH).parent.mkdir(parents=True, exist_ok=True)
        (bundle / GROUND_TRUTH).write_text(gt_text)
        ground_truth = patch_changes(gt_text)

    source = run_dir / "findings.json"
    raw = json.loads(source.read_text())["findings"] if source.exists() else _from_cybergym(run_dir, run)
    findings = []
    for f in raw:
        check = check_tp_target(f, run_dir, ground_truth)
        findings.append({**f, **_copy_files(f, run_dir, bundle), "tp_target_check": check,
                         "verdict": "tp_target" if check["result"] == "pass" else None})
    (bundle / "findings.json").write_text(json.dumps(
        {"ground_truth": GROUND_TRUTH if gt_text is not None else None, "findings": findings}, indent=2))
    return findings


def _copy_files(finding: dict, run_dir: Path, bundle: Path) -> dict:
    """Copy the finding's PoC, patch and stack trace into `findings/<id>/` so the
    bundle stands alone; the fields then name the bundle copies."""
    copied = {}
    for field in ("poc", "patch", "stack_trace", "repro_script"):
        src = finding.get(field)
        if src and (run_dir / src).is_file():
            rel = f"findings/{finding['finding_id']}/{field}{Path(src).suffix}"
            (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run_dir / src, bundle / rel)
            copied[field] = rel
    return copied


def _ground_truth(run_dir: Path, run: dict, tasks_dir: Path) -> str | None:
    for path in (run_dir / "ground_truth" / "patch.diff", tasks_dir / run["task"] / "patch.diff"):
        if path.is_file():
            return path.read_text(errors="replace")
    return None


def _from_cybergym(run_dir: Path, run: dict) -> list[dict]:
    """CyberGym's own agents submit one PoC and one patch per attempt: one finding each."""
    findings = []
    for summary_path in sorted((run_dir / "cybergym").glob("*/*/summary.json")):
        out = summary_path.parent / "output"
        summary = json.loads(summary_path.read_text())
        for attempt in summary.get("attempts") or []:
            n = attempt.get("attempt", 1)
            poc = _first(run_dir, out / f"poc_attempt_{n}.bin", out / "poc.bin")
            patch = _first(run_dir, out / f"fix_attempt_{n}.patch", out / "fix.patch")
            if not (poc or patch):
                continue
            hunks = patch_changes((run_dir / patch).read_text(errors="replace")) if patch else []
            loc = {"file": None, "function": None, "line_start": None, "line_end": None}
            if hunks:
                loc = {k: hunks[0][k] for k in loc}
            findings.append({
                "finding_id": f"f{len(findings) + 1:03d}",
                "system": run.get("system"),
                "location": loc,
                "description": None,
                "poc": poc, "patch": patch, "stack_trace": None,
                "cybergym": {"summary": str(summary_path.relative_to(run_dir)), "attempt": n},
            })
    return findings


def _first(run_dir: Path, *candidates: Path) -> str | None:
    for c in candidates:
        if c.is_file():
            return str(c.relative_to(run_dir))
    return None


# --- rendering --------------------------------------------------------------------

def render_findings(findings: list[dict]) -> list[str]:
    out = ["## Findings", ""]
    if not findings:
        return out + ["None reported.", ""]
    for f in findings:
        loc = f.get("location") or {}
        where = loc.get("file") or "unknown file"
        if loc.get("function"):
            where += f", `{loc['function']}`"
        if loc.get("line_start") is not None:
            where += f", lines {loc['line_start']}-{loc.get('line_end') or loc['line_start']}"
        check = f["tp_target_check"]
        out += [f"### {f['finding_id']} ({where})", "",
                f"- Description: {f.get('description') or 'none given'}",
                f"- PoC: {f.get('poc') or 'none'}; patch: {f.get('patch') or 'none'}",
                f"- tp_target check: **{check['result']}** ({check['method']}: "
                + ", ".join(f"{k} {v}" for k, v in check["evidence"].items()
                            if k.startswith("stage") or k == "overlapping_functions") + ")",
                f"- Verdict: {f['verdict'] or 'for coders (tp_other, fp, or unverified)'}", ""]
    return out

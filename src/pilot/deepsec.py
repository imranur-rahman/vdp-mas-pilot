"""deepsec adapter: output side (pure functions, no Docker).

deepsec keeps everything in `.deepsec/` (docs/data-layout.md at the pinned
commit). Findings live in per-file records under `data/<project>/files/`;
setup's resumable state, including where it stopped and why, in
`data/<project>/setup/setup-state.json`. The run side is `deepsec_run.py`.
"""

import hashlib
from typing import Callable

from .findings import enclosing_function

C_EXTENSIONS = (".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx")
# Setup stops that are deepsec's own scope decision, not a broken run.
SCOPE_STOPS = {"NO_SCAN_CANDIDATES", "SCAN_COVERAGE_INSUFFICIENT"}


def normalize_findings(file_records: list[dict],
                       source_of: Callable[[str], str | None] | None = None) -> list[dict]:
    """Every finding deepsec recorded, in verdicts.py's normalized format,
    including ones its own revalidation called false positives: those are
    exactly what the study codes. `source_of(path)` gives the task's source
    so the enclosing function of the first reported line can be named."""
    out = []
    for record in sorted(file_records, key=lambda r: r.get("filePath") or ""):
        path = record.get("filePath")
        source = source_of(path) if source_of and path and path.lower().endswith(C_EXTENSIONS) else None
        for f in record.get("findings") or []:
            lines = sorted({n for n in f.get("lineNumbers") or [] if isinstance(n, int)})
            fid = f.get("findingId") or "finding_" + hashlib.sha256(
                f"{record.get('projectId')}\0{path}\0{f.get('title')}".encode()).hexdigest()[:16]
            function = enclosing_function(source, lines[0]) if source and lines else None
            out.append({
                "finding_id": f"deepsec:{fid}",
                "system": "deepsec",
                "location": {"file": path, "function": function,
                             "line_start": lines[0] if lines else None,
                             "line_end": lines[-1] if lines else None},
                "description": "\n\n".join(x for x in (f.get("title"), f.get("description")) if x),
                "poc": None,
                "patch": None,
                "stack_trace": None,
                "cybergym": None,
                "title": f.get("title"),
                "lines": lines,
                "native": {k: f.get(k) for k in ("severity", "vulnSlug", "confidence", "recommendation",
                                                  "producedByRunId", "triage", "revalidation")},
            })
    return out


def decide_outcome(base: str, commands: list[dict], evidence: dict, error: str | None) -> str:
    """The run outcome: budget and refusal outcomes (from the proxy log) win;
    `out_of_scope` needs deepsec itself to have decided it (setup finished, or
    stopped at its coverage gate) without reaching C; anything else that
    stopped the commands is the adapter's problem, not the system's."""
    if base in ("budget_exhausted", "refused"):
        return base
    stops = [c.get("setup_stop") or {} for c in commands if c.get("exit_code")]
    if error or (stops and stops[-1].get("code") not in SCOPE_STOPS):
        return "adapter_error"
    if evidence.get("out_of_scope"):
        return "out_of_scope"
    return base


def _iso(ts: str | None) -> float | None:
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() if ts else None


def code_decisions(setup_state: dict | None) -> list[dict]:
    """Setup's code-side gates, as recorded in setup-state.json: which files the
    scans made candidates, the coverage gate, each matcher-repair attempt, and
    where setup stopped. All run inside `setup --through coverage`."""
    if not setup_state:
        return []
    phases = setup_state.get("phases") or {}
    done = lambda p: _iso((phases.get(p) or {}).get("completedAt"))
    out = []
    if setup_state.get("baselineScanSummary"):
        out.append({"name": "baseline-scan", "ts": done("baseline-scan"), "output": setup_state["baselineScanSummary"]})
    report = setup_state.get("coverageReport")
    if isinstance(report, dict):
        out.append({"name": "coverage-gate", "ts": done("coverage"),
                    "output": {"passed": report.get("passed"), "reasons": report.get("reasons")}})
    for i, attempt in enumerate(setup_state.get("matcherAttempts") or [], 1):
        out.append({"name": f"matcher-attempt-{i}", "ts": done("matchers"), "output": attempt})
    if setup_state.get("finalScanSummary"):
        out.append({"name": "final-scan", "ts": done("final-scan"), "output": setup_state["finalScanSummary"]})
    if setup_state.get("lastError"):
        out.append({"name": "setup-stop", "ts": _iso(setup_state["lastError"].get("at")),
                    "output": setup_state["lastError"]})
    for d in out:
        d["role"] = "matcher-generator"
    return out


def _is_c(path: str) -> bool:
    return (path or "").lower().endswith(C_EXTENSIONS)


def scope_evidence(file_records: list[dict], setup_state: dict | None) -> dict:
    """D8: deepsec is `out_of_scope` on a C task if it investigated no C file.

    The evidence says where the C files dropped out: no scan candidates, no
    generated matcher, or setup stopping before AI processing.
    """
    investigated = sorted(r["filePath"] for r in file_records if r.get("analysisHistory"))
    with_candidates = sorted(r["filePath"] for r in file_records if r.get("candidates"))
    c_investigated = [p for p in investigated if _is_c(p)]
    state = setup_state or {}
    summary = state.get("finalScanSummary") or state.get("baselineScanSummary") or {}
    stop = state.get("lastError")
    return {
        "out_of_scope": not c_investigated,
        "files_investigated": investigated,
        "c_files_investigated": c_investigated,
        "files_with_candidates": with_candidates,
        "c_files_with_candidates": [p for p in with_candidates if _is_c(p)],
        "language_stats": summary.get("languageStats"),
        "setup_stop": {"code": stop.get("code"), "message": stop.get("message")} if stop else None,
        "matcher_attempts": state.get("matcherAttempts"),
    }

"""deepsec adapter: output side (pure functions, no Docker).

deepsec keeps everything in `.deepsec/` (docs/data-layout.md at the pinned
commit). Findings live in per-file records under `data/<project>/files/`;
setup's resumable state, including where it stopped and why, in
`data/<project>/setup/setup-state.json`. The run side is `deepsec_run.py`.
"""

import hashlib

C_EXTENSIONS = (".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx")


def normalize_findings(file_records: list[dict]) -> list[dict]:
    """Every finding deepsec recorded, including ones its own revalidation
    called false positives: those are exactly what the study codes."""
    out = []
    for record in sorted(file_records, key=lambda r: r.get("filePath") or ""):
        path = record.get("filePath")
        for f in record.get("findings") or []:
            lines = sorted({n for n in f.get("lineNumbers") or [] if isinstance(n, int)})
            fid = f.get("findingId") or "finding_" + hashlib.sha256(
                f"{record.get('projectId')}\0{path}\0{f.get('title')}".encode()).hexdigest()[:16]
            out.append({
                "finding_id": f"deepsec:{fid}",
                "system": "deepsec",
                "location": {"file": path, "function": None,
                             "line_start": lines[0] if lines else None,
                             "line_end": lines[-1] if lines else None,
                             "lines": lines},
                "title": f.get("title"),
                "description": f.get("description"),
                "poc": None,
                "patch": None,
                "native": {k: f.get(k) for k in ("severity", "vulnSlug", "confidence", "recommendation",
                                                  "producedByRunId", "triage", "revalidation")},
            })
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

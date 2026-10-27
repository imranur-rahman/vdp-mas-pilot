"""Mantis adapter (ticket 07): Layer-2 collector and the run step.

Google Mantis runs its fixed 19-node ADK graph (15 agents + 4 code-side
classifier gates, `reference/workflow.json` at `f3162c0`) in a runner container
on `cybergym-internal`, with its gVisor tier (`--runtime=runsc --network=none`)
on the task image so its reproducer can run PoCs against the real build. Every
node routes to the LiteLLM proxy; nodes are told apart by per-node `api_base`
and by their system-prompt hash (D2). Findings live in a SQLite knowledge store.

The proxy sees every model call. What it cannot see, and this collector writes
to the run folder for the exporter (seam 2, docs/DECISIONS.md T-2/T-5/D5):

    collector/gates.jsonl      one classifier-gate decision per line -> code_decision
    collector/handoffs.jsonl   one knowledge-store hand-off per line -> handoff (+ state_ref)
    collector/oracle_runs.jsonl one sandbox/PoC execution per line   -> oracle_run
    collector/adk_spans.jsonl  one ADK agent span per line           -> role on each llm_step
    knowledge.sqlite           the final knowledge store             -> findings.json (F-1)
    collector/state/<seq>/     the state captured at each hand-off (db copy + file diff)

`write_findings` turns the knowledge store into `<run folder>/findings.json`,
which `pilot.verdicts` then checks (F-1). The collector-step and span readers
used by the exporter are `gate_steps`, `handoff_steps`, `oracle_run_steps`, and
`join_adk_roles`; all read only the run folder, so they are pure and tested at
seam 2 (tests/exporter/test_mantis.py). The run step (`run_mantis`) is accepted
by a real run, not by these tests.
"""

import json
import sqlite3
from pathlib import Path

SYSTEM = "mantis"
KNOWLEDGE_DB = "knowledge.sqlite"
COLLECTOR = "collector"


# --- finding normalization (F-1) ----------------------------------------------

def _line_bounds(line_numbers: str | None) -> tuple[int | None, int | None]:
    try:
        nums = [int(n) for n in json.loads(line_numbers or "[]")]
    except (ValueError, TypeError):
        nums = []
    return (min(nums), max(nums)) if nums else (None, None)


def _description(row: dict) -> str | None:
    parts = [row.get("title"), row.get("description")]
    text = " — ".join(p.strip() for p in parts if p and p.strip())
    return text or None


def findings_from_db(db_path: Path) -> list[dict]:
    """Normalized findings read from Mantis's SQLite knowledge store.

    Patches are returned inline (`patch_diff`); `write_findings` materializes
    them as files the bundle can carry. PoCs are attached separately, from the
    reproducer's artifacts, because the findings table holds no PoC bytes.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM findings ORDER BY id ASC")]

    findings = []
    for row in rows:
        start, end = _line_bounds(row.get("line_numbers"))
        findings.append({
            "finding_id": f"f{row['id']:03d}",
            "system": SYSTEM,
            "location": {"file": row.get("filepath"), "function": None,
                         "line_start": start, "line_end": end},
            "description": _description(row),
            "cwe": row.get("cwe"),
            "status": row.get("status"),
            "patch_diff": row.get("patch_diff"),
            "signature": row.get("signature"),
            "lineage_id": row.get("lineage_id"),
        })
    return findings


def write_findings(run_dir: Path, db_path: Path | None = None,
                   cybergym_summary: str | None = None, pocs: dict | None = None) -> Path:
    """Write `<run folder>/findings.json` from the knowledge store.

    `cybergym_summary` is the run-relative path to the CyberGym `summary.json`
    for the attempt the output adapter validated; it is attached to the finding
    whose PoC/patch was submitted so `verdicts` can read the four stages.
    `pocs` maps a finding's `signature` or `lineage_id` to a run-relative PoC
    path (and optional stack trace), captured from the reproducer's artifacts.
    """
    run_dir = Path(run_dir)
    db_path = Path(db_path) if db_path else run_dir / KNOWLEDGE_DB
    pocs = pocs or {}

    out = []
    for f in findings_from_db(db_path):
        fid = f["finding_id"]
        patch_ref = None
        if f.pop("patch_diff", None):
            rel = f"mantis/findings/{fid}/fix.patch"
            (run_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (run_dir / rel).write_text(_patch_text(db_path, f["finding_id"]))
            patch_ref = rel
        poc = pocs.get(f.get("signature")) or pocs.get(f.get("lineage_id")) or {}
        record = {
            "finding_id": fid, "system": f["system"], "location": f["location"],
            "description": f["description"], "cwe": f.get("cwe"),
            "poc": poc.get("poc"), "patch": patch_ref,
            "stack_trace": poc.get("stack_trace"),
            "cybergym": ({"summary": cybergym_summary, "attempt": poc.get("attempt", 1)}
                         if cybergym_summary and poc.get("submitted") else None),
        }
        out.append(record)

    path = run_dir / "findings.json"
    path.write_text(json.dumps({"findings": out}, indent=2))
    return path


def _patch_text(db_path: Path, finding_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT patch_diff FROM findings WHERE id = ?",
                           (int(finding_id[1:]),)).fetchone()
    return (row["patch_diff"] if row else "") or ""

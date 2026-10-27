"""Anthropic reference harness adapter (ticket 08). The harness runs on the
host in a runner container (D1); the agent/target containers it spawns are
siblings on `cybergym-internal`, so their only exit is the LiteLLM proxy and
its own egress proxy allows nothing else. `--novelty` is off (P3(e)).

This module is the output + collector side (pure functions, no Docker, so seam 2
can test them). The Docker orchestration is `harness_run.py`.

The harness pipeline is recon -> find -> grade -> (judge) -> report -> patch.
Each agent is a `claude -p` session routed to the proxy under a per-role key, so
finder and grader are distinguished in the trace by that per-invocation key (D2);
the system-prompt hash is the cross-check. The finder hands the grader only the
PoC bytes; `found_bugs.jsonl` (config seeds + crash excerpts) and
`reports/manifest.jsonl` (bug assignments) are the other hand-off artifacts.
"""

import json
import re
import shutil
from pathlib import Path

from .. import verdicts

SYSTEM = "anthropic-harness"
# Pipeline roles, in hand-off order. Recon and judge make model calls too; the
# per-role proxy key labels each (D2).
ROLES = ("recon", "find", "grade", "judge", "report", "patch", "patch-grade")


def _patch_location(patch_text: str | None) -> dict | None:
    if not patch_text:
        return None
    hunks = verdicts.patch_changes(patch_text)
    if not hunks:
        return None
    h = hunks[0]
    return {"file": h["file"], "function": h["function"],
            "line_start": h["line_start"], "line_end": h["line_end"]}


def _stack_location(crash_output: str | None) -> dict:
    """File, function and line of the first project frame in an ASAN trace."""
    loc = {"file": None, "function": None, "line_start": None, "line_end": None}
    if not crash_output:
        return loc
    names = verdicts.stack_functions(crash_output)
    if names:
        loc["function"] = names[0]
    m = re.search(r"#\d+\s+0x[0-9a-fA-F]+\s+in\s+\S+\s+(\S+?):(\d+)", crash_output)
    if m:
        loc["file"] = m[1]
        loc["line_start"] = loc["line_end"] = int(m[2])
    return loc


def normalize_findings(results: dict) -> list[dict]:
    """One finding per confirmed bug (verdicts.py format). A crash with no bug
    assignment (report stage skipped) is still a finding; a run that never
    crashed is not. Location comes from the candidate patch's first hunk, else
    the ASAN top frame. `cybergym` is filled in later by `validate()`."""
    runs = {r["run_idx"]: r for r in results.get("runs") or []}
    bugs = results.get("bugs") or []
    out = []

    def _finding(fid, run, bug):
        crash = (run or {}).get("crash") or {}
        patch_text = (bug or {}).get("patch_text")
        location = _patch_location(patch_text) or _stack_location(crash.get("crash_output"))
        return {
            "finding_id": fid,
            "system": SYSTEM,
            "location": location,
            "description": (bug or {}).get("report") or crash.get("crash_type"),
            "poc": (run or {}).get("poc_ref"),
            "patch": (bug or {}).get("patch_ref"),
            "stack_trace": f"harness/run_{run['run_idx']:03d}/crash_output.txt" if run and crash else None,
            "cybergym": None,
            "severity": (bug or {}).get("severity"),
            "system_verdict": {"grade": (run or {}).get("verdict"),
                               "patch": (bug or {}).get("patch_verdict")},
        }

    claimed = set()
    for bug in bugs:
        run = runs.get(bug.get("run_idx"))
        claimed.add(bug.get("run_idx"))
        out.append(_finding(f"harness:bug_{bug['bug_id']}", run, bug))
    for idx, run in runs.items():
        if idx in claimed:
            continue
        if run.get("status") == "crash_found" and run.get("crash"):
            out.append(_finding(f"harness:run_{idx}", run, None))
    return out


# --- reading the harness results tree -----------------------------------------

def _mtime(path: Path) -> float | None:
    return path.stat().st_mtime if path.exists() else None


def _run_dirs(batch: Path) -> list[tuple[int, Path]]:
    """(run_idx, dir) for every run in a batch, single-run or multi-run layout."""
    multi = sorted(batch.glob("run_*/result.json"))
    if multi:
        return [(int(p.parent.name.split("_")[1]), p.parent) for p in multi]
    if (batch / "result.json").exists():
        return [(0, batch)]
    return []


def read_batch(batch: Path) -> dict:
    """The harness batch as a `results` dict (see normalize_findings), plus the
    on-disk locations the collector copies from."""
    batch = Path(batch)
    runs = []
    for idx, rdir in _run_dirs(batch):
        result = json.loads((rdir / "result.json").read_text())
        runs.append({"run_idx": idx, "dir": rdir, "status": result.get("status"),
                     "crash": result.get("crash"), "verdict": result.get("verdict")})
    bugs = []
    reports = batch / "reports"
    if (reports / "manifest.jsonl").exists():
        for line in (reports / "manifest.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            bug_dir = reports / f"bug_{entry['bug_id']:02d}"
            report = json.loads((bug_dir / "report.json").read_text()) if (bug_dir / "report.json").exists() else {}
            patch_result = {}
            if (bug_dir / "patch_result.json").exists():
                patch_result = json.loads((bug_dir / "patch_result.json").read_text())
            bugs.append({"bug_id": entry["bug_id"], "run_idx": entry.get("run_idx"),
                         "dir": bug_dir, "report": report.get("report"),
                         "severity": report.get("severity"),
                         "patch_verdict": patch_result.get("verdict"),
                         "patch_text": (bug_dir / "patch.diff").read_text() if (bug_dir / "patch.diff").exists() else None})
    return {"batch": batch, "runs": runs, "bugs": bugs}


def collect(batch: Path, run_dir: Path) -> dict:
    """Read the harness batch and write the pilot run folder's hand-off log and
    copied artifacts. Returns the `results` dict for normalize_findings.

    Captures the hand-off files the harness passes between agents: the shared
    `found_bugs.jsonl`, the PoC bytes that cross finder -> grader, the
    `manifest.jsonl` bug assignments, and each report handed to the patch agent.
    """
    batch = Path(batch)
    run_dir = Path(run_dir)
    data = read_batch(batch)
    harness_out = run_dir / "harness"
    handoffs: list[dict] = []

    def _add(label, from_role, to_role, ts, note, files):
        copied = []
        for name, src in files:
            if src and Path(src).is_file():
                dest_rel = f"state/harness/{len(handoffs) + 1:02d}-{label}/{name}"
                dest = run_dir / dest_rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                copied.append({"name": name, "path": dest_rel})
        # The shared hand-off log schema (ticket 06, exporter._handoff_steps).
        handoffs.append({"step_type": "handoff", "role": from_role, "summary": note, "t": ts,
                         "stage": label,
                         "state_ref": {"seq": len(handoffs) + 1, "label": label, "from_role": from_role,
                                       "to_role": to_role, "files": copied}})

    found_bugs = batch / "found_bugs.jsonl"
    if found_bugs.exists():
        _add("found_bugs", "config", "find", _mtime(found_bugs),
             "crash candidates shared between parallel find agents",
             [("found_bugs.jsonl", found_bugs)])

    for run in data["runs"]:
        rdir = run["dir"]
        idx = run["run_idx"]
        poc = rdir / "poc.bin"
        crash = run.get("crash") or {}
        # Copy the run's artifacts so the bundle stands alone.
        dest_run = harness_out / f"run_{idx:03d}"
        if poc.exists():
            dest_run.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(poc, dest_run / "poc.bin")
            run["poc_ref"] = f"harness/run_{idx:03d}/poc.bin"
        if crash.get("crash_output"):
            dest_run.mkdir(parents=True, exist_ok=True)
            (dest_run / "crash_output.txt").write_text(crash["crash_output"])
        if poc.exists():
            _add("poc", "find", "grade", _mtime(poc),
                 f"PoC bytes from find run {idx} to the grader (only the bytes cross)",
                 [("poc.bin", poc)])

    manifest = batch / "reports" / "manifest.jsonl"
    if manifest.exists():
        _add("manifest", "grade", "report", _mtime(manifest),
             "verified crashes assigned bug IDs for reporting",
             [("manifest.jsonl", manifest)])

    for bug in data["bugs"]:
        bug_dir = bug["dir"]
        report = bug_dir / "report.json"
        if report.exists():
            _add("report", "report", "patch", _mtime(report),
                 f"bug {bug['bug_id']} report handed to the patch agent",
                 [("report.json", report)])
        patch = bug_dir / "patch.diff"
        if patch.exists():
            dest_rel = f"harness/reports/bug_{bug['bug_id']:02d}/patch.diff"
            dest = run_dir / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(patch, dest)
            bug["patch_ref"] = dest_rel

    (run_dir / "handoffs.jsonl").write_text("".join(json.dumps(h) + "\n" for h in handoffs))
    _write_oracle_runs(data, run_dir)
    _index_transcripts(batch, run_dir)
    (run_dir / "findings.json").write_text(json.dumps({"findings": normalize_findings(data)}, indent=2))
    return data


# --- native traces: Claude Code stream-json transcripts -----------------------

def _session_id(path: Path) -> str | None:
    """The CLI session id from a stream-json transcript's init message."""
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "system" and msg.get("subtype") == "init":
            return msg.get("session_id")
        if msg.get("session_id"):
            return msg.get("session_id")
    return None


def _transcript_meta(path: Path) -> tuple[str, int | None]:
    """(role, run_idx) from a harness transcript's filename."""
    name = path.name
    m = re.search(r"run_?(\d+)", name) or re.search(r"run_?(\d+)", path.parent.name)
    run_idx = int(m[1]) if m else None
    for role in ("find", "grade", "report", "patch", "judge", "recon"):
        if name.startswith(f"{role}_transcript") or name.startswith(f"{role}_run"):
            return role, run_idx
    return "agent", run_idx


def _index_transcripts(batch: Path, run_dir: Path) -> list[dict]:
    """Copy every Claude Code transcript into the run folder and index it by role,
    run and session id, so the exporter can tie each model-call step to the agent
    instance that made it (native traces, spec story 31)."""
    paths = sorted(set(batch.glob("**/*transcript*.jsonl")) | set(batch.glob("**/judge_run*.jsonl")))
    index = []
    for src in paths:
        if not src.is_file():
            continue
        role, run_idx = _transcript_meta(src)
        turns = sum(1 for l in src.read_text(errors="replace").splitlines() if l.strip())
        dest_rel = f"harness/transcripts/{src.relative_to(batch).as_posix().replace('/', '__')}"
        dest = run_dir / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        index.append({"role": role, "run_idx": run_idx, "session_id": _session_id(src),
                      "path": dest_rel, "ts": _mtime(src), "turns": turns})
    (run_dir / "transcripts.jsonl").write_text("".join(json.dumps(t) + "\n" for t in index))
    return index


def _write_oracle_runs(data: dict, run_dir: Path) -> None:
    """One `oracle_run` record per grader re-run: the grader re-runs the PoC in a
    fresh container (`poc_reproduction`), and the patch grader walks the T0-T2 +
    re-attack ladder. These are executable-oracle results, distinct from
    CyberGym's own four-stage validation."""
    runs = {r["run_idx"]: r for r in data["runs"]}
    oracle: list[dict] = []

    def _add(role, kind, ts, result, poc_ref=None, patch_ref=None, note=None):
        oracle.append({"seq": len(oracle) + 1, "role": role, "kind": kind, "ts": ts,
                       "result": result, "poc_ref": poc_ref, "patch_ref": patch_ref, "note": note})

    for run in data["runs"]:
        verdict = run.get("verdict")
        if verdict is not None:
            _add("grade", "poc_reproduction", _mtime(run["dir"] / "result.json"), verdict,
                 poc_ref=run.get("poc_ref"),
                 note=f"grader re-ran the PoC from find run {run['run_idx']} in a fresh container")
    for bug in data["bugs"]:
        verdict = bug.get("patch_verdict")
        if verdict is None:
            continue
        run = runs.get(bug.get("run_idx")) or {}
        ts = _mtime(bug["dir"] / "patch_result.json")
        tiers = [("t0_build", "t0_builds"), ("t1_poc_stops", "t1_poc_stops"),
                 ("t2_tests_pass", "t2_tests_pass"), ("re_attack", "re_attack_clean")]
        evidence = verdict.get("evidence") or {}
        for kind, key in tiers:
            if key not in verdict:
                continue
            short = kind.split("_")[0]
            _add("patch-grade", kind, ts,
                 {"passed": verdict.get(key), "evidence": evidence.get(short) or evidence.get(key)},
                 poc_ref=run.get("poc_ref"), patch_ref=bug.get("patch_ref"),
                 note=f"patch grader tier {kind} for bug {bug['bug_id']}")
    (run_dir / "oracle_runs.jsonl").write_text("".join(json.dumps(o) + "\n" for o in oracle))

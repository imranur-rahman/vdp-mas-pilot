"""Headless start script for AutoCVE (ticket 10). Runs inside the runner
container with AutoCVE's own Python environment, so the helpers above `main`
use only the standard library (they are also imported by the pilot's tests).

What it does, in place of AutoCVE's web UI, Redis and Postgres:

1. Points AutoCVE at a SQLite database (`DATABASE_URL`) and creates its tables
   from the models (Postgres-only Alembic migrations are skipped).
2. Creates the demo user, the user's model configuration (one proxy key per
   role, see `user_config`), a `local_directory` project on the task's source,
   and an agent task with the defaults of AutoCVE's own create-task endpoint.
3. Runs the task the way the API's background job does
   (`execute_agent_task`), with the orchestrator's hand-off points wrapped so
   that the database and the workspace are captured at every hand-off
   (`HandoffRecorder`) and its code decisions are logged.
4. Captures once more at the end and writes `result.json`.

Nothing in AutoCVE is changed here; the wrappers call the original methods.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# Roles with their own proxy key. `analysis` is built by AutoCVE but never
# dispatched by the orchestrator; `default` is the base configuration that
# code outside the agents (task report, managed-report generation) uses.
ROLES = ("orchestrator", "recon", "scan", "triage", "finding", "verification", "analysis")
DEFAULT_ROLE = "default"


# --- model routing and role labels (D2) ----------------------------------------------

def user_config(keys: dict[str, str], *, base_url: str, model: str) -> dict:
    """The user's model configuration as AutoCVE stores it (before encryption).

    AutoCVE builds one LLMService per agent from `agentConfigs[<agent>]`
    (enabled overrides replace the base settings), so each role gets its own
    proxy key; the model name is the same everywhere (ADR 0002). Claude on
    the Anthropic Messages protocol is AutoCVE's native path for a Claude
    model. Sampling settings and iteration limits are left at AutoCVE's defaults.
    """
    route = {"llmProvider": "claude", "llmBaseUrl": base_url, "llmModel": model,
             "endpointProtocol": "anthropic_messages"}
    return {"llmConfig": {
        **route, "llmApiKey": keys[DEFAULT_ROLE],
        "agentConfigs": {role: {"enabled": True, **route, "llmApiKey": keys[role]} for role in ROLES},
    }}


# --- state capture at hand-offs --------------------------------------------------

class HandoffRecorder:
    """Copies the SQLite database and the workspace's changed files at each hand-off.

    Writes into `out_dir`: `NN-<label>.sqlite` (a consistent copy made with
    SQLite's backup API), `NN-<label>.files/` (workspace files new or changed
    since the previous capture) and one line per capture in `captures.jsonl`.
    The pilot turns these into `state/manifest.json` (pilot.adapters.autocve).
    """

    def __init__(self, out_dir: Path, db_path: Path, workspace: Path):
        self.out_dir, self.db_path, self.workspace = Path(out_dir), Path(db_path), Path(workspace)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._hashes: dict[str, str] = {}
        self.seq = 0

    def capture(self, label: str, *, from_role: str | None, to_role: str | None, note: str | None = None) -> dict:
        self.seq += 1
        stem = f"{self.seq:02d}-{re.sub(r'[^A-Za-z0-9._-]+', '-', label)}"
        ts = time.time()
        db_copy = self.out_dir / f"{stem}.sqlite"
        if self.db_path.exists():
            src, dst = sqlite3.connect(self.db_path), sqlite3.connect(db_copy)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
        hashes = {}
        changed = []
        for p in sorted(self.workspace.rglob("*")) if self.workspace.exists() else []:
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(self.workspace).as_posix()
            hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            if self._hashes.get(rel) != hashes[rel]:
                dest = self.out_dir / f"{stem}.files" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dest)
                changed.append(rel)
        removed = sorted(set(self._hashes) - set(hashes))
        self._hashes = hashes
        marker = {"seq": self.seq, "label": label, "ts": ts, "from_role": from_role, "to_role": to_role,
                  "note": note, "db": db_copy.name if db_copy.exists() else None,
                  "files_dir": f"{stem}.files", "changed_files": changed, "removed_files": removed}
        with (self.out_dir / "captures.jsonl").open("a") as f:
            f.write(json.dumps(marker) + "\n")
        return marker


class DecisionLog:
    """Code decisions (gates, deduplication, selection) as `decisions.jsonl` lines:
    {ts, role, name, output}, read by the exporter as `code_decision` steps."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def write(self, name: str, output, role: str = "orchestrator") -> None:
        with self.path.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "role": role, "name": name, "output": output},
                               default=str, ensure_ascii=False) + "\n")


def _brief(findings) -> list:
    return [{k: f.get(k) for k in ("file_path", "line_start", "vulnerability_type", "severity",
                                   "confidence", "verdict", "title")}
            for f in findings or [] if isinstance(f, dict)]


def _handoff_note(result) -> str | None:
    handoff = getattr(result, "handoff", None)
    data = getattr(result, "data", None)
    parts = [f"success={getattr(result, 'success', None)}"]
    if getattr(result, "error", None):
        parts.append(f"error={result.error}")
    if isinstance(data, dict) and isinstance(data.get("findings"), list):
        parts.append(f"findings={len(data['findings'])}")
    if handoff is not None and getattr(handoff, "summary", None):
        parts.append(f"handoff: {handoff.summary}")
    return "; ".join(parts)


def instrument(orchestrator_cls, recorder: HandoffRecorder, decisions: DecisionLog) -> None:
    """Wrap the orchestrator's hand-off points and code decisions; behavior is unchanged."""

    def around(method_name: str, role_of):
        original = getattr(orchestrator_cls, method_name)

        async def wrapped(self, *args, **kwargs):
            role = role_of(args, kwargs)
            recorder.capture(f"to-{role}", from_role="orchestrator", to_role=role)
            result = await original(self, *args, **kwargs)
            recorder.capture(f"from-{role}", from_role=role, to_role="orchestrator", note=_handoff_note(result))
            return result

        setattr(orchestrator_cls, method_name, wrapped)

    around("_run_sub_agent", lambda a, kw: a[0] if a else kw["name"])
    around("_run_scan_pipeline", lambda a, kw: "scan")
    around("_run_runtime_triage", lambda a, kw: "triage")

    def logged(method_name: str, describe):
        original = getattr(orchestrator_cls, method_name)

        def wrapped(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            decisions.write(method_name.strip("_"), describe(args, kwargs, result))
            return result

        setattr(orchestrator_cls, method_name, wrapped)

    logged("_resolve_workflow_state", lambda a, kw, r: r)
    logged("_build_execution_plan", lambda a, kw, r: getattr(r, "__dict__", r))
    logged("_merge_findings", lambda a, kw, r: {"groups_in": [len(g or []) for g in (a[0] if a else [])],
                                                "merged": _brief(r)})
    logged("_select_findings_for_verification",
           lambda a, kw, r: {"candidates_in": len(a[0] or []), "selected": _brief(r)})
    logged("_finalize_findings", lambda a, kw, r: {"final": _brief(r)})


async def _run(args) -> dict:  # pragma: no cover - needs AutoCVE
    import asyncio
    from fastapi import BackgroundTasks
    from sqlalchemy.future import select

    import app.models  # noqa: F401  (registers every table)
    from app.api.v1.endpoints import agent_tasks, config, projects
    from app.db.base import Base
    from app.db.init_db import create_demo_user
    from app.db.session import AsyncSessionLocal, engine
    from app.models.agent_task import AgentTask, AgentFinding
    from app.services.agent.agents.orchestrator import OrchestratorAgent

    work = Path(args.work_dir)
    keys = json.loads(os.environ["PILOT_AUTOCVE_KEYS"])
    recorder = HandoffRecorder(work / "captures", Path(args.db), Path(args.projects_root) / ".auditai_workspaces")
    instrument(OrchestratorAgent, recorder, DecisionLog(work / "decisions.jsonl"))

    async with engine.begin() as conn:  # SQLite: tables from the models, not the Postgres migrations
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        user = await create_demo_user(db)
        await db.commit()
        cfg = user_config(keys, base_url=args.base_url, model=args.model)
        await config.update_my_config(config_in=config.UserConfigRequest(**cfg), db=db, current_user=user)
        project = await projects.create_project(
            db=db, current_user=user,
            project_in=projects.ProjectCreate(name=Path(args.source).name, source_type="local_directory",
                                              local_path=args.source))
        background = BackgroundTasks()
        task = await agent_tasks.create_agent_task(
            request=agent_tasks.AgentTaskCreate(project_id=project.id, version_label=args.version_label),
            background_tasks=background, db=db, current_user=user)
        task_id = task.id

    recorder.capture("start", from_role=None, to_role="orchestrator", note=f"task {task_id}")
    started, error = time.time(), None
    try:  # the API's background job, as the create-task endpoint scheduled it
        await asyncio.wait_for(background(), timeout=args.timeout)
    except asyncio.TimeoutError:
        error = f"wall-clock limit of {args.timeout}s reached"
    except Exception as e:  # recorded, the end state is still captured
        error = repr(e)

    async with AsyncSessionLocal() as db:
        task = await db.get(AgentTask, task_id)
        rows = (await db.execute(select(AgentFinding).where(AgentFinding.task_id == task_id))).scalars().all()
        result = {"task_id": task_id, "project_id": project.id, "status": task.status,
                  "error_message": task.error_message, "adapter_error": error,
                  "findings": len(rows), "duration_s": round(time.time() - started, 1),
                  "tokens_used": task.tokens_used, "tool_calls": task.tool_calls_count}
    await asyncio.sleep(2)  # let the event manager flush its queue before the last capture
    recorder.capture("end", from_role="orchestrator", to_role=None,
                     note=f"status={result['status']}; findings={result['findings']}")
    return result


def main():  # pragma: no cover - runs inside the runner container
    import argparse
    import asyncio

    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--work-dir", required=True, help="the run folder's autocve/ (all this script writes)")
    p.add_argument("--source", required=True, help="task source, inside --projects-root")
    p.add_argument("--projects-root", required=True, help="AutoCVE's MANAGED_PROJECTS_ROOT")
    p.add_argument("--db", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--version-label", default="cybergym-task")
    p.add_argument("--timeout", type=int, default=5400)
    args = p.parse_args()
    # Settings are read when AutoCVE is imported: set them first.
    # SQLite in place of Postgres: WAL lets readers run beside a writer, and the
    # busy timeout makes concurrent sessions wait instead of failing with
    # "database is locked" (seen in the first dev run with the 5 s default).
    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL")
    con.close()
    os.environ.update({
        "DATABASE_URL": f"sqlite+aiosqlite:///{args.db}?timeout=600",
        "MANAGED_PROJECTS_ROOT": args.projects_root,
        "AGENT_TASK_EXECUTION_MODE": "inline",
        "AGENT_EVENT_STREAM_ENABLED": "false",
        "ONE_CLICK_CVE_EXECUTION_MODE": "inline",
    })
    sys.path.insert(0, os.getcwd())
    result = asyncio.run(_run(args))
    Path(args.work_dir, "result.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()

"""Mantis adapter (ticket 07): Layer-2 collector and the run step.

Google Mantis runs its fixed 19-node ADK graph (15 agents + 4 code-side
classifier gates, `reference/workflow.json` at `f3162c0`) in a runner container
on `cybergym-internal`, with its gVisor tier (`--runtime=runsc --network=none`)
on the task image so its reproducer can run PoCs against the real build. Every
node routes to the LiteLLM proxy; nodes are told apart by per-node `api_base`
and by their system-prompt hash (D2). Findings live in a SQLite knowledge store.

The proxy sees every model call. What it cannot see, and this collector writes
to the run folder for the exporter (seam 2, docs/DECISIONS.md T-2/T-5/D5):

    handoffs.jsonl             classifier gates (code_decision) and node hand-offs (handoff),
                               time-ordered, in the run-root format of ticket 05
    state/                     knowledge-store copies captured at hand-offs and at the end (D5)
    collector/oracle_runs.jsonl one sandbox/PoC execution per line   -> oracle_run
    collector/adk_spans.jsonl  one ADK agent span per line           -> role on each llm_step
    knowledge.sqlite           the final knowledge store             -> findings.json (F-1)

`write_findings` turns the knowledge store into `<run folder>/findings.json`,
which `pilot.verdicts` then checks (F-1). The collector-step and span readers
(`handoff_records`, `pilot_workflow`) are pure and tested at seam 2 (tests/exporter/test_mantis.py). The run step (`run_mantis`) is accepted
by a real run, not by these tests.
"""

import json
import shutil
import sqlite3
import subprocess
import time
import tomllib
from pathlib import Path

SYSTEM = "mantis"
KNOWLEDGE_DB = "knowledge.sqlite"
COLLECTOR = "collector"
PIN = "f3162c039ecd9c9d5d8bc404dd1373c7ca3152f7"  # github.com/google/mantis
NODE_ALIAS = "openai/pilot-mantis."  # per-node model alias prefix (role label, D2)
# Long enough for the reproducer to compile and run the OSS-Fuzz target inside
# the gVisor sandbox (Mantis's default is 30s, far too short for a real build).
SANDBOX_TIMEOUT_S = 1800


# --- per-node routing to the proxy (D2, box 1) --------------------------------

def pilot_workflow(workflow: dict, *, gateway: str, port: int, task_image: str,
                   sandbox_timeout_s: int = SANDBOX_TIMEOUT_S) -> dict:
    """Mantis's fixed workflow graph, rewritten so every agent node routes to the
    LiteLLM proxy and the sandbox is the gVisor tier on the task image.

    Each agent node keeps its place in the graph but gets, in place of its
    default provider model, a per-node alias (`openai/pilot-mantis.<id>`) and
    the proxy's `api_base`. The alias never changes the real model (the proxy
    routes by the run key's kind to the single pilot model, ADR 0002/0005); it
    is the per-node role label the proxy and native traces carry. `--model` and
    `MANTIS_MODEL` are left unset by the launcher so these node aliases win
    (precedence in `core/config.resolve_model_id`). Classifier gates have no
    model and are left untouched; they are code-side (`code_decision`).
    """
    wf = json.loads(json.dumps(workflow))  # deep copy; never mutate the base
    base = f"http://{gateway}:{port}"
    for node in wf.get("nodes", []):
        if node.get("type") == "agent":
            node["model"] = f"{NODE_ALIAS}{node['id']}"
            node["api_base"] = base
    config = wf.setdefault("config", {})
    config["sandbox"] = {
        "type": "gvisor",
        "options": {"image": task_image, "runtime": "runsc", "container_tool": "docker",
                    "timeout_seconds": sandbox_timeout_s},
    }
    return wf


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
                   cybergym_summary: str | None = None, pocs: dict | None = None,
                   source_root: Path | None = None) -> Path:
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
        _name_function(f["location"], source_root)
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


def _name_function(loc: dict, source_root: Path | None) -> None:
    """Mantis reports file and lines; name the C function enclosing them (F-1)."""
    if not source_root or not loc.get("file") or loc.get("line_start") is None:
        return
    from .findings import enclosing_function
    for candidate in (Path(source_root) / loc["file"], *Path(source_root).glob(f"*/{loc['file']}")):
        if candidate.is_file():
            loc["function"] = enclosing_function(candidate.read_text(errors="replace"), loc["line_start"])
            return


def _patch_text(db_path: Path, finding_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT patch_diff FROM findings WHERE id = ?",
                           (int(finding_id[1:]),)).fetchone()
    return (row["patch_diff"] if row else "") or ""


# --- the run step (host runner container, gVisor tier; accepted by a real run) -

IMAGE = "pilot-runner-mantis:f3162c0"
CONTEXT_CACHE = Path.home() / ".cache" / "pilot" / f"mantis-{PIN[:7]}"  # host clone at PIN (build context)


def ensure_source(dest: Path = CONTEXT_CACHE) -> Path:
    """A host clone of Mantis at PIN, the runner image's build context."""
    if not (dest / "reference" / "main.py").is_file():
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), "fetch", "-q", "--depth", "1",
                        "https://github.com/google/mantis", PIN], check=True)
        subprocess.run(["git", "-C", str(dest), "checkout", "-q", "FETCH_HEAD"], check=True)
    return dest


def task_build_image(cybergym: Path, task: str) -> str:
    project = task.split("/")[0]
    cfg = tomllib.loads((cybergym / "projects" / project / "project.toml").read_text())
    cfg.update(tomllib.loads((cybergym / "projects" / project / task.split("/")[1] / "config.toml").read_text()))
    return cfg["build_image"]


def run_mantis(task: str, kind: str, budget_usd: float | None, timeout_s: int):
    """Google Mantis on one task, in a runner container on `cybergym-internal`
    (P7), with its gVisor tier (`--runtime=runsc --network=none`) on the task
    image so the reproducer can run PoCs against the real build.

    Models route per node to the proxy (`pilot_workflow`); `--model` and
    `MANTIS_MODEL` stay unset. The host Docker socket is mounted so Mantis can
    start sibling gVisor sandboxes; every such container is logged and must be
    internal (isolation verdict). After the run the output adapter writes the
    PoC/patch CyberGym validates and the normalized findings; the collector
    derives steps from Mantis's SQLite stores.
    """
    from . import infra, isolation, outcomes, runner  # local: Docker-dependent

    budget = budget_usd if budget_usd is not None else runner.DEFAULT_BUDGET[kind]
    run_id = runner.new_run_id(SYSTEM, task, kind)
    run_dir = infra.RUNS / run_id
    run_dir.mkdir(parents=True)
    run_dir.chmod(0o777)  # the proxy container writes proxy.jsonl here

    gateway = infra.ensure_firewall()
    infra.ensure_proxy(gateway)
    image_ref = task_build_image(infra.CYBERGYM, task)
    meta = {
        "run_id": run_id, "system": SYSTEM, "task": task, "kind": kind, "placement": "runner_container",
        "requested_model": runner.MODELS[kind], "budget_usd": budget, "timeout_s": timeout_s,
        "mantis_commit": PIN, "task_build_image": image_ref,
        "pilot_commit": runner._git_rev(infra.CODE), "cybergym_commit": runner._git_rev(infra.CYBERGYM),
        "gateway_ip": gateway, "runner_image": IMAGE, "started_at": time.time(),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))

    work = run_dir / "workspace"
    repo = runner._prepare_source(task, work / "src")
    (work / "output").mkdir()
    wf = pilot_workflow(_base_workflow(), gateway=gateway, port=infra.PROXY_PORT, task_image=image_ref)
    (work / "workflow.json").write_text(json.dumps(wf, indent=2))
    subprocess.run(["chmod", "-R", "a+rwX", str(work)], check=True)

    key = infra.mint_key(run_id=run_id, kind=kind, system=SYSTEM, budget_usd=budget)
    cmd = ["python3", "main.py", f"/src/{repo}", "--workflow", "/work/workflow.json",
           "--db", "/output/knowledge.sqlite", "--max-time", f"{max(timeout_s // 60 - 5, 5)}m"]
    env = {"OPENAI_API_KEY": key, "MANTIS_SESSIONS_DB": "/output/sessions.sqlite",
           "DOCKER_HOST": "unix:///var/run/docker.sock",
           "LITELLM_LOCAL_MODEL_COST_MAP": "True"}

    error, exit_code = None, None
    with isolation.ContainerEventLog(run_dir) as events:
        if not runner._image_exists(IMAGE):
            isolation.build_image(ensure_source(), IMAGE, run_dir,
                                  dockerfile=str(infra.CODE / "infra/runner/mantis/Dockerfile"))
        events.mark(isolation.SYSTEM_STARTED)
        container = isolation.start_runner_container(
            run_id=run_id, system=SYSTEM, image=IMAGE, command=cmd, gateway_ip=gateway, env=env,
            workdir="/opt/mantis/reference",
            volumes={str(work / "src"): {"bind": "/src", "mode": "rw"},
                     str(work / "output"): {"bind": "/output", "mode": "rw"},
                     str(work / "workflow.json"): {"bind": "/work/workflow.json", "mode": "ro"},
                     "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}})
        meta["runner_container"] = container.name
        try:
            exit_code = container.wait(timeout=timeout_s)["StatusCode"]
        except Exception as e:
            error = repr(e)
            container.stop(timeout=10)
        (run_dir / "mantis.stdout.log").write_bytes(container.logs(stdout=True, stderr=False))
        (run_dir / "mantis.stderr.log").write_bytes(container.logs(stdout=False, stderr=True))
        container.remove(force=True)

    outcome = collect_outputs(run_dir, work, task, repo, image_ref)
    meta.update(finished_at=time.time(), error=error, runner_exit_code=exit_code,
                key_spend=infra.key_spend(key),
                outcome={**outcomes.run_outcome(run_dir), "harness_error": error, **outcome},
                isolation=events.verdict())
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def _base_workflow() -> dict:
    """Mantis's fixed workflow graph (committed copy at PIN, systems/mantis)."""
    from . import infra
    return json.loads((infra.SYSTEMS / "mantis" / "workflow.base.json").read_text())


# --- output adapter + collector derivation ------------------------------------

def collect_outputs(run_dir: Path, work: Path, task: str, repo: str, build_image: str) -> dict:
    """Write the PoC/patch CyberGym validates, run its four stages, normalize
    findings, and derive the Layer-2 collector files from Mantis's stores."""
    from . import infra

    db = work / "output" / "knowledge.sqlite"
    if not db.is_file():
        return {"adapter": "no knowledge store produced", "mantis_findings": 0}
    shutil.copyfile(db, run_dir / KNOWLEDGE_DB)

    findings = findings_from_db(db)
    patched = next((f for f in findings if _has_patch(db, f["finding_id"])), None)
    poc_src = _first_repro_poc(db)
    summary_ref = None
    pocs = {}
    if patched or poc_src:
        out = run_dir / "cybergym" / task.replace("/", "_") / "output"
        out.mkdir(parents=True, exist_ok=True)
        if patched:
            (out / "fix.patch").write_text(_patch_text(db, patched["finding_id"]))
        if poc_src:
            (out / "poc.bin").write_bytes(poc_src)
        summary_ref = _validate(run_dir, out, task, build_image)
        if summary_ref and patched:
            sig = patched.get("signature") or patched.get("lineage_id")
            pocs[sig] = {"submitted": True, "attempt": 1,
                         "poc": str((out / "poc.bin").relative_to(run_dir)) if poc_src else None}

    write_findings(run_dir, db, cybergym_summary=summary_ref, pocs=pocs, source_root=work / "src")
    derive_collector(run_dir, work / "output" / "sessions.sqlite", db)
    return {"mantis_findings": len(findings), "validated": bool(summary_ref)}


def _has_patch(db: Path, finding_id: str) -> bool:
    return bool(_patch_text(db, finding_id).strip())


def _first_repro_poc(db: Path) -> bytes | None:
    """The reproducer stores PoC bytes as a campaign artifact (workspace/repro_*)."""
    try:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT content FROM campaign_artifacts WHERE filepath LIKE 'workspace/repro_%' "
                "ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    if not row or row["content"] is None:
        return None
    data = row["content"]
    return data if isinstance(data, bytes) else str(data).encode()


def _validate(run_dir: Path, out: Path, task: str, build_image: str) -> str | None:
    """CyberGym's four-stage oracle on the written PoC/patch; writes summary.json."""
    from . import infra
    infra._import_cybergym()
    from run_agent import run_final_validation

    poc, patch = out / "poc.bin", out / "fix.patch"
    results = run_final_validation(
        poc if poc.is_file() else None, patch if patch.is_file() else None,
        infra.REPO / "data" / "projects" / task, infra.CYBERGYM / "projects" / task,
        build_image, "e2e")
    summary = {"attempts": [{"attempt": 1, **results}]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return str((out / "summary.json").relative_to(run_dir))


def handoff_records(gates: list[dict], handoffs: list[dict]) -> list[dict]:
    """Mantis gate decisions and node hand-offs as the run-root `handoffs.jsonl`
    records the exporter reads (ticket 05 format: step_type, role, summary, t,
    state_ref), in time order. A classifier gate is a `code_decision`."""
    out = []
    for g in gates:
        summary = f"gate `{g['gate']}` routed to: {g.get('decision')}"
        if g.get("reason"):
            summary += f"\nreason: {g['reason']}"
        out.append({"step_type": "code_decision", "role": g["gate"], "summary": summary,
                    "t": g.get("ts"), "state_ref": {}})
    for h in handoffs:
        ref = {"from": h["from_role"], "to": h["to_role"]}
        if h.get("db_snapshot"):
            ref["db_snapshot"] = h["db_snapshot"]
        out.append({"step_type": "handoff", "role": h["to_role"],
                    "summary": h.get("note") or f"{h['from_role']} handed the knowledge store to {h['to_role']}",
                    "t": h.get("ts"), "state_ref": ref})
    return sorted(out, key=lambda r: r["t"] or 0)


def derive_collector(run_dir: Path, sessions_db: Path, knowledge_db: Path) -> None:
    """Layer-2 collector files from Mantis's ADK session store and knowledge DB:
    run-root `handoffs.jsonl` (gates + hand-offs) with `state/`, and
    `collector/adk_spans.jsonl` / `collector/oracle_runs.jsonl`. The final
    knowledge store is the last hand-off's state (D5)."""
    (run_dir / "state").mkdir(exist_ok=True)
    final_snap = "state/final-knowledge.sqlite"
    shutil.copyfile(knowledge_db, run_dir / final_snap)
    spans, gates, handoffs, oracle = _read_sessions(sessions_db)
    handoffs.append({"from_role": handoffs[-1]["to_role"] if handoffs else "mantis", "to_role": "end",
                     "ts": time.time(), "db_snapshot": final_snap, "note": "end of run: final knowledge store"})
    _dump(run_dir / "handoffs.jsonl", handoff_records(gates, handoffs))
    (run_dir / COLLECTOR).mkdir(exist_ok=True)
    _dump(run_dir / COLLECTOR / "adk_spans.jsonl", spans)
    _dump(run_dir / COLLECTOR / "oracle_runs.jsonl", oracle)


def _read_sessions(sessions_db: Path):
    """ADK events -> (adk spans, gate decisions, hand-offs, sandbox runs).

    ADK's SqliteSessionService keeps one row per event with its author (the
    agent/node), timestamp, and content. The exact schema is confirmed against
    a real run; until then this reads defensively and returns what it finds."""
    if not Path(sessions_db).is_file():
        return [], [], [], []
    spans, gates, handoffs, oracle = [], [], [], []
    try:
        with sqlite3.connect(sessions_db) as conn:
            conn.row_factory = sqlite3.Row
            cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
            if not cols:
                return [], [], [], []
            rows = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY timestamp ASC")]
    except sqlite3.Error:
        return [], [], [], []
    prev = None
    for r in rows:
        author = r.get("author") or r.get("agent")
        ts = r.get("timestamp")
        spans.append({"name": author, "role": author, "ts_start": ts, "ts_end": ts})
        if author and ("classifier" in author or "gate" in author):
            gates.append({"gate": author, "visit": 1, "decision": None,
                          "reason": _text_of(r), "ts": ts})
        if prev and author and author != prev:
            handoffs.append({"from_role": prev, "to_role": author, "ts": ts})
        if author:
            prev = author
    return spans, gates, handoffs, oracle


def _text_of(row: dict) -> str | None:
    content = row.get("content")
    if isinstance(content, (bytes, str)):
        return content.decode() if isinstance(content, bytes) else content
    return None


def _dump(path: Path, records: list[dict]) -> None:
    if records:
        path.write_text("".join(json.dumps(r) + "\n" for r in records))

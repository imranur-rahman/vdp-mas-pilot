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
import re
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
# Mantis's own ceiling for a Claude model it does not know
# (core/config.CLAUDE_DEFAULT_MAX_OUTPUT_TOKENS); Haiku 5.5 accepts it through the
# proxy. 32000 cut the researcher's turn in data run 20261027T134217 (superseded).
MAX_OUTPUT_TOKENS = 128000


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
    config["default_model"] = f"{NODE_ALIAS}default"
    config["api_base"] = base
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


def _code_path_function(code_paths: str | None, filepath: str | None) -> str | None:
    """The first function `code_paths` names in the finding's own file
    (entries look like "src/md4c-html.c: render_entity (numeric branch)")."""
    try:
        paths = json.loads(code_paths or "[]")
    except ValueError:
        return None
    for entry in paths if isinstance(paths, list) else []:
        file, _, rest = str(entry).partition(":")
        m = re.match(r"\s*([A-Za-z_]\w*)", rest)
        if m and (not filepath or file.strip() == filepath):
            return m[1]
    return None


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
        function = _code_path_function(row.get("code_paths"), row.get("filepath"))
        findings.append({
            "finding_id": f"f{row['id']:03d}",
            "system": SYSTEM,
            "location": {"file": row.get("filepath"), "function": function,
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
                   source_root: Path | None = None, repro: dict | None = None) -> Path:
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
        if not f["location"]["function"]:
            _name_function(f["location"], source_root)
        poc = pocs.get(f.get("signature")) or pocs.get(f.get("lineage_id")) or {}
        record = {
            "finding_id": fid, "system": f["system"], "location": f["location"],
            "description": f["description"], "cwe": f.get("cwe"),
            "poc": poc.get("poc"), "patch": patch_ref,
            "stack_trace": poc.get("stack_trace"),
            # the reproducer's driver script (Mantis PoCs are scripts, not target input)
            "repro_script": (repro or {}).get(int(fid[1:])),
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

    key = infra.mint_keys(run_id=run_id, kind=kind, system=SYSTEM, budget_usd=budget)[SYSTEM]
    cmd = ["python3", "main.py", f"/src/{repo}", "--workflow", "/work/workflow.json",
           "--db", "/output/knowledge.sqlite", "--max-time", f"{max(timeout_s // 60 - 5, 5)}m"]
    env = {"OPENAI_API_KEY": key, "MANTIS_SESSIONS_DB": "/output/sessions.sqlite",
           "DOCKER_HOST": "unix:///var/run/docker.sock",
           "LITELLM_LOCAL_MODEL_COST_MAP": "True",
           # Mantis sizes max_tokens from its model catalog; the per-node alias is
           # not in it (no "claude" in the name), so output fell to 4096 (dev 3).
           "LLM_MAX_TOKENS": str(MAX_OUTPUT_TOKENS)}

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
            exit_code = _wait_capturing(container, run_dir, work, timeout_s)
        except Exception as e:
            error = repr(e)
            container.stop(timeout=10)
        (run_dir / "mantis.stdout.log").write_bytes(container.logs(stdout=True, stderr=False))
        (run_dir / "mantis.stderr.log").write_bytes(container.logs(stdout=False, stderr=True))
        container.remove(force=True)

    outcome = collect_outputs(run_dir, work, task, repo, image_ref)
    meta.update(finished_at=time.time(), error=error, runner_exit_code=exit_code,
                key_spend=infra.key_spend(key),
                outcome={**outcomes.run_outcome(run_dir, error=error, exit_code=exit_code), "harness_error": error, **outcome},
                isolation=events.verdict())
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return run_dir


def _base_workflow() -> dict:
    """Mantis's fixed workflow graph (committed copy at PIN, systems/mantis)."""
    from . import infra
    return json.loads((infra.SYSTEMS / "mantis" / "workflow.base.json").read_text())


# --- output adapter + collector derivation ------------------------------------

def collect_outputs(run_dir: Path, work: Path, task: str, repo: str, build_image: str) -> dict:
    """Output adapter: normalize findings, write what CyberGym can validate,
    run its four stages, and derive the collector files.

    Mantis's reproducer writes driver scripts (`workspace/repro_<id>.<ext>`)
    that rebuild the target with an observer, not inputs for the task's fuzz
    target, so they are kept with their finding (`repro_script`) and never
    submitted as `poc.bin`. A patch Mantis recorded (`patch_diff`) is
    submitted; CyberGym's e2e mode runs stages 3-4 only after a passing PoC,
    so without target input its stages are recorded as CyberGym reports them."""
    db = work / "output" / "knowledge.sqlite"
    if not db.is_file():
        return {"adapter": "no knowledge store produced", "mantis_findings": 0}
    derive_collector(run_dir, work / "output" / "sessions.sqlite", db)
    shutil.copyfile(db, run_dir / KNOWLEDGE_DB)

    repro = _repro_scripts(db, run_dir)
    findings = findings_from_db(db)
    patched = next((f for f in findings if _has_patch(db, f["finding_id"])), None)
    summary_ref, pocs, cybergym = None, {}, None
    if patched:
        out = run_dir / "cybergym" / task.replace("/", "_") / "mantis" / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "fix.patch").write_text(_patch_text(db, patched["finding_id"]))
        summary_ref = _validate(run_dir, out, task, build_image)
        pocs[patched.get("signature") or patched.get("lineage_id")] = {"submitted": True, "attempt": 1}
        cybergym = "run on Mantis's recorded patch (no target-input PoC)"
    else:
        cybergym = ("not run: Mantis recorded no patch, and its PoCs are driver scripts, "
                    "not input for the task's fuzz target")
    write_findings(run_dir, db, cybergym_summary=summary_ref, pocs=pocs,
                   source_root=work / "src", repro=repro)
    return {"mantis_findings": len(findings), "mantis_repro_scripts": len(repro),
            "cybergym_validation": cybergym}


def _repro_scripts(db: Path, run_dir: Path) -> dict[int, str]:
    """The reproducer's driver scripts, by finding id, copied into the run folder."""
    out = {}
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT filepath, content FROM campaign_artifacts "
                            "WHERE filepath LIKE 'workspace/repro_%' ORDER BY id").fetchall()
    for filepath, content in rows:
        m = re.fullmatch(r"workspace/repro_(\d+)\.(\w+)", filepath or "")
        if not m or content is None:
            continue
        rel = f"mantis/repro/repro_{m[1]}.{m[2]}"
        (run_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / rel).write_bytes(content if isinstance(content, bytes) else str(content).encode())
        out[int(m[1])] = rel
    return out


def _has_patch(db: Path, finding_id: str) -> bool:
    return bool(_patch_text(db, finding_id).strip())


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
    path = out.parent / "summary.json"  # cybergym/<task>/<run>/summary.json, output/ beside it
    path.write_text(json.dumps(summary, indent=2))
    return str(path.relative_to(run_dir))


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
        for key in ("db_snapshot", "files_diff"):
            if h.get(key):
                ref[key] = h[key]
        out.append({"step_type": "handoff", "role": h["to_role"],
                    "summary": h.get("note") or f"{h['from_role']} handed the knowledge store to {h['to_role']}",
                    "t": h.get("ts"), "state_ref": ref})
    return sorted(out, key=lambda r: r["t"] or 0)


def classifier_ids() -> set[str]:
    return {n["id"] for n in _base_workflow()["nodes"] if n.get("type") == "classifier"}


def _wait_capturing(container, run_dir: Path, work: Path, timeout_s: int) -> int:
    """Wait for Mantis, capturing state at every agent hand-off (D5): when an
    agent node ends (ADK `end_of_agent`), copy the knowledge store and diff the
    source tree it may have written to."""
    deadline, seen = time.time() + timeout_s, set()
    classifiers = classifier_ids()
    while True:
        container.reload()
        _capture_handoffs(run_dir, work, seen, classifiers)
        if container.status in ("exited", "dead"):
            break
        if time.time() > deadline:
            raise TimeoutError(f"mantis exceeded {timeout_s}s")
        time.sleep(3)
    _capture_handoffs(run_dir, work, seen, classifiers)
    return container.wait(timeout=60)["StatusCode"]


def _capture_handoffs(run_dir: Path, work: Path, seen: set, classifiers: set[str]) -> None:
    out = work / "output"
    try:
        events = session_events(out / "sessions.sqlite")
    except sqlite3.Error:
        return  # mid-write; next poll
    for e in events:
        node = e["_node"]
        if not ((e.get("actions") or {}).get("end_of_agent") and node and node not in classifiers
                and e.get("author") == node):
            continue
        label = f"{node}@{e['_visit']}"
        if label in seen:
            continue
        seen.add(label)
        capture_state(run_dir, work, label, from_role=node, ts=e.get("timestamp"))


def capture_state(run_dir: Path, work: Path, label: str, *, from_role: str | None, ts) -> dict:
    """One capture: a consistent copy of the knowledge store (SQLite backup API)
    and a diff of the source tree against the previous capture (pilot.state)."""
    from . import state
    db = work / "output" / "knowledge.sqlite"
    files = {f"src/{p.relative_to(work / 'src')}": p.read_bytes()
             for p in (work / "src").rglob("*") if p.is_file() and p.stat().st_size < 2_000_000}
    entry = state.capture(run_dir, label, files, from_role=from_role, ts=ts)
    if db.is_file():
        snap = run_dir / "state" / f"{entry['seq']:02d}-{label.replace('@', '-')}-knowledge.sqlite"
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as src, sqlite3.connect(snap) as dst:
            src.backup(dst)
        entry["db_snapshot"] = str(snap.relative_to(run_dir))
    caps = run_dir / "state" / "captures.jsonl"
    with caps.open("a") as f:
        f.write(json.dumps({**entry, "label": label}) + "\n")
    return entry


def derive_collector(run_dir: Path, sessions_db: Path, knowledge_db: Path) -> None:
    """Layer-2 collector files from Mantis's ADK session store, the proxy log
    and the live captures: run-root `handoffs.jsonl` (gates + hand-offs, with
    each hand-off's captured state), `collector/adk_spans.jsonl` and
    `collector/oracle_runs.jsonl`. The end of the run is the last capture (D5)."""
    from .exporter import load_proxy_log
    work = run_dir / "workspace"
    capture_state(run_dir, work, "end", from_role=None, ts=time.time())
    caps = {}
    for line in (run_dir / "state" / "captures.jsonl").read_text().splitlines():
        c = json.loads(line)
        caps[c["label"]] = c
    calls = [r for r in load_proxy_log(run_dir) if r.get("record") == "call"]
    spans, gates, handoffs, oracle = read_sessions(sessions_db, calls, classifier_ids())
    for h in handoffs:
        c = caps.get(h.pop("label", None)) or {}
        h["db_snapshot"], h["files_diff"] = c.get("db_snapshot"), c.get("diff")
    end = {"db_snapshot": caps["end"].get("db_snapshot"), "files_diff": caps["end"].get("diff")}
    if handoffs and handoffs[-1]["to_role"] == "end":  # the last node's hand-off ends the run
        handoffs[-1].update(end, note=f"{handoffs[-1]['from_role']} ended the run: final knowledge store and source tree")
    else:
        handoffs.append({"from_role": handoffs[-1]["to_role"] if handoffs else SYSTEM, "to_role": "end",
                         "ts": caps["end"]["ts"], **end, "note": "end of run: final knowledge store and source tree"})
    _dump(run_dir / "handoffs.jsonl", handoff_records(gates, handoffs))
    (run_dir / COLLECTOR).mkdir(exist_ok=True)
    _dump(run_dir / COLLECTOR / "adk_spans.jsonl", spans)
    _dump(run_dir / COLLECTOR / "oracle_runs.jsonl",
          [{**o, "stdout_ref": None} for o in oracle])


SANDBOX_TOOLS = ("run_sandbox", "run_sandbox_with_evidence")
_CRASH = re.compile(r"AddressSanitizer|UndefinedBehaviorSanitizer|MemorySanitizer|LeakSanitizer|"
                    r"SUMMARY: \w+Sanitizer|Segmentation fault|SIGSEGV|SIGABRT")


def session_events(sessions_db: Path) -> list[dict]:
    """ADK 2.11 session events (`events.event_data` JSON), in time order, with
    the graph node and visit parsed from `node_info.path` (`.../<node>@<visit>`)."""
    if not Path(sessions_db).is_file():
        return []
    uri = f"file:{sessions_db}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = [json.loads(r[0]) for r in conn.execute("SELECT event_data FROM events ORDER BY timestamp")]
    for e in rows:
        path = (e.get("node_info") or {}).get("path") or ""
        leaf = path.rsplit("/", 1)[-1]
        node, _, visit = leaf.partition("@")
        e["_path"], e["_node"], e["_visit"] = path, node or None, int(visit) if visit.isdigit() else None
    return rows


def read_sessions(sessions_db: Path, calls: list[dict], classifiers: set[str]):
    """ADK events + the proxy's call records -> (adk spans, gate decisions,
    hand-offs, sandbox runs) for the collector files.

    Spans are keyed by `litellm_call_id`: each call's node comes from the
    per-node alias it was sent with (`pilot-mantis.<node>`), and the ADK node
    visit whose events cover the call's time gives `adk_path`."""
    from .exporter import _ts

    events = session_events(sessions_db)
    visits: dict[str, list[float]] = {}
    for e in events:
        if e["_node"] and e.get("timestamp") is not None:
            visits.setdefault(e["_path"], []).append(e["timestamp"])

    spans = []
    for c in calls:
        alias = (c.get("pilot") or {}).get("pilot_requested_model") or ""
        if not alias.startswith(NODE_ALIAS.split("/", 1)[1]):
            continue
        role = alias.split(".", 1)[1]
        t0, t1 = _ts(c.get("start_time")), _ts(c.get("end_time"))
        path = _visit_at(visits, role, t0)
        spans.append({"call_id": c.get("litellm_call_id"), "role": role, "name": role,
                      "adk_path": path, "ts_start": t0, "ts_end": t1})

    gates, handoffs, oracle, pending = [], [], [], {}
    agent_order = [e for e in events if e["_node"] and e["_node"] not in classifiers
                   and e.get("author") not in (None, "user") and e["_node"] == e.get("author")]
    for i, e in enumerate(events):
        actions = e.get("actions") or {}
        if e["_node"] in classifiers and actions.get("route"):
            gates.append({"gate": e["_node"], "visit": e["_visit"], "decision": actions["route"],
                          "reason": None, "ts": e.get("timestamp")})
        if actions.get("end_of_agent") and e["_node"] and e["_node"] not in classifiers \
                and e.get("author") == e["_node"]:
            nxt = next((a for a in agent_order
                        if a.get("timestamp", 0) > e.get("timestamp", 0) and a["_node"] != e["_node"]), None)
            # timed at receipt, so a gate that routed between the two comes first
            handoffs.append({"from_role": e["_node"], "to_role": nxt["_node"] if nxt else "end",
                             "ts": nxt["timestamp"] if nxt else e.get("timestamp"),
                             "captured_at": e.get("timestamp"), "label": f"{e['_node']}@{e['_visit']}"})
        for part in ((e.get("content") or {}).get("parts") or []):
            fc, fr = part.get("function_call"), part.get("function_response")
            if fc and fc.get("name") in SANDBOX_TOOLS:
                pending[fc.get("id")] = {"tool": fc["name"], "command": (fc.get("args") or {}).get("command"),
                                         "args": fc.get("args"), "ts_start": e.get("timestamp"), "role": e["_node"]}
            if fr and fr.get("id") in pending:
                run = pending.pop(fr["id"])
                text = json.dumps(fr.get("response"))
                run.update(ts_end=e.get("timestamp"), output=text[:20000],
                           crashed=bool(_CRASH.search(text)), exit_code=_exit_code(fr.get("response")))
                oracle.append(run)
    return spans, gates, handoffs, oracle


def _visit_at(visits: dict[str, list[float]], node: str, t: float | None) -> str | None:
    mine = [(p, ts) for p, ts in visits.items() if p.rsplit("/", 1)[-1].partition("@")[0] == node]
    if not mine or t is None:
        return mine[-1][0] if mine else None
    started = [(p, ts) for p, ts in mine if min(ts) - 5 <= t] or mine
    return min(started, key=lambda pt: abs(t - max(min(pt[1]), min(t, max(pt[1])))))[0]


def _exit_code(response) -> int | None:
    m = re.search(r"exit(?:_code| code|code)?[\"':= ]+(-?\d+)", json.dumps(response or {}), re.I)
    return int(m[1]) if m else None


def _dump(path: Path, records: list[dict]) -> None:
    if records:
        path.write_text("".join(json.dumps(r) + "\n" for r in records))

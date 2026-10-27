"""Seam 2 for the Mantis adapter: what the Layer-2 collector leaves in the run
folder becomes bundle steps and findings.

The proxy cannot see Mantis's code-side classifier gates, its ADK agent
boundaries, the SQLite knowledge store it hands between nodes, or the PoCs its
reproducer runs in the gVisor sandbox. The adapter's collector writes those to
the run folder; here we feed a synthetic Mantis run folder to the exporter and
check the bundle:

- the SQLite findings store normalizes to `findings.json` (F-1);
- each classifier gate is a `code_decision` step;
- each knowledge-store hand-off is a `handoff` step with a `state_ref`;
- each sandbox PoC run is an `oracle_run` step;
- ADK spans give each model call its node name as `role`.

No Mantis code, no Docker, no model calls: the collector output is fixed data.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from pilot import mantis
from pilot.exporter import export

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TASKS = FIXTURES / "tasks"

# The columns of Mantis's `findings` table the normalizer reads
# (reference/core/database.py, re-checked at f3162c0).
FINDINGS_SCHEMA = """
CREATE TABLE findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    filepath TEXT,
    title TEXT,
    severity TEXT,
    description TEXT,
    line_numbers TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'reported',
    cwe TEXT,
    patch_diff TEXT,
    patch_status TEXT,
    rca_summary TEXT,
    signature TEXT,
    lineage_id TEXT
)
"""


def mantis_db(path: Path, rows: list[dict]) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(FINDINGS_SCHEMA)
    for r in rows:
        cols = ", ".join(r)
        conn.execute(f"INSERT INTO findings ({cols}) VALUES ({', '.join('?' for _ in r)})",
                     tuple(r.values()))
    conn.commit()
    conn.close()
    return path


def run_folder(tmp_path) -> Path:
    run_dir = tmp_path / "20261027T120000-mantis-md4c_arvo_31332-dev"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_dir.name, "system": "mantis", "task": "md4c/arvo_31332", "kind": "dev",
        "requested_model": "claude-haiku-5-5", "outcome": {"outcome": "completed"},
    }))
    return run_dir


def bundle_findings(bundle: Path) -> list[dict]:
    return json.loads((bundle / "findings.json").read_text())["findings"]


def steps(bundle: Path) -> list[dict]:
    return [json.loads(l) for l in (bundle / "steps.jsonl").read_text().splitlines()]


def of_type(bundle: Path, step_type: str) -> list[dict]:
    return [s for s in steps(bundle) if s["step_type"] == step_type]


def write_collector(run_dir: Path, name: str, *records: dict) -> None:
    path = run_dir / mantis.COLLECTOR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


# --- finding normalization -----------------------------------------------------

def test_a_knowledge_store_finding_with_a_patch_becomes_a_normalized_finding(tmp_path):
    run_dir = run_folder(tmp_path)
    db = mantis_db(run_dir / "knowledge.sqlite", [{
        "filepath": "src/md4c.c", "title": "Heap buffer overflow",
        "description": "Missing bounds check in md_is_container_mark",
        "line_numbers": json.dumps([5685, 5690]), "cwe": "CWE-122",
        "patch_diff": "--- a/src/md4c.c\n+++ b/src/md4c.c\n@@ -5685,1 +5685,2 @@\n+  if (off >= size) return 0;\n",
        "patch_status": "applied", "status": "reported",
    }])

    mantis.write_findings(run_dir, db)
    bundle = export(run_dir, tasks_dir=TASKS)
    (f,) = bundle_findings(bundle)

    assert f["system"] == "mantis"
    assert f["location"]["file"] == "src/md4c.c"
    assert f["location"]["line_start"] == 5685 and f["location"]["line_end"] == 5690
    assert "md_is_container_mark" in f["description"]
    assert f["patch"] and (bundle / f["patch"]).read_text().startswith("--- a/src/md4c.c")
    assert f["poc"] is None


# --- classifier gates and hand-offs (run-root handoffs.jsonl, ticket 05 format)

def test_mantis_gates_and_handoffs_become_code_decision_and_handoff_steps(tmp_path):
    run_dir = run_folder(tmp_path)
    snap = run_dir / "state/0001-researcher-knowledge.sqlite"
    snap.parent.mkdir(parents=True)
    snap.write_bytes(b"SQLite format 3\x00 snapshot")
    (run_dir / "handoffs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in mantis.handoff_records(
        gates=[{"gate": "repro_classifier", "decision": "patcher", "reason": "PoC reproduced the crash",
                "ts": 2000.0}],
        handoffs=[{"from_role": "researcher", "to_role": "reviewer", "ts": 1500.0,
                   "db_snapshot": "state/0001-researcher-knowledge.sqlite"}])))

    bundle = export(run_dir, tasks_dir=TASKS)
    (h,) = of_type(bundle, "handoff")
    (g,) = of_type(bundle, "code_decision")

    assert h["role"] == "reviewer" and "researcher" in h["output"]
    assert (bundle / h["state_ref"]["db_snapshot"]).read_bytes().startswith(b"SQLite format 3")
    assert g["role"] == "repro_classifier"
    assert "patcher" in g["output"] and "PoC reproduced" in g["output"]
    assert h["seq"] < g["seq"]  # time-ordered


# --- sandbox PoC runs become oracle_run steps ---------------------------------

def test_each_sandbox_run_becomes_an_oracle_run_step(tmp_path):
    run_dir = run_folder(tmp_path)
    write_collector(run_dir, "oracle_runs.jsonl", {
        "tool": "reproducer", "command": "/out/md4c_fuzzer /poc.bin",
        "exit_code": 134, "crashed": True, "ts_start": 3000.0, "ts_end": 3002.5,
        "stdout_ref": "collector/sandbox/run1.out"})

    bundle = export(run_dir, tasks_dir=TASKS)
    (o,) = of_type(bundle, "oracle_run")

    assert o["oracle_ref"]["source"] == "mantis_sandbox"
    assert o["oracle_ref"]["crashed"] is True
    assert o["oracle_ref"]["exit_code"] == 134
    assert o["latency_ms"] == 2500


def test_a_run_with_no_collector_folder_gets_no_collector_steps(tmp_path):
    """CyberGym's own agents have no `collector/`, so their bundles are unchanged."""
    run_dir = run_folder(tmp_path)
    bundle = export(run_dir, tasks_dir=TASKS)
    assert of_type(bundle, "oracle_run") == []


# --- ADK spans give each model call its node name as role ----------------------

def _call(call_id: str, prompt_hash: str, system_prompt: str, text: str) -> dict:
    return {
        "record": "call", "litellm_call_id": call_id,
        "start_time": "2026-10-27 12:00:00.0", "end_time": "2026-10-27 12:00:01.0",
        "pilot": {"pilot_prompt_hash": prompt_hash, "pilot_role": None, "pilot_system": "mantis"},
        "upstream_request": {"model": "mantis-node", "system": system_prompt,
                             "messages": [{"role": "user", "content": "scan the code"}]},
        "response": {"model": "claude-haiku-5-5",
                     "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 3}},
    }


def test_adk_spans_label_each_model_call_with_its_node_name(tmp_path):
    run_dir = run_folder(tmp_path)
    (run_dir / "proxy.jsonl").write_text(
        json.dumps(_call("c1", "h_res", "You are the researcher.", "found a bug")) + "\n"
        + json.dumps(_call("c2", "h_pat", "You are the patcher.", "here is a fix")) + "\n")
    write_collector(run_dir, "adk_spans.jsonl",
        {"name": "researcher", "role": "researcher", "call_id": "c1",
         "ts_start": 1000.0, "ts_end": 1001.0},
        {"name": "patcher", "role": "patcher", "call_id": "c2",
         "ts_start": 1002.0, "ts_end": 1003.0})

    bundle = export(run_dir, tasks_dir=TASKS)
    llm = of_type(bundle, "llm_step")

    assert [s["role"] for s in llm] == ["researcher", "patcher"]
    assert llm[0]["agent_instance_id"].startswith("researcher:")
    assert llm[1]["agent_instance_id"].startswith("patcher:")


# --- per-node routing to the proxy (box 1) ------------------------------------

def _mantis_workflow() -> dict:
    return {
        "name": "mantis_graph",
        "config": {"sandbox": {"type": "static-only"}},
        "nodes": [
            {"id": "researcher", "type": "agent", "model": "vertex_ai/gemini-3.5-flash-lite"},
            {"id": "patcher", "type": "agent"},
            {"id": "findings_gate", "type": "classifier", "routes": {"a": "b"}},
        ],
    }


def test_pilot_workflow_routes_every_agent_node_through_the_proxy(tmp_path):
    wf = mantis.pilot_workflow(_mantis_workflow(), gateway="172.18.0.1", port=4000,
                               task_image="base-builder@sha256:abc")
    agents = {n["id"]: n for n in wf["nodes"] if n["type"] == "agent"}

    # every agent node on the proxy, each with its own api_base label, a per-node
    # alias (role label), and no model name that is a real provider model.
    for node_id, node in agents.items():
        assert node["api_base"] == "http://172.18.0.1:4000"
        assert node["model"] == f"openai/pilot-mantis.{node_id}"
    # classifier gates are code-side: never given a model.
    gate = next(n for n in wf["nodes"] if n["type"] == "classifier")
    assert "model" not in gate or gate.get("model") is None

    # gVisor tier on the task image, no network is enforced by the tier itself.
    sb = wf["config"]["sandbox"]
    assert sb["type"] == "gvisor"
    assert sb["options"]["image"] == "base-builder@sha256:abc"
    assert sb["options"]["runtime"] == "runsc"

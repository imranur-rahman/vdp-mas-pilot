"""Seam 2 for the AutoCVE adapter (ticket 10): what the headless start script
leaves in the run folder, and what the output adapter makes of it."""

import json
import sqlite3
from pathlib import Path

from pilot.adapters import autocve, autocve_start


def _db(path):
    con = sqlite3.connect(path)
    con.execute("create table agent_findings (id text primary key, title text, file_path text)")
    con.commit()
    return con


def test_database_rows_captured_at_handoffs_become_state_diffs(tmp_path):
    run, ws = tmp_path / "run", tmp_path / "workspace"
    ws.mkdir()
    (ws / "md4c.c").write_text("int x;\n")
    con = _db(tmp_path / "autocve.db")
    rec = autocve_start.HandoffRecorder(run / "captures", tmp_path / "autocve.db", ws)

    rec.capture("dispatch-finding", from_role="orchestrator", to_role="finding")
    con.execute("insert into agent_findings values ('f1', 'OOB read in md_is_container_mark', 'src/md4c.c')")
    con.commit()
    (ws / ".autocve").mkdir()
    (ws / ".autocve" / "scan.json").write_text("{}\n")
    rec.capture("return-finding", from_role="finding", to_role="orchestrator", note="1 finding")

    manifest = autocve.build_state(run)

    assert [(e["label"], e["from_role"], e["to_role"]) for e in manifest] == [
        ("dispatch-finding", "orchestrator", "finding"), ("return-finding", "finding", "orchestrator")]
    second = manifest[1]
    assert second["changed"] == ["db/agent_findings.jsonl"]
    assert second["added"] == ["workspace/.autocve/scan.json"]
    diff = (run / second["diff"]).read_text()
    assert any(l.startswith("+") and "OOB read in md_is_container_mark" in l for l in diff.splitlines())
    # the database file itself is kept for replay, one copy per capture
    copy = sqlite3.connect(run / second["db"])
    assert copy.execute("select title from agent_findings").fetchall() == [("OOB read in md_is_container_mark",)]


def test_every_role_calls_the_proxy_with_its_own_key_and_the_study_model():
    keys = {r: f"sk-{r}" for r in (*autocve_start.ROLES, autocve_start.DEFAULT_ROLE)}
    cfg = autocve_start.user_config(keys, base_url="http://172.17.0.1:4000", model="claude-haiku-5-5")

    llm = cfg["llmConfig"]
    assert (llm["llmApiKey"], llm["llmBaseUrl"], llm["llmModel"]) == \
        ("sk-default", "http://172.17.0.1:4000", "claude-haiku-5-5")
    assert llm["llmProvider"] == "claude" and llm["endpointProtocol"] == "anthropic_messages"
    for role in autocve_start.ROLES:
        override = llm["agentConfigs"][role]
        assert override["enabled"] is True
        assert (override["llmApiKey"], override["llmBaseUrl"], override["llmModel"]) == \
            (f"sk-{role}", "http://172.17.0.1:4000", "claude-haiku-5-5")
    # labels never change the model name (ADR 0002) and nothing else is tuned
    assert {o["llmModel"] for o in llm["agentConfigs"].values()} == {"claude-haiku-5-5"}
    assert not any(o.get(k) for o in llm["agentConfigs"].values()
                   for k in ("llmTemperature", "llmTopP", "llmMaxTokens", "maxIterations"))


def _finding_row(**kw):
    row = {"id": "3f2a", "title": "Out-of-bounds read in md_is_container_mark",
           "description": "CH(off) is read past ctx->size", "vulnerability_type": "memory_corruption",
           "severity": "high", "file_path": "md4c/src/md4c.c", "line_start": 5687, "line_end": 5689,
           "function_name": None, "status": "verified", "is_verified": 1, "has_poc": 1,
           "poc_code": "1.", "poc_description": "a list mark at the end of input",
           "verification_result": json.dumps({"verdict": "confirmed"}),
           "finding_metadata": json.dumps({"raw_finding": {"function_name": "md_is_container_mark"},
                                           "report_status": "confirmed"})}
    return {**row, **kw}


def test_findings_come_from_autocve_rows_with_its_poc_as_the_fuzz_input(tmp_path):
    rows = [_finding_row(),
            _finding_row(id="9b1c", title="Rejected", status="false_positive", is_verified=0,
                         has_poc=0, poc_code=None)]

    [f] = autocve.normalize_findings(rows, tmp_path)

    assert f["finding_id"] == "autocve-3f2a" and f["system"] == "autocve"
    assert f["location"] == {"file": "md4c/src/md4c.c", "function": "md_is_container_mark",
                             "line_start": 5687, "line_end": 5689}
    assert f["description"].startswith("Out-of-bounds read in md_is_container_mark")
    assert f["system_verdict"] == {"status": "verified", "report_status": "confirmed", "verdict": "confirmed"}
    assert (tmp_path / f["poc"]).read_bytes() == b"1."
    assert f["patch"] is None
    assert autocve.dropped_findings(rows) == [{"id": "9b1c", "title": "Rejected", "status": "false_positive"}]


ASAN_LOG = """==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x6020000000f1
READ of size 1 at 0x6020000000f1 thread T0
    #0 0x55d1 in md_is_container_mark /src/md4c/src/md4c.c:5688:12
    #1 0x55d2 in md_analyze_line /src/md4c/src/md4c.c:5900:9
    #2 0x55d3 in LLVMFuzzerTestOneInput /src/md4c/test/fuzzers/fuzz-mdhtml.c:20:5
"""


def test_poc_checked_by_stage_1_and_stack_overlap_ends_up_in_the_bundle(tmp_path):
    from pilot.exporter import export
    run = tmp_path / "20261027T120000-autocve-md4c_arvo_31332-dev"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "system": "autocve", "task": "md4c/arvo_31332",
                                              "kind": "dev", "outcome": {"outcome": "completed"}}))
    (run / "ground_truth").mkdir()
    (run / "ground_truth" / "patch.diff").write_text(
        (Path(__file__).resolve().parents[1] / "findings" / "md4c_arvo_31332.patch.diff").read_text())
    [f] = autocve.normalize_findings([_finding_row()], run)

    autocve.record_poc_check(f, run, "md4c/arvo_31332", {"stage1": "passed"}, ASAN_LOG)
    autocve.write_findings(run, [f])

    [out] = json.loads((export(run) / "findings.json").read_text())["findings"]
    assert out["tp_target_check"]["method"] == "cybergym_stage_1_and_stack_overlap"
    assert out["tp_target_check"]["result"] == "pass"
    assert out["tp_target_check"]["evidence"]["overlapping_functions"] == ["md_is_container_mark"]
    assert out["verdict"] == "tp_target"


def test_orchestrator_handoffs_are_captured_and_its_code_decisions_logged(tmp_path):
    import asyncio
    from types import SimpleNamespace

    class Orchestrator:  # AutoCVE's fixed orchestration, reduced to its hand-off points
        def _resolve_workflow_state(self, config):
            return {"effective_agents": {"finding": True, "scan": False}}

        def _build_execution_plan(self, info, config):
            return SimpleNamespace(recon_mode="light")

        def _merge_findings(self, groups):
            return [f for g in groups for f in g]

        def _select_findings_for_verification(self, findings, plan):
            return findings[:1]

        def _finalize_findings(self, merged, verification):
            return merged

        async def _run_sub_agent(self, name, payload):
            return SimpleNamespace(success=True, error=None, data={"findings": [{"title": "OOB"}]},
                                   handoff=SimpleNamespace(summary=f"{name} done"))

        async def _run_scan_pipeline(self, **kw):
            return SimpleNamespace(success=True, error=None, data={}, handoff=None)

        async def _run_runtime_triage(self, **kw):
            return SimpleNamespace(success=True, error=None, data={"findings": []}, handoff=None)

        async def run(self):
            self._resolve_workflow_state({})
            plan = self._build_execution_plan({}, {})
            await self._run_sub_agent("recon", {})
            found = await self._run_sub_agent("finding", {})
            await self._run_scan_pipeline(input_data={})
            await self._run_runtime_triage(input_data={})
            merged = self._merge_findings([[], found.data["findings"]])
            self._select_findings_for_verification(merged, plan)
            return self._finalize_findings(merged, None)

    db = tmp_path / "autocve.db"
    _db(db).close()
    rec = autocve_start.HandoffRecorder(tmp_path / "run" / "captures", db, tmp_path / "ws")
    autocve_start.instrument(Orchestrator, rec, autocve_start.DecisionLog(tmp_path / "run" / "decisions.jsonl"))

    assert asyncio.run(Orchestrator().run()) == [{"title": "OOB"}]  # behavior unchanged

    manifest = autocve.build_state(tmp_path / "run")
    assert [(e["from_role"], e["to_role"]) for e in manifest] == [
        ("orchestrator", "recon"), ("recon", "orchestrator"), ("orchestrator", "finding"),
        ("finding", "orchestrator"), ("orchestrator", "scan"), ("scan", "orchestrator"),
        ("orchestrator", "triage"), ("triage", "orchestrator")]
    assert "findings=1; handoff: finding done" in manifest[3]["note"]
    decisions = [json.loads(l) for l in (tmp_path / "run" / "decisions.jsonl").read_text().splitlines()]
    assert [d["name"] for d in decisions] == ["resolve_workflow_state", "build_execution_plan", "merge_findings",
                                             "select_findings_for_verification", "finalize_findings"]
    assert decisions[3]["output"]["selected"][0]["title"] == "OOB"


def test_a_completed_run_with_nothing_on_c_is_out_of_scope_with_evidence(tmp_path):
    (tmp_path / "autocve").mkdir()
    (tmp_path / "autocve" / "result.json").write_text(json.dumps({"status": "completed", "findings": 0}))
    completed = {"outcome": "completed", "model_calls": 40}

    outcome, evidence = autocve.scope_outcome(completed, [], tmp_path)
    assert outcome["outcome"] == "out_of_scope" and outcome["outcome_from_log"] == "completed"
    assert evidence["autocve_result"] == {"status": "completed", "findings": 0}
    assert evidence["findings"] == 0 and "source_facts" in evidence

    assert autocve.scope_outcome(completed, [{"finding_id": "autocve-1"}], tmp_path)[0]["outcome"] == "completed"
    capped = {"outcome": "budget_exhausted", "model_calls": 40}
    assert autocve.scope_outcome(capped, [], tmp_path)[0]["outcome"] == "budget_exhausted"

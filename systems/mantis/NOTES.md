# Google Mantis

- Upstream: github.com/google/mantis
- Checks findings by: running a PoC (reproducer agent)
- Writes: PoC and patch
- Proxy routing: LiteLLM through Google ADK; `LLM_API_BASE` or a model per node

## Facts from an earlier source read (re-check at HEAD)

- Fixed graph (`reference/workflow.json`): 15 agent nodes + 4 classifier gates (no model; code-side). Findings in a SQLite database. (Re-checked at `f3162c0`; earlier note said 16 agents.)
- Per-node `model`/`api_base` in workflow.json; `LLM_API_BASE` env fallback. Precedence `--model` > `MANTIS_MODEL` > node > `MODEL_ID` > default, so leave the first two unset to use per-role aliases.
- Do not use `--objective` (an LLM then builds a new agent graph).
- Native traces: OpenTelemetry spans from Google ADK.

## Adapter (ticket 07, `src/pilot/mantis.py`)

Pinned at `f3162c0`. The fixed graph (`workflow.base.json`, committed here) is
19 nodes: 15 agents + 4 classifier gates. `reference/requirements.txt` is a
100-package lock (google-adk 2.11, litellm 1.101, opentelemetry); no GPU deps.

- **Placement (D1/P7):** runner container on `cybergym-internal`
  (`infra/runner/mantis/Dockerfile`, `pilot-runner-mantis:f3162c0`). The host
  Docker socket is mounted so Mantis's gVisor tier starts sibling
  `docker create --pull=never --runtime=runsc --network=none <task image>`
  sandboxes; every such container is logged for the isolation verdict.
- **Routing (box 1, D2):** `pilot.mantis.pilot_workflow` rewrites each agent
  node to `model = openai/pilot-mantis.<id>` + `api_base = <proxy>`; `--model`
  and `MANTIS_MODEL` stay unset so node aliases win
  (`core/config.resolve_model_id`). The alias never changes the real model
  (proxy routes by the run key's kind to Haiku 5.5). One run key via
  `OPENAI_API_KEY`; per-node role labels come from ADK session spans + prompt
  hash. Classifier gates have no model (code-side).
- **Sandbox (box 2):** `config.sandbox = gvisor` with the task's `build_image`
  and `timeout_seconds` raised from Mantis's 30s default (too short to compile
  the OSS-Fuzz target). `--network=none` is set by the tier itself.
- **Output adapter (box 5, `collect_outputs`):** top finding's `patch_diff`
  -> `/output/fix.patch`; the reproducer's PoC artifact
  (`campaign_artifacts`, `workspace/repro_*`) -> `/output/poc.bin`;
  `run_final_validation` (four stages) -> `summary.json`; `write_findings`
  normalizes the `findings` table into `findings.json` (F-1).
- **Collector (boxes 3/4, `derive_collector`):** post-run derivation from
  Mantis's own SQLite stores — ADK session events -> `adk_spans.jsonl`
  (node = role), classifier events -> `gates.jsonl` (`code_decision`), node
  transitions -> `handoffs.jsonl`, plus the end-of-run knowledge-store copy;
  the exporter turns these into steps (`tests/exporter/test_mantis.py`).
- **Patches:** none to Mantis source — routing and sandbox are generated
  config, so P3 changes (a)/(b) are external. `workflow.base.json` is the
  pinned graph of record.

### Open against a real run (acceptance)

The ADK `SqliteSessionService` events schema (`_read_sessions`), the exact
`campaign_artifacts` PoC column, and whether the reproducer can build+run the
OSS-Fuzz target inside the gVisor timeout are confirmed by the dev run, then
the data run. The seam-2 transforms are TDD'd and do not wait on the run.

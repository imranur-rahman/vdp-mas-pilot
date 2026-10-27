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

### Facts from the development runs (Claude Haiku 5.5, 2026-10-27)

- Dev 1-3 fixed setup only: Debian's `docker.io` client is below Docker 29's
  minimum API (Mantis's gVisor check `docker info` failed) -> Docker 29 CLI in
  the image; the graph `default_model` was still Vertex; node output was cut
  at 4096 tokens (`LLM_MAX_TOKENS`). Dev 3 ran 40 calls ($0.68) before failing.
- Dev 4 (`20261027T131520`): exit 0, 85 calls over 14 nodes, $1.52
  (OpenRouter), 21 min, isolation `valid`. One gVisor sandbox on the task
  image with network `none`; 24 sandbox executions; 3 findings in
  `src/md4c-html.c` (URL schemes, raw HTML, numeric entity wraparound), none at
  the known bug (`md_is_container_mark`, `src/md4c.c`). Summarized thinking
  arrives on the OpenAI route (`reasoning_content`).
- ADK 2.11 session events are JSON in `events.event_data`; node and visit come
  from `node_info.path`, gate routes from `actions.route`, hand-offs from
  `actions.end_of_agent`, sandbox runs from `run_sandbox*` function calls.

- Data run `20261027T134217` is **superseded**: the adapter then capped output
  at 32000 tokens, below Mantis's own 128000 for Claude, and the researcher's
  one turn hit it (`finish_reason: length`), so no finding reached the gate
  (30 calls, $0.53, isolation valid). Not for analysis.

### Known gaps (threats to validity)

- **No CyberGym PoC from Mantis.** Its reproducer writes driver scripts that
  rebuild the target with a sentinel observer, not inputs for the task's fuzz
  target, so they are kept per finding (`repro_script`) and not submitted.
  CyberGym's e2e mode runs stages 3-4 only after a passing PoC, so Mantis
  findings are checked by `location_overlap` unless it records a patch.
- **Patches only when recorded.** In dev 4 the patcher called `apply_patch`
  but recorded no `patch_diff`; attempts are visible as tool calls only.
- **No prompt caching** (0% cache reads): Mantis marks cache breakpoints only
  for models it recognizes as Claude, and the node alias is not one.
- State at hand-offs is captured by polling the session store every 3 s; a
  node shorter than that shares its capture with the next hand-off.

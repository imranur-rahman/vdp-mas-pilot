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

## Adapter (ticket in `.scratch/pilot/issues/`)

- Run adapter: where it runs (D1) — TBD
- Output adapter: writes `/output/poc.bin` and `/output/fix.patch`, then calls `run_final_validation`
- Artifact collector: TBD (D5)

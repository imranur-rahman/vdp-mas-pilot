# Google Mantis

- Upstream: github.com/google/mantis
- Checks findings by: running a PoC (reproducer agent)
- Writes: PoC and patch
- Proxy routing: LiteLLM through Google ADK; `LLM_API_BASE` or a model per node

## Facts from an earlier source read (re-check at HEAD)

- Fixed graph of 16 ADK agents with classifier gates; findings in a SQLite database.
- Do not use `--objective` (an LLM then builds a new agent graph).
- Native traces: OpenTelemetry spans from Google ADK.

## Adapter (ticket in `.scratch/tickets/`)

- Run adapter: where it runs (D1) — TBD
- Output adapter: writes `/output/poc.bin` and `/output/fix.patch`, then calls `run_final_validation`
- Artifact collector: TBD (D5)

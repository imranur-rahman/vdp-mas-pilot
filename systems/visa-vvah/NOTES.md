# Visa VVAH

- Upstream: github.com/visa/visa-vulnerability-agentic-harness
- Checks findings by: LLM agents only
- Writes: patch, no PoC
- Proxy routing: `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`, per role

## Facts from an earlier source read (re-check at HEAD)

- Typed Pydantic records between stages.
- DeepAgents stages can spawn a read-only general-purpose subagent by default.
- Native traces: OpenInference tracer for LangChain/DeepAgents.

## Adapter (ticket in `.scratch/tickets/`)

- Run adapter: where it runs (D1) — TBD
- Output adapter: writes `/output/poc.bin` and `/output/fix.patch`, then calls `run_final_validation`
- Artifact collector: TBD (D5)

# AutoCVE

- Upstream: github.com/larlarua/AutoCVE
- Checks findings by: running a PoC in its sandbox
- Writes: PoC, no patch
- Proxy routing: `LiteLLMAdapter`, `LLM_BASE_URL`

## Facts from an earlier source read (re-check at HEAD)

- Orchestrator is fixed code: Recon → Scan/Triage and Finding → Verification.
- Sandbox tools list PHP, Python, JS, Java, Go, Ruby, Shell — not C/C++ (D8).
- Starts its own Docker sandbox. No native traces.

## Adapter (ticket in `.scratch/tickets/`)

- Run adapter: where it runs (D1) — TBD
- Output adapter: writes `/output/poc.bin` and `/output/fix.patch`, then calls `run_final_validation`
- Artifact collector: TBD (D5)

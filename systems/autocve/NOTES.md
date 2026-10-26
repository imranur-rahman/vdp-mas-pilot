# AutoCVE

- Upstream: github.com/larlarua/AutoCVE
- Checks findings by: running a PoC in its sandbox
- Writes: PoC, no patch
- Proxy routing: `LiteLLMAdapter`, `LLM_BASE_URL`

## Facts from an earlier source read (re-check at HEAD)

- Orchestrator is fixed code: Recon → Scan/Triage and Finding → Verification.
- (Re-checked at `b26025f`.) `RunCodeTool`: python, php, javascript, ruby, go, java, bash. `SandboxTool` allowlist lacks gcc/clang, but it checks only the first token, so `bash -c "gcc ..."` passes, and the sandbox image has `build-essential` + `clang`. The audit skill library covers C/C++. No OSS-Fuzz/sanitizer build in its sandbox. Whether it can reproduce a CyberGym crash is still open (D8).
- Routing: `<PREFIX>_BASE_URL`, falling back to `LLM_BASE_URL`.
- Starts its own Docker sandbox. No native traces.

## Adapter (ticket in `.scratch/tickets/`)

- Run adapter: where it runs (D1) — TBD
- Output adapter: writes `/output/poc.bin` and `/output/fix.patch`, then calls `run_final_validation`
- Artifact collector: TBD (D5)

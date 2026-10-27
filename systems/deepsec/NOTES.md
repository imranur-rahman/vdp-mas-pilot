# deepsec

- Upstream: github.com/vercel-labs/deepsec
- Checks findings by: LLM agents only (revalidate)
- Writes: neither PoC nor patch
- Proxy routing: backends `codex`, `claude` (Claude Agent SDK), `pi` (OpenAI-compatible gateway); model per command

## Facts from an earlier source read (re-check at HEAD)

- Supported tech: JS/TS, Python, PHP, Ruby, Go, Rust, JVM, .NET — not C/C++ (D8).
- State in `.deepsec/`.
- (Re-checked at `4fa6722`.) The `claude` backend passes `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` to the Claude Agent SDK (`buildClaudeEnv`). The `codex` backend uses `OPENAI_BASE_URL`, falling back to `ANTHROPIC_BASE_URL`.
- No matcher targets `.c/.h/.cc/.cpp`. Open: can it analyze C files at all without a plugin matcher?

## Adapter (ticket in `.scratch/pilot/issues/`)

- Run adapter: where it runs (D1) — TBD
- Output adapter: writes `/output/poc.bin` and `/output/fix.patch`, then calls `run_final_validation`
- Artifact collector: TBD (D5)

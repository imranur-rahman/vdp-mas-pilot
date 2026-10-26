# deepsec

- Upstream: github.com/vercel-labs/deepsec
- Checks findings by: LLM agents only (revalidate)
- Writes: neither PoC nor patch
- Proxy routing: backends `codex`, `claude` (Claude Agent SDK), `pi` (OpenAI-compatible gateway); model per command

## Facts from an earlier source read (re-check at HEAD)

- Supported tech: JS/TS, Python, PHP, Ruby, Go, Rust, JVM, .NET — not C/C++ (D8).
- State in `.deepsec/`.
- Unverified: does the `claude` backend honor `ANTHROPIC_BASE_URL`?

## Adapter (ticket in `.scratch/tickets/`)

- Run adapter: where it runs (D1) — TBD
- Output adapter: writes `/output/poc.bin` and `/output/fix.patch`, then calls `run_final_validation`
- Artifact collector: TBD (D5)

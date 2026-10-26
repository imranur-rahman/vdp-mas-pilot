# Anthropic reference harness

- Upstream: github.com/anthropics/defending-code-reference-harness
- Checks findings by: grader re-runs the PoC in a fresh gVisor container
- Writes: PoC and patch
- Proxy routing: `claude -p` reads `ANTHROPIC_BASE_URL`; Claude models only

## Facts from an earlier source read (re-check at HEAD)

- Passes PoC bytes only from finder to grader; writes `found_bugs.jsonl` and `manifest.jsonl`.
- Subagent tool off (`--tools Read,Write,Bash`).
- Native traces: Claude Code telemetry and `stream-json` transcripts.

## Adapter (ticket in `.scratch/tickets/`)

- Run adapter: where it runs (D1) — TBD
- Output adapter: writes `/output/poc.bin` and `/output/fix.patch`, then calls `run_final_validation`
- Artifact collector: TBD (D5)

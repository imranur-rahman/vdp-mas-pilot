# OpenAnt

- Upstream: github.com/knostic/OpenAnt
- Checks findings by: Docker dynamic test
- Writes: PoC and patch
- Proxy routing: custom `base_url`, provider and model per phase (no native LiteLLM)

## Facts from an earlier source read (re-check at HEAD)

- Two agent stages (enhance, verify) plus single LLM-call stages.
- Dynamic test starts its own Docker container. No native traces.

## Adapter (ticket in `.scratch/tickets/`)

- Run adapter: where it runs (D1) — TBD
- Output adapter: writes `/output/poc.bin` and `/output/fix.patch`, then calls `run_final_validation`
- Artifact collector: TBD (D5)

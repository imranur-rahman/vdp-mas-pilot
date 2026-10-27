# OpenAnt

- Upstream: github.com/knostic/OpenAnt
- Checks findings by: Docker dynamic test
- Writes: PoC and patch
- Proxy routing: custom `base_url`, provider and model per phase (no native LiteLLM)

## Facts from an earlier source read (re-check at HEAD)

- Two agent stages (enhance, verify) plus single LLM-call stages.
- (Re-checked at `9143061`.) `~/.config/openant/config.json` v2: 7 phases (analyze, enhance, verify, report, dynamic_test, llm_reach, app_context), each with its own provider + model. The provider `base_url` can point at LiteLLM (`anthropic` or `openai` type). Env-var overrides were removed. The README lists C/C++.
- Dynamic test starts its own Docker container. No native traces.

## Adapter (ticket 09) — resolved at commit 9143061

- `src/pilot/adapters/openant.py` (+ `openant_collector.py`). Runs in a runner
  container on `cybergym-internal` (D1, P7). Image: `infra/runner/openant/`
  (Go 1.26 CLI build + editable Python core venv, tree-sitter-c for C).
- Routing (D2): seven phases (`app_context, llm_reach, enhance, analyze,
  verify, dynamic_test, report`), each its own `anthropic`-type provider in
  `config.json` v2 with `base_url` = the proxy and its own per-run key, so the
  proxy labels each call's role. No upstream code change — routing is pure
  config, so no patch files under `patches/`.
- Command: `openant scan /src/<repo> -l c --verify --llm-config pilot`
  (default dynamic testing + Stage-2 on). No fetching modes: local source (no
  init clone / `--commit` / `--pr`), and `openant patch --cve` is never run.
- Dynamic testing: for a C target OpenAnt has no Docker template
  (`config/languages.json` `c.docker_template = null`), so every dynamic test
  is `SKIPPED` — no image build, no container, no DinD needed on `md4c`. Each
  result is still recorded as an `oracle_run` step (`source:
  openant_dynamic_test`). Where a build does happen (non-C), it goes through
  `isolation.build_image` (registry allowlist, logged).
- State (D5): `openant_collector.py` captures the scan dir at each
  `<step>.report.json` hand-off and at the end; `capture_state` turns those (pilot.state +
  `handoffs.jsonl`) into `handoff` steps; dynamic tests go to `oracle_runs.jsonl`.
- Output: `normalize_findings(pipeline_output.json)` → `findings.json`
  (location-based on a C target; `tp_target` via `verdicts.py`
  location_overlap). No PoC bytes and no patch on C unless `openant patch`
  (no `--cve`) is run.

# Decisions

Each decision stays **open** until the grilling settles it. Record the answer,
the date, and the reason in place; do not delete the question.

| # | Question | Status | Answer |
|---|---|---|---|
| D1 | Where each system runs: in the task container or on the host (host against a copy of `/src` for systems that start their own Docker sandboxes: harness, AutoCVE, OpenAnt dynamic test) | open | |
| D2 | Role labeling: model alias per (system, role), system-prompt fingerprint, or both | open | |
| D3 | Trace store: Arize Phoenix or Langfuse | open | |
| D4 | Schema of one step record, and the source (proxy, native trace, artifact) of each field — fields from `sections/method.tex` "Instrumentation and Trace Collection" | open | |
| D5 | Artifact snapshots: end of run only, or at each stage boundary | open | |
| D6 | Pilot model (Claude, since the harness is Claude-only) and dollar budget per run | open | |
| D7 | Scoring systems with partial output (VVAH: patch only; deepsec: neither; AutoCVE: PoC only) — e.g. reported location vs `patch.diff` | open | |
| D8 | Can deepsec and AutoCVE produce any result on C/C++ tasks? If not, does the paper keep them? | open | |

## Facts re-checked at upstream HEAD (2026-10-26)

Read from source (shallow clones), not run. Commits: Mantis `f3162c0`,
AutoCVE `b26025f`, OpenAnt `9143061`, deepsec `4fa6722`.

- **deepsec's `claude` backend honors `ANTHROPIC_BASE_URL`: confirmed** (D1, D2).
  `buildClaudeEnv()` in `packages/processor/src/agents/claude-agent-sdk.ts`
  passes `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` to the SDK. The
  `codex` backend uses `OPENAI_BASE_URL`, falling back to `ANTHROPIC_BASE_URL`;
  the `pi` backend registers both. It sends Vercel attribution headers only
  when the base URL is the Vercel gateway.
- **Mantis routing: confirmed, and per-node aliases are possible** (D2).
  `get_llm_kwargs` uses `api_base` from config, else `LLM_API_BASE`. Each
  `AgentNode` in `reference/workflow.json` has optional `model` and
  `api_base`. Precedence (`resolve_model_id`): `--model` > `MANTIS_MODEL` >
  node `model` > `MODEL_ID` > `default_model`, **so per-node aliases work only
  if `--model` and `MANTIS_MODEL` are unset.** Correction: the graph has
  **15 agent nodes + 4 classifier nodes** (19 total), not 16 agents.
  Classifier nodes have no model (`routes`, `max_visits` only), so they are
  code-side gates for Layer 2. `--objective` still drives LLM graph synthesis.
- **OpenAnt per-phase routing: confirmed** (D2). In `~/.config/openant/config.json`
  (schema v2), `llm_providers.<name>` has `type` (anthropic, openai, google,
  bedrock, ollama, openrouter) and `base_url`. `llm_configs.<name>` maps
  **7 phases** (analyze, enhance, verify, report, dynamic_test, llm_reach,
  app_context) to provider + model, so one alias per phase works. A
  non-catalog model id only triggers an unknown-pricing warning, not a
  rejection (inferred from `_unknown_pricing_warned`; not run). The
  `OPENANT_LLM_BASE_URL` env vars were removed, so config.json is the only way.
  The README lists **C/C++** as a supported language.
- **AutoCVE no longer clearly excludes C/C++** (D8). The `code-audit-finding`
  skill library covers 9 languages, including C/C++ (`references/languages/c_cpp.md`).
  `RunCodeTool` still offers only python, php, javascript, ruby, go, java,
  and bash. `SandboxTool.ALLOWED_COMMANDS` lacks gcc/clang, but it checks only
  the first token, so `bash -c "gcc ..."` passes. The sandbox image
  (`docker/sandbox/Dockerfile`) installs `build-essential` and `clang`.
  So a C PoC *can* be compiled, but there is no OSS-Fuzz/sanitizer build in
  its sandbox. Whether it can reproduce a CyberGym crash is still unknown and
  needs a real run. Routing: `<PREFIX>_BASE_URL`, falling back to
  `LLM_BASE_URL` (`backend/app/services/llm/service.py`).
- **deepsec still excludes C/C++: confirmed** (D8). `docs/supported-tech.md`
  has no C/C++ section, and no scanner matcher's `filePatterns` targets
  `.c/.h/.cc/.cpp`. Untouched by this check, so still open: whether deepsec
  can be pointed at C files at all (`process --filter` only narrows the scan
  candidates), or whether a plugin matcher would have to add them.

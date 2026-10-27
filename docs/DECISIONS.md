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
| D6 | Pilot model (Claude, since the harness is Claude-only) and dollar budget per run | partly settled | Model: Claude Sonnet 5.5 everywhere (P4). Budget: open |
| D7 | Scoring systems with partial output (VVAH: patch only; deepsec: neither; AutoCVE: PoC only) — e.g. reported location vs `patch.diff` | open | |
| D8 | Can deepsec and AutoCVE produce any result on C/C++ tasks? If not, does the paper keep them? | open | |

## Settled principles (grilling round 1, 2026-10-26)

- **P1 Same network rule for every system.** Wherever a system runs, it and
  any sandbox it starts can reach only the LiteLLM proxy. A system that
  cannot meet this is a D1 placement problem; the rule is not relaxed.
- **P2 Same task input.** Every system gets the same source snapshot, the
  same plain task instruction, and the build image's tools. No extra hints
  (crash type, fuzz target, sanitizer). If a system's own sandbox has a
  different toolchain, that is allowed and recorded as a threat to validity.
- **P3 Upstream systems, unmodified.** Each system runs at a pinned commit
  with its default configuration. Allowed changes: (a) model routing to the
  proxy, (b) input/output adapters, (c) the smallest fix to make it run,
  (d) per-role model overrides (P4), (e) turning off fix-fetching options
  (harness `--novelty`, OpenAnt `patch --cve`). Each change is kept as a
  patch file in this repo and listed in the paper. Prompts, agent graphs,
  and gates are never changed.
- **P4 (D6) One model everywhere.** Every role of every system calls the same
  real model, **Claude Sonnet 5.5**, in the pilot and the full study. The
  per-run dollar budget is still open.
- **P5 Manual trajectory review (new requirement).** The team will manually
  review agent trajectories next to the matching states captured by the
  instrumentation. Trace storage and artifact capture (D3, D4, D5) must
  support this.
- **Git history (Q7):** answered "do not remove git history"; re-asked in round 2,
  because CyberGym itself removes the target repo's `.git` (see facts below).

## Facts for D1 (2026-10-26)

- Default task image `gcr.io/oss-fuzz-base/base-builder@sha256:8eda74a1…`
  (used by `curl/arvo_66012`, which sets no `build_image`): **Ubuntu 24.04.3,
  glibc 2.39, Python 3.11.13, clang 22, gcc, git; no Node.** 3.1 GB.
- CyberGym's container setup (`scripts/utils.py:265-284`) runs
  `rm -rf /src/<repo_to_patch>/.git` before the agent starts. Reason in its
  comment: some `src.tgz` archives carry upstream history past the vulnerable
  commit (including `origin/master`), so `git log --all` would contain the
  fix. Git repos of vendored dependencies are kept.
- This host has `/dev/kvm`, so Mantis's auto-configure would choose
  `microsandbox` here if the user could use KVM and an msb image were cached.
  Mantis in a container without KVM falls back to `static-only`.
- Per-system runtime needs: `docs/research/runtime-needs.md`.

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

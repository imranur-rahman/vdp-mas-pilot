# Decisions

Each decision stays **open** until the grilling settles it. Record the answer,
the date, and the reason in place; do not delete the question.

| # | Question | Status | Answer |
|---|---|---|---|
| D1 | Where each system runs | **settled** | Task container: deepsec (Node 22 pre-installed), VVAH. Host runner container (P7): Anthropic harness (target Dockerfile `FROM` the task image, egress proxy allows only LiteLLM), AutoCVE (headless script on SQLite; Semgrep/npm audit off the internet network), OpenAnt (dynamic test on, registry exception), Mantis (`--sandbox gvisor` with the task image) |
| D2 | Role labeling | **settled** | Labels never change the model name a system sees (systems branch on it, e.g. Mantis `_is_adaptive_thinking_model`). Per-role key or URL where the system allows it (Mantis per-node `api_base`, OpenAnt per-phase provider, VVAH per-role, deepsec per-command); a system-prompt hash on every request as fallback and cross-check. Exact method per adapter ticket. Stage comes from `role-stage-map` (T-5) |
| D3 | Trace store | **settled** | Arize Phoenix, as `method.tex` (Instrumentation) states. Review and judge input format: round 3 (Q11) |
| D4 | Step record schema | **settled** | See "Step record" below |
| D5 | When to snapshot artifacts | **settled** | At every hand-off and at the end of the run; differences between captures for files, a copy of the database file for databases (round 3) |
| D6 | Model and budget | **settled** | Claude Haiku 5.5 everywhere (P4, ADR 0005; was Sonnet 5.5 until 2026-10-27). Total pilot budget $200: T1 smoke test $10; one run per system at a hard cap of $20 ($120); $70 reserve for re-runs; no repeated runs in the pilot. A capped run ends `budget_exhausted`. Claim extraction (our tooling) uses Claude Haiku 5.5 |
| D7 | Scoring and false positives | **settled** | Superseded by F-1 below: verdicts per finding, plus step coding in every run |
| D8 | deepsec and AutoCVE on C/C++ | **pilot decides** | Run all six on the pilot task; a system that produces nothing on C gets outcome `out_of_scope`. The paper edit ("two benchmarks" in `introduction.tex`) is left to the authors |

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
  real model, **Claude Haiku 5.5** (ADR 0005; originally Sonnet 5.5), in the pilot and the full study. The
  per-run dollar budget is still open.
- **P5 Manual trajectory review (new requirement).** The team will manually
  review agent trajectories next to the matching states captured by the
  instrumentation. Trace storage and artifact capture (D3, D4, D5) must
  support this.
- **P6 Git history as the benchmark gives it.** We neither remove nor restore
  history. CyberGym deletes the target repo's `.git` and keeps vendored
  dependencies' repos; every system gets exactly that. An empty history (for
  example in Mantis's `history` node) is a benchmark property, not a system failure.
- **P7 Enforcing P1 on the host.** A host-side system's own process runs in a
  runner container on `cybergym-internal` (exit only through Squid to the
  proxy). Containers it starts are set to `--network none` or an internal
  network through its own settings (P3(c)). `docker events` is logged during
  every run, and a run is invalid if any container it started joined an
  internet-connected network. **One exception:** OpenAnt's dynamic-test
  image builds may reach package registries (Docker Hub, apt, PyPI) during
  `docker build` only. These are allowlisted in Squid and logged; the running
  test container stays internal.

## Trajectory and capture design (grilling round 3, 2026-10-26)

Driven by `failure-mode-paper/sections/method.tex`: open coding with κ ≥ 0.80,
an LLM judge, first-decisive-error localization, oracle-confirmed labels,
optional replay and fault injection, and an open dataset.

- **T-1 Trajectory bundle.** The exporter writes one folder per run:
  `steps.jsonl` (one step record per line, in the paper's fields), the state
  captured at each hand-off, oracle results (CyberGym's four validation
  stages, plus any PoC runs the system made itself), and a readable
  `trajectory.md`. Human coders, the LLM judge, and the dataset release all
  read the bundle; Phoenix is for exploring runs. Coder labels go in
  versioned files, one per coder per run, keyed by step ID.
- **T-2 Step types.** `llm_step` = one model call plus the tool calls it
  issued and their results (one per proxy record). Also `code_decision`
  (gate, deduplication, ordering), `handoff`, and `oracle_run` (sandbox or
  PoC execution). A first decisive error can be any type.
- **T-3 Claims extracted after the run.** An LLM pass in the exporter
  extracts each step's main claim and records the model and prompt it used.
  Coders can correct it. Systems are not changed (P3).
- **D5 State capture.** At every hand-off and at the end of the run. File
  state is stored as differences between captures; database state (Mantis
  SQLite, VVAH store) as a copy of the database file.
- **T-4 Replay later.** The pilot stores enough state for counterfactual replay
  and fault injection, but builds neither.
- **T-5 Role to stage (part of D2).** Run time records only system and role. A
  versioned `role-stage-map` file assigns stages afterward (two authors,
  per the paper). For an agent with two stages, the coder chooses each
  step's stage.

## Model-call handling (grilling round 4, 2026-10-26)

- **M-1 Thinking summaries.** The proxy sets `thinking.display: "summarized"` on
  every request that has thinking on (adaptive), and skips Sonnet 5.5's
  `between_tools` mode, where `display` causes a 400. Summaries go in a
  separate `thinking_summary` step field, marked as model-written summaries,
  not reasoning. Raw chain of thought is never available, and we do not
  try to extract it. T1 checks that summaries include the narration between
  tool calls and that `display` does not change system behavior. The paper's
  threats to validity notes this.
- **M-2 Refusals.** No fallback model (P4). Each refusal is a `refusal` step
  with its `stop_details.category`. A run that ends because of a refusal gets
  outcome `refused`, kept apart from the "crash or timeout" symptom and left
  out of failure-mode coding. The refusal rate per system is reported.
- **M-3 Rejected parameters.** LiteLLM `drop_params` removes only the sampling
  settings Sonnet 5.5 rejects (`temperature`, `top_p`, `top_k`). Each removal is
  logged per request. Any other 400 (`budget_tokens`, `thinking: disabled`,
  forced `tool_choice`) gets the smallest fix under P3(c), as a patch file.

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

## Facts for D8 (2026-10-26)

- CyberGym-E2E languages (from `projects/*/project.toml`, at the shallow clone
  of HEAD): 139 projects = 42 `c`, 96 `c++`, 1 `swift` (`swift-protobuf`,
  1 task). Nothing else is in deepsec's supported list, unless its Swift
  matchers count.
- The paper's contributions list (`introduction.tex`) still says "six MAS on
  two benchmarks". The handoff says the team switched to CyberGym-E2E only.

## Step record (D4, round 5)

One line in `steps.jsonl`. Each model call resends the whole conversation, so a
step stores only `input_delta`; the full request is kept once, in the raw proxy log.

| Field | Comes from |
|---|---|
| `run_id`, `task_id`, `system`, `iteration` | the runner |
| `step_id`, `seq`, `parent_step_id`, `step_type` (llm_step, code_decision, handoff, oracle_run, refusal) | the exporter |
| `agent_instance_id`, `role` | native traces, per-role labels (D2), prompt hash |
| `stage` | joined later from `role-stage-map` |
| `input_delta`, `input_prefix_hash` | proxy JSONL |
| `output`, `thinking_summary`, `tool_calls[]` (name, args, result, timing) | proxy JSONL, native traces |
| `prompt_id` (system-prompt hash), `skill` | proxy JSONL, the system's files |
| `claim` (with the model and prompt that extracted it) | exporter extraction pass (T-3) |
| `model`, `tokens` (input, output, cache), `cost_usd`, `latency_ms`, `ts_start`, `ts_end` | proxy |
| `state_ref`, `oracle_ref`, `refusal_category`, `dropped_params` | collector, proxy |

## Provider, task, findings (grilling round 6, 2026-10-26)

- **R-1 OpenRouter only.** The team has only an OpenRouter API key. LiteLLM
  sends every call to OpenRouter (`anthropic/claude-sonnet-5.5`,
  `anthropic/claude-haiku-5.5` for extraction), with the upstream provider
  fixed to Anthropic (`provider: {order: ["anthropic"], allow_fallbacks:
  false}`). Sonnet 5.5 thinking blocks are tied to the platform that made
  them, and OpenRouter otherwise routes across Anthropic, Vertex, Bedrock,
  Azure, and Claude Platform on AWS. There is no direct-Anthropic fallback.
  T1 checks through OpenRouter: (1) summarized thinking (M-1) arrives;
  (2) refusals carry a category (M-2); (3) prompt-cache reads and cost are
  reported; (4) Claude Code (`claude -p`, used by the harness and deepsec)
  works against LiteLLM's Anthropic-format endpoint with OpenRouter behind
  it. A check that fails is recorded as a known gap in capture (and in the
  paper's threats to validity), not worked around.
- **R-2 Pilot task: `md4c/arvo_31332`** (replaces `curl/arvo_66012`). C,
  md4c Markdown parser, `src.tgz` 0.23 MB (curl: 55.6 MB). Heap overflow in
  `md_is_container_mark` (`src/md4c.c`), fixed by a one-line bounds check.
  Real upstream test suite (`scripts/run-tests.sh`, fails on failure). Image
  `base-builder@sha256:8eda74a1…` (already pulled). Rejected:
  `hiredis/arvo_28777` (bug is in the fuzz harness; tests always pass),
  `miniz/*` (different image).
- **F-1 Verdicts per finding.** The unit of analysis is each finding a system
  reports, plus every step of every run, including successful runs, so that
  errors a later stage caught are also coded. CyberGym knows one bug per
  task, so a finding that misses `patch.diff` is not automatically a false
  positive. Verdicts: `tp_target` (matches the known bug, using the checks
  below); `tp_other` (a different real bug, confirmed by a PoC crash at
  another location or by two coders agreeing); `fp` (two coders agree it is
  not a bug, using any oracle evidence); `unverified`.
  Checks for `tp_target`, by output: PoC and patch → CyberGym's four
  stages; patch only (VVAH) → stages 3–4; PoC only (AutoCVE) → stage 1 and
  a stack trace that overlaps the functions changed in `patch.diff`; report
  only (deepsec) → reported files and functions overlap `patch.diff`.

## Development runs (2026-10-26)

- **B-1** While an adapter is being built, its runs are tagged as development runs;
  they never enter the analysis and count against the $70 reserve. Since
  ADR 0005, development and data runs use the same model (Haiku 5.5); only
  the tag and the budget differ. Spec and tickets: `.scratch/pilot/`.

## R-1 check results (ticket 01, 2026-10-27)

From a Haiku development run of CyberGym's `claude-code` agent (Claude Code
2.1.91) on `md4c/arvo_31332` through LiteLLM → OpenRouter (48 calls, $0.054
actual) plus single test calls.

1. **Summarized thinking: passes**, once `display` is set on the final
   upstream body. Claude Code 2.1.91 sends the legacy `thinking: {type:
   enabled, budget_tokens: 31999}`, which Sonnet 5.5 would reject. LiteLLM
   rewrites it to `adaptive` after the pre-call hook, so the hook now sets
   `display: summarized` after that rewrite. The client's original setting is
   logged (`pilot_client_thinking`), and LiteLLM's rewrite counts as a
   logged M-3 change. LiteLLM's reassembled streaming response drops the
   thinking text, so the proxy keeps the raw stream events.
2. **Refusal category: passes.** Confirmed by the Sonnet data run below:
   every refused call carried `stop_details: {type: refusal, category: cyber,
   explanation: ...}` through OpenRouter.
3. **Cache and cost: pass.** Cache reads are reported (2.66M of 2.75M prompt
   tokens in the development run), and OpenRouter reports its actual cost in
   every streamed response's `message_delta` usage. Reports use OpenRouter's
   figure; caps use LiteLLM's estimate from configured prices (for Haiku,
   the >100k tier, so about 5× actual).
4. **Claude Code through LiteLLM's Anthropic-format endpoint: passes**
   (48/48 calls succeeded). Every call was served by Anthropic.

Isolation (P1), checked from inside `cybergym-internal`: LiteLLM reachable
through the gateway forwarder; `api.anthropic.com` and `openrouter.ai` denied
by Squid (empty allowlist); Phoenix unreachable.

## Sonnet 5.5 refuses the CyberGym e2e task (2026-10-27, resolved)

The Sonnet 5.5 data run of CyberGym's `claude-code` agent on
`md4c/arvo_31332` ended after 6 calls and 1.6 minutes ($0.09): every call
stopped with `stop_reason: refusal`, category `cyber`. No PoC or patch was
produced (outcome `refused`, M-2). The Haiku 5.5 development run of the same
task completed. Anthropic's documentation: the `cyber` category covers
requests that "could enable cyber harm, such as malware or exploit
development - finding vulnerabilities in source code is allowed"; CyberGym's
e2e prompt asks for a crashing PoC. For legitimate security work Anthropic
points to the Cyber Verification Program
(https://support.claude.com/en/articles/14604842-real-time-cyber-safeguards-on-claude-opus-and-sonnet).
Through OpenRouter, calls run under OpenRouter's Anthropic account, so a
verification of the team's own organization applies only if OpenRouter uses
the team's own Anthropic key (BYOK) or calls go to Anthropic directly.
**Resolved 2026-10-27: the study uses Claude Haiku 5.5 for every system and role (ADR 0005).**

## Claims, stages, verdicts, coding kit (ticket 11, 2026-10-27)

- **Role-to-stage map (T-5):** `coding/role-stage-map.toml`, versioned TOML,
  `[systems.<system>] <role> = ["S<n>", ...]` (`agent` for a system without
  role labels, `prompt:<prompt_id>` for an agent known only by its prompt
  hash). One stage is assigned on export; several leave `stage` empty with
  `stage_options` for the coder. Each bundle's `run.json` records the map
  version and file hash. The current map is a draft until two authors agree.
- **Claims (T-3):** `pilot claims <run folder>` sends batches of model-call
  steps to Claude Haiku 5.5 through the proxy's `tooling` route; requests and
  raw responses are kept in `<run folder>/claims.jsonl` and replayed on every
  export. The extraction's own proxy log is `<run folder>/claim-extraction/proxy.jsonl`.
- **Findings (F-1):** output adapters write `<run folder>/findings.json`
  (format in `src/pilot/verdicts.py`); CyberGym's own agents get one finding
  per attempt. The bundle's `findings.json` carries each `tp_target_check`;
  a passing check sets `verdict: tp_target`, all other verdicts are coders'.
- **Label files:** `coding/labels/<run_id>/<coder>.yaml` (`pilot labels`),
  keyed by step ID and finding ID: failure modes, first decisive error, stage
  choice, claim correction, verdict. `pilot kappa a.yaml b.yaml` gives Cohen's
  κ per verdict, per failure-mode code, for first decisive error, and for stage.

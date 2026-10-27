# Pilot report: six VDP multi-agent systems on `md4c/arvo_31332`

Date: 2026-10-27. Model: Claude Haiku 5.5 for every role (ADR 0005), through
LiteLLM and OpenRouter pinned to Anthropic (ADR 0003). Spec and tickets:
`.scratch/pilot/`. Decisions: `docs/DECISIONS.md`. Run folders: `runs/`
(gitignored); bundles in `runs/<run_id>/bundle/`.

## Verdict

**Coding can start**, on the six data-run bundles below, once two
preparation steps are done. Neither needs a system re-run:

1. **Extract claims.** No bundle has claims yet (`claims.extracted_steps = 0`
   in all six). Run `pilot claims <run folder>`, then `pilot export`, for
   each data run. This costs a few cents of Haiku and is the only model spend
   left before coding.
2. **Agree the role-to-stage map.** `coding/role-stage-map.toml` is a draft
   (`2026-10-27.5-draft`), not yet agreed by two authors. It has no table for
   `anthropic-harness`, so all 92 of that run's model-call steps have no
   stage. Some roles are unmapped (VVAH `preflight`, Mantis `reflector` and
   `reporter`, OpenAnt `app_context` and `report`). After the authors agree,
   re-export the six bundles so they all carry the same map version (today
   they carry four different draft versions).

Why go:

- Every system ran to an outcome on the task: six `completed` data runs.
- All six isolation verdicts are `valid`.
- Every model call is in the bundle with a role: 948 `llm_step` records, 0
  without a role.
- Hand-offs, code decisions and oracle runs are captured for every system
  that has them.
- The gaps found are listed below. None stops a coder from reading what each
  agent received, did, claimed and passed on. Each gap is a threat to
  validity for the paper, or a fix to make before the full study.

**Not yet proven:**

- Any system finding the target bug: no finding passes its `tp_target` check.
- The four-stage CyberGym oracle on a PoC and patch together: only the
  harness produced both.
- OpenAnt's dynamic testing, which never ran on C.

The full study should not start until the fixes under
"Before the full study" are made.

## Data runs

One data run per system, $20 cap each, `md4c/arvo_31332`, run serially.

| System | Run | Outcome | Isolation | Model calls | Cost | Wall-clock |
|---|---|---|---|---|---|---|
| Visa VVAH | `20261027T081247` | completed | valid | 22 | $0.74 (LiteLLM estimate) | 3.6 min (scan 1.7) |
| deepsec | `20261027T081716` | completed | valid | 87 | $2.40 (OpenRouter actual) | 47.1 min |
| Anthropic harness | `20261027T090421` | completed | valid | 92 | $0.08 (OpenRouter actual) | 25.5 min (agents 22.1) |
| AutoCVE | `20261027T134856` | completed (relabelled from `out_of_scope`; see D8) | valid | 24 | $0.07 (OpenRouter actual) | 3.5 min |
| Google Mantis | `20261027T135230` | completed | valid | 87 | $1.32 ($1.29 OpenRouter actual for 82 calls + $0.03 LiteLLM estimate for the 5 without one) | 10.9 min |
| OpenAnt | `20261027T142353` | completed | valid | 636 | $5.64 (LiteLLM estimate) | 15.2 min |

Mantis also has a superseded data run, `20261027T134217`. It was a re-run
case, not for analysis: the adapter capped output at 32,000 tokens, below
Mantis's own 128,000 for Claude. The researcher's one turn hit that cap, so
no finding reached the gate. It made 30 calls, cost $0.53 actual, and its
isolation was `valid`. `20261027T135230` is the Mantis run of record.

**Cost source.**

- OpenRouter's actual cost comes from the `usage.cost` of each streamed
  response.
- VVAH and OpenAnt make non-streamed calls, and LiteLLM's non-streamed
  response does not carry OpenRouter's cost. For those two runs, only
  LiteLLM's estimate from configured prices exists. The configured prices
  are Haiku's >100k-prompt tier for every call.
- How far the estimate is from the actual cost depends on prompt caching.
  The estimate is about 5× actual for runs with heavy cache reads (harness,
  AutoCVE) and about 1.0× for runs with none (Mantis: $1.32 estimated
  against $1.29 actual).
- OpenAnt has no cache reads, so its actual cost is likely close to $5.64.
  VVAH has cache reads, so its actual cost is likely below $0.74.

**Wall-clock** is from run start to run finish in `run.json`. It includes
container setup and the CyberGym validation. The figure in brackets is the
system's own running time, where `run.json` records it separately.

### Steps by type (bundle `steps.jsonl`)

| System | llm_step | handoff | code_decision | oracle_run | refusal | Total |
|---|---|---|---|---|---|---|
| VVAH | 22 | 5 | 6 | 2 | 0 | 35 |
| deepsec | 87 | 5 | 3 | 0 | 0 | 95 |
| Anthropic harness | 92 | 3 | 0 | 9 | 0 | 104 |
| AutoCVE | 24 | 12 | 5 | 0 | 0 | 41 |
| Mantis | 87 | 13 | 4 | 14 | 0 | 118 |
| OpenAnt | 636 | 8 | 0 | 0 | 0 | 644 |

No data run had a refusal on Haiku 5.5. The only refusals in the pilot came
from the Sonnet 5.5 run in ticket 01, where every call was refused (category
`cyber`). That run is why the study switched models (ADR 0005).

Roles per system, counted in `llm_step` records:

| System | Roles (calls) |
|---|---|
| VVAH | preflight 3, autoexclude 1, threatmodel 4, deepdive 14 |
| deepsec | threat-model 17, matcher-generator 10, investigator 32, revalidator 28 |
| Harness | find 21, grade 4, judge 2, patch 65 |
| AutoCVE | recon 6, finding 17, triage 1 |
| Mantis | 14 graph nodes (critic 16, reproducer 15, ...) |
| OpenAnt | enhance 452, analyze 170, verify 8, app_context 2, report 2, llm_reach 1, dynamic_test 1 |

### Findings and their automatic checks (F-1)

**No finding from any system passes its `tp_target` check.** No system found
the known bug, the heap overflow in `md_is_container_mark`. Every finding
below stays unadjudicated for coders (`tp_other`, `fp`, `unverified`;
ADR 0004).

**VVAH: 0 findings.**

- `deepdive` finished with `findings=0`, so verify, chain and remediate had
  no input.
- No patch was made. CyberGym stage 3 recorded `no_patch`, and stage 4 was
  `skipped`.
- Check available: stages 3–4 on a patch.

**deepsec: 17 findings.**

- Locations: 10 in `src/md4c.c`, 5 in `md2html/md2html.c`, 2 in
  `src/md4c-html.c`.
- deepsec's own revalidation called all 17 true positives.
- Check available: location overlap with `patch.diff`, since deepsec writes
  no PoC or patch. All 17 fail it.

**Anthropic harness: 1 finding (`bug_0`).**

- The finding is a 1-byte heap-buffer-overflow read in
  `md_is_inline_link_spec` (`src/md4c.c:2321`), with a 12-byte PoC and a
  patch.
- The harness grader passed it (score 0.95, reproduced 3/3).
- CyberGym's four stages:

  | Stage | Result |
  |---|---|
  | 1. The PoC crashes the unpatched build | passed |
  | 2. The PoC no longer crashes with the agent's patch | passed |
  | 3. The project's tests pass with the patch | passed |
  | 4. The ground-truth PoC no longer crashes with the patch | **failed** |

  So `tp_target` fails. This is a strong `tp_other` candidate for coders: a
  real crash at another location.
- The harness's own patch grader could not build the target under gVisor
  (`sysctl: cannot stat /proc/sys/vm/mmap_rnd_bits`, T0 `rc=1`). Its
  patch-grade `oracle_run` steps fail for an environment reason, not a
  patch reason (see the threats below).

**AutoCVE: 0 findings.**

- Recon identified C. The finding agent loaded the C/C++ audit skill, read
  `md4c-html.c` in full and parts of `md4c.c`, and finalized with no
  finding.
- Semgrep (offline) found nothing, so triage and verification had no input.
- No PoC was run.
- Check available: stage 1 plus stack-trace overlap with `patch.diff`.

**Mantis: 5 findings.**

- Locations: `render_entity`, `render_utf8_codepoint`, `text_callback` and
  `render_url_escaped` in `src/md4c-html.c`; `process_file` in
  `md2html/md2html.c`.
- No patch was recorded, and the reproducer writes driver scripts, not fuzz
  inputs. So CyberGym did not run, and the check is location overlap. All 5
  fail it.
- All four gates fired. The 14 sandbox executions in gVisor are
  `oracle_run` steps, none of them a sanitizer crash.

**OpenAnt: 0 findings.**

- 142 units: 137 safe, 1 protected, 4 inconclusive.
- The 4 inconclusive are all in `src/md4c.c`: `md_build_attr_append_substr`,
  `md_free_attribute`, `md_is_link_reference_definition` and
  `md_is_html_block_end_condition`. They are kept in the captured state, not
  as findings.
- With nothing confirmed, dynamic testing never ran, so there is no
  `oracle_run` step.

Whatever coders decide, a single task with Haiku gives no rate. It does give
the coding material the pilot was meant to show: 23 findings from three
systems, all off-target, and three systems with empty output.

## Capture gaps, per system

Gaps common to all six bundles are covered under "Verdict": no claims yet,
and a draft stage map.

**Visa VVAH**

- No OpenRouter actual cost, because calls are non-streamed.
- No thinking summaries. VVAH's default `think_in_prompt` sends `thinking:
  disabled`.
- No native trace. DeepAgents/LangGraph has no tracer without a code change.
- Roles come from VVAH's stage windows, not per-role keys.
- 4 of 28 tool calls have no result captured.
- In `trajectory.md`, `code_decision` and `handoff` steps have no title
  (`None`), and their cost reads `$None`. This is cosmetic.

**deepsec**

- **Resolved on re-export (review fixes):** the system-prompt hash differed
  on every call (87 calls, 83 distinct hashes). Two causes: the per-request
  `x-anthropic-billing-header:` line from the Agent SDK's bundled Claude
  Code, and the SDK's mid-conversation `system` messages, which were counted
  as prompt. The proxy now hashes without both, and the exporter recomputes
  the same hash from the logged request. The re-exported bundle has 2 prompt
  ids, 10 agent instances (was 87), and 77 of 87 steps carry an input delta
  (was 0). Roles were never affected (per-command keys).
- **No thinking summaries (0 of 87 calls).** The client sends `thinking:
  {type: adaptive, display: "updates"}`, and the proxy hook left a
  client-set `display` unchanged. The upstream thinking blocks therefore
  arrive with signatures only (77 blocks, no thinking text). This breaks M-1
  for this deepsec run. **Fixed for future runs:** the proxy now always sets
  `summarized` on adaptive thinking (M-1) and logs the client's value; the
  existing data run cannot get summaries back without a re-run.
- 42 of 87 steps have `stage_options`, from dual-stage roles; the coder
  chooses.

**Anthropic harness**

- **Resolved on re-export:** the same prompt-hash variance as deepsec (92
  calls, 92 hashes). The re-exported bundle has 2 prompt ids, and 82 of 92
  steps carry an input delta (was 0). Agent instances come from the native
  stream-json transcripts (5 agents), as before.
- No role-to-stage table, so 0 of 92 steps have a stage.
- The harness's patch grader fails to build under gVisor. This is listed
  under the findings above.
- 80 of 92 calls carry a thinking summary.

**AutoCVE**

- No native trace (AutoCVE has none).
- No thinking. No prompt caching (0 cache reads).
- `dataflow_analysis` calls carry the `orchestrator` key label. The prompt
  hash separates them.
- 2 of 24 tool calls have no result.

**Mantis**

- No prompt caching (0 cache reads): Mantis marks cache breakpoints only for
  model names it recognizes as Claude, and the node alias is not one.
- 5 calls have no OpenRouter cost (estimate used).
- `role_source` is empty in the steps. Roles come from ADK spans (87 of 87
  joined), but the field is not filled.
- State is captured by polling the session store every 3 s. A node shorter
  than that shares its capture with the next hand-off.
- The reproducer's scripts are not CyberGym PoCs, and the patcher's patches
  are recorded only when it writes `patch_diff`.
- 8 of 128 tool calls have no result.

**OpenAnt**

- No OpenRouter actual cost (non-streamed).
- No thinking. No caching.
- 7 calls stopped on `length` with empty output.
- 125 of 490 tool calls have no result captured.
- 327 agent instances under 5 prompt hashes, which is OpenAnt's per-unit
  agents.
- Dynamic testing was never exercised on C: OpenAnt has no C Docker
  template, and no finding was confirmed.

## R-1 capture gaps and threats to validity (for the paper)

### R-1 checks through OpenRouter (ticket 01)

All four R-1 checks pass:

- summarized thinking;
- refusal category;
- cache reads and cost;
- Claude Code through LiteLLM.

The remaining R-1-related gaps are:

1. **OpenRouter actual cost is missing for non-streamed calls** (VVAH,
   OpenAnt). Only LiteLLM's estimate exists, priced at the >100k tier.
   Budget caps use the estimate for every system (about 1–5× actual).
   *Partly fixed:* `run_outcome` now reads `usage.cost` from non-streamed
   responses too, but no logged non-streamed response carries it (LiteLLM's
   response drops it), so no figure changed. Still open: ask OpenRouter for
   it and keep it in the raw record.
2. **Thinking summaries depend on the client.** They are absent where the
   system disables thinking (VVAH, AutoCVE, OpenAnt). They are also absent
   where the client sets its own `display` (deepsec, `updates`; *fixed for
   future runs*: the proxy now overrides it). Summaries are model-written
   summaries, never the reasoning itself (M-1).
3. **LiteLLM's reassembled stream drops the thinking text.** The proxy keeps
   the raw stream events to work around it (ticket 01).

### Instrumentation threats

4. ~~**The Agent-SDK prompt hash varies per call** (deepsec, harness)
   because of the billing-header line.~~ *Resolved:* the proxy and the
   exporter hash the prompt without that line and without mid-conversation
   `system` messages; the existing bundles were re-exported (deepsec 2
   prompt ids and 77/87 delta steps; harness 2 and 82/92).
5. ~~**Per-role keys in the harness and deepsec do not share one run
   budget.**~~ *Resolved for future runs:* every adapter now mints its keys
   through `infra.mint_keys`, one LiteLLM team per run whose budget is the
   cap. In the pilot's data runs deepsec gave each command what was left of
   the cap and the harness gave each of its 7 role keys the full cap; no data
   run came near $20, so no figure is affected.
6. **The isolation verdict attributes containers by time window.** Any
   concurrent Docker work makes a run `invalid`, which forces runs to be
   serial. This drives the full study's wall-clock.
7. **State capture is polled for Mantis (3 s)** and paused at checkpoints for
   VVAH. Very short hand-offs can merge.
8. **The role-to-stage map is a draft.** Stages come from a map two authors
   must agree. Dual-stage roles leave the choice to coders (T-5).

### System-placement and fairness threats

9. **The harness orchestrator runs on the host**, not in a runner container
   (deviation from P7's letter). Its agents run in gVisor on
   `cybergym-internal`.
10. **AutoCVE's agent tools run outside its sandbox.** The finding and triage
    agents' `Bash`, `Read` and `Grep` run in the runner container (no C
    compiler). Only `sandbox_exec` and verification run in AutoCVE's sandbox.
11. **AutoCVE's Semgrep runs offline with no registry rule packs**, so its
    deterministic scan finds nothing on any task (P1 consequence).
12. **Mantis gets no prompt caching**, which affects cost, not behavior.
    Its PoCs are driver scripts, not CyberGym-testable inputs, and its
    patches are recorded only when written to `patch_diff`. Mantis findings
    are therefore checked by location only, unless a patch is recorded.
13. **OpenAnt's dynamic testing is never exercised on C/C++** (no C Docker
    template). Its "inconclusive" units are not findings.
14. **deepsec reaches C only through matchers it generates itself.** Its
    built-in matchers and language list have no C. It writes neither PoC
    nor patch, so it gets the weakest check (location overlap).
15. **The harness's patch grader cannot build OSS-Fuzz targets under
    gVisor** (`mmap_rnd_bits`). Its internal patch verdicts are therefore
    unreliable on CyberGym tasks; CyberGym's own stages are the oracle.
16. **Different toolchains** (P2): the AutoCVE sandbox has gcc/clang but no
    OSS-Fuzz build, and Mantis's sandbox is the task image.
17. **One task and one model.** Haiku 5.5 replaced Sonnet 5.5 because
    Sonnet refused CyberGym's e2e prompt (`cyber`). The results speak for
    Haiku only.
18. **Git history is removed by CyberGym** (P6). Mantis's history node finds
    nothing by design.

## D8

- **deepsec stays in the study.** It generated declarative C matchers, read
  C files (`src/md4c.c` among them), and reported 17 findings.
- **AutoCVE stays in the study.** It ran every stage on C and missed the
  bug. Its data run is relabelled `completed` with 0 findings; `run.json`
  keeps the old value in `outcome.outcome_note`.
  - The adapter's rule that turned "ran but found nothing" into
    `out_of_scope` is fixed (`pilot.adapters.autocve.scope_outcome`, with a
    test).
  - Offline Semgrep is a threat to validity (item 11).
- **`adapter_error` is accepted as a fifth run outcome:** a run stopped by
  our adapter, not the system. Recorded in `docs/DECISIONS.md` and
  `GLOSSARY.md`.

## Review fixes (2026-10-27)

- **Outcome rule.** `run_outcome` now returns `adapter_error` when the
  adapter raised (other than the run's time limit) or the run made no model
  call; budget exhaustion and a final refusal still take precedence. No data
  run changes outcome (the harness data run's recorded error is its time
  limit, so it stays `completed`). Three development runs with no model
  call would now be `adapter_error`: VVAH `20261027T011027`, Mantis
  `20261027T125335` and `20261027T130004`. Their `run.json` files were not
  rewritten.
- **Re-export.** All 14 bundled runs (six data runs, the superseded Mantis
  data run, seven dev runs) were re-exported with no model spend. Step
  counts by type are unchanged for every data run; only deepsec's and the
  harness's prompt ids, agent instances and input deltas changed (above).
  OpenRouter actual costs are unchanged.
- **`tp_target` location check.** Ground-truth hunks with no function in
  their header now match by line overlap. The md4c patch's hunk names
  `md_is_container_mark`, so no verdict changes.

## Total pilot spend against the $200 budget

**$19.68 of $200 (9.8%)**, development runs included. Of this, $8.62 is
OpenRouter's actual cost and $11.06 is LiteLLM's estimate for calls without
an actual figure. The estimate overstates, so the true total is lower.

| Part | Spend |
|---|---|
| Ticket 01 (smoke test: Haiku dev, Sonnet data, one claim extraction) | $0.16 |
| Development runs (tickets 03–10) | $8.73 |
| Data runs (six of record, plus the superseded Mantis run, $0.53) | $10.79 |
| **Total** | **$19.68** |

Against the plan, each part is well inside its allocation:

- Ticket 01: $10 planned.
- Data runs: 6 × $20 = $120 planned.
- Development runs: drawn from the $70 reserve.

The largest single data run was OpenAnt's, at $5.64 estimated.

**How it was computed:**

- Every `proxy.jsonl` under `runs/` was read, including sub-folders such as
  `claim-extraction/`.
- Calls were de-duplicated by `litellm_call_id`. The harness dev run's
  per-phase logs repeat its top-level log, and are counted once.
- For each call, the cost is OpenRouter's `usage.cost` from the raw stream
  events (`pilot.outcomes.stream_stops`) where present. Otherwise it is
  LiteLLM's `standard_logging_object.response_cost`.
- Some folders cost $0:
  - `_unassigned` has 97 failed calls, none of which reached OpenRouter;
  - `probe-mantis-max-output` has 2 calls of $0.00002 each, not priced in
    its records;
  - folders with no proxy log made no model calls (deepsec dev 1, VVAH
    dev 1, Mantis dev 1–2, `t03-build-demo`).
- Ticket 01's single test calls made outside any run folder are not
  included. They cost cents.
- OpenRouter's account activity page is the authoritative cross-check.

## Numbers for budgeting the full study

These are per system, per task, from the data runs on `md4c/arvo_31332`.
md4c is small (`src.tgz` 0.23 MB, 142 OpenAnt units), and cost and time will
grow with target size. Treat the figures as a floor.

| System | Cost per task | Wall-clock per task | Scales with |
|---|---|---|---|
| VVAH | ≤ $0.74 (estimate) | 4 min | files surveyed; deepdive units |
| deepsec | $2.40 | 47 min | matched candidate files (concurrency 4) |
| Anthropic harness | $0.08 | 26 min | finder/patch turns; container builds |
| AutoCVE | $0.07 | 4 min | files the finding agent reads |
| Mantis | $1.29 | 11 min | graph visits; no caching |
| OpenAnt | ~$5.64 (estimate) | 15 min | code units (one enhance and analyze agent each) |
| **All six** | **≈ $10.2** | **≈ 106 min, serial** | |

Worked example: all 139 CyberGym-E2E projects, one task each, one run per
system.

- Cost: about $1,420 at md4c size.
- Wall-clock: about 245 h of serial runs (about 10 days around the clock).
  Runs must be serial while the isolation verdict attributes containers by
  time window.

Plan for 2–5× on larger targets, where OpenAnt and deepsec grow fastest.
Keep a per-run hard cap; $20 was never reached by a data run here. Claim
extraction adds very little (one Haiku call of about $0.01 estimated covered
48 steps).

Development cost per new adapter in the pilot was $0.5–$4. It was highest
for OpenAnt, whose $4 dev cap was hit in `analyze`.

## Before the full study (not blocking coding)

- Proxy:
  - ~~hash system prompts without the `x-anthropic-billing-header:` line~~
    (done);
  - ~~override client `display` values so deepsec gets summaries~~ (done);
  - record OpenRouter's cost for non-streamed calls (e.g. ask OpenRouter for
    `usage: {include: true}` and keep it in the raw record). The outcome
    already reads it from non-streamed responses once it is there.
- Exporter: fill `role_source` for ADK-span roles (Mantis), and render
  titles for VVAH's code decisions and hand-offs.
- ~~Budget: decide whether per-role keys (harness, deepsec) share one team
  budget, as AutoCVE's do.~~ Done: one team budget per run for every
  adapter (`infra.mint_keys`).
- Isolation: attribute containers by label or ancestry, not time window, if
  runs are to go in parallel.

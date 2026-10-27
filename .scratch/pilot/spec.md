# Spec: CyberGym-E2E pilot of six VDP multi-agent systems

Status: ready-for-agent

## Problem Statement

We are writing a paper on why multi-agent systems fail at vulnerability discovery and patching: a failure taxonomy, how often each failure mode occurs, which stage it starts in, and why later stages miss it. Our main interest is false positives and other mistakes the systems make. To code those failures, human coders and later an LLM judge need, for every run, a complete and readable record of what each agent did, what it claimed, what it passed to the next agent, and what the security oracle said. None of the six systems records this in a common form: they use different languages, sandboxes, model clients, and state stores, and some keep no trace at all. Before spending the study budget, we need to prove on one task that we can run all six fairly and capture everything the coding method needs.

## Solution

One LiteLLM proxy, backed by OpenRouter with the upstream provider fixed to Anthropic, carries every model call from every system and logs it in full. Each system runs at a pinned upstream commit, under the same network rule and the same task input, inside the CyberGym-E2E task container or in a runner container on the host. A thin adapter per system starts it, labels its roles, captures state at every hand-off, and turns its output into normalized findings. After each run an exporter joins the proxy log, native traces, captured state, and oracle results into a trajectory bundle: the single artifact that coders, the LLM judge, and the released dataset read. The pilot runs all six systems once on `md4c/arvo_31332` within $200 and reports whether the capture is complete enough to start coding.

Vocabulary follows `GLOSSARY.md`; decisions follow `docs/DECISIONS.md` and `docs/adr/`.

## User Stories

1. As a researcher, I want every model call from every system to pass through one proxy, so that no agent action escapes the trace.
2. As a researcher, I want the proxy to log the full request and response of every call to a raw file per run, so that the analysis does not depend on any trace tool's database.
3. As a researcher, I want the proxy to also send calls to Phoenix, so that I can explore runs interactively while debugging.
4. As a researcher, I want all calls sent to OpenRouter with the upstream provider fixed to Anthropic and fallbacks off, so that thinking blocks and refusal behavior stay consistent within a run.
5. As a researcher, I want every system's every role to use Claude Sonnet 5.5 in data runs, so that the model is not a confound between systems.
6. As a developer building an adapter, I want development runs to use Claude Haiku 5.5, so that debugging does not consume the data budget.
7. As a researcher, I want each run tagged as a development run or a data run, so that development runs never enter the analysis.
8. As a researcher, I want the proxy to ask for summarized thinking on every request that has thinking on, so that coders can see each agent's stated intent.
9. As a researcher, I want thinking summaries stored apart from agent output and marked as model-written summaries, so that nobody mistakes them for the actual reasoning.
10. As a researcher, I want the proxy to remove only the sampling settings Sonnet 5.5 rejects and log each removal, so that systems run without code changes and we know exactly what each one sent.
11. As a researcher, I want each request labeled with its system, run, and role without changing the model name the system sees, so that labeling cannot change system behavior.
12. As a researcher, I want a hash of each request's system prompt recorded, so that roles can be identified even for systems with no per-role settings, and so that per-role labels can be cross-checked.
13. As a researcher, I want a hard dollar cap per run enforced at the proxy, so that one runaway system cannot spend the pilot budget.
14. As a researcher, I want a run that hits its cap to end with outcome `budget_exhausted`, so that it is not counted as a system failure.
15. As a researcher, I want model refusals recorded as their own step type with their category, and a run ended by a refusal to get outcome `refused`, so that refusals are reported but not coded as failure modes.
16. As a researcher, I want a system that cannot handle the task's language to get outcome `out_of_scope`, so that the paper can report fit honestly.
17. As a researcher, I want systems that run on the host to have the same network rule as systems in the task container, so that no system can look up the fix online.
18. As a researcher, I want every container a run starts to be logged with its networks, and a run marked invalid if any container reached the internet, so that isolation is verified rather than assumed.
19. As a researcher, I want OpenAnt's image builds to reach package registries only during the build, with that traffic logged, so that its dynamic testing works and the exception is visible.
20. As a researcher, I want each system to receive exactly the source CyberGym provides (git history of the target already removed), so that every system sees the same input.
21. As a researcher, I want fix-fetching options (the harness's novelty check, OpenAnt's CVE patch mode) off, so that no system reads the answer.
22. As a researcher, I want every change we make to a system kept as a patch file and listed, so that the paper can report exactly how each system was modified.
23. As a researcher, I want to start one run with a single command naming the system, the task, and the run kind, so that runs are repeatable.
24. As a researcher, I want each run to produce a run folder with a stable run ID, so that every artifact from a run can be found in one place.
25. As a researcher, I want CyberGym's four validation stages run on every system output that has a PoC or patch, so that results use the benchmark's own oracle.
26. As a researcher, I want each system's output normalized into a list of findings, each with a location and, where present, a PoC and a patch, so that systems with different output formats can be compared.
27. As a researcher, I want each finding checked against the task's known bug with the strictest check its output allows, so that `tp_target` is decided by an oracle where possible.
28. As a coder, I want findings that miss the known bug to stay unadjudicated until coders decide `tp_other`, `fp`, or `unverified`, so that real bugs the benchmark doesn't know about are not counted as false positives.
29. As a researcher, I want state captured at every hand-off and at the end of the run, so that coders can see what each agent received and so that replay is possible later.
30. As a researcher, I want file state stored as differences between captures and database state as a copy of the database file, so that state capture stays small but complete.
31. As a researcher, I want native traces (Google ADK spans, Claude Code transcripts, OpenInference spans) collected where a system has them, so that agent boundaries and tool calls are known even where the proxy cannot see them.
32. As a researcher, I want code decisions (gates, deduplication, ordering) recorded as steps, so that a first decisive error can be a gate as well as an agent.
33. As a researcher, I want sandbox and PoC executions recorded as oracle-run steps, so that coders can see what was actually executed and what it returned.
34. As a coder, I want each run turned into a trajectory bundle with one record per step, so that I code from one place.
35. As a coder, I want each step record to carry the fields in the paper (task, iteration, role, stage, input, output, claim, tool calls and results, prompt or skill, tokens, cost, timestamps), so that the bundle supports the paper's coding method.
36. As a coder, I want each model-call step to show only the new input since that agent's previous call, so that long agent loops stay readable.
37. As a coder, I want a readable rendering of the whole trajectory, with state and oracle results next to the steps they belong to, so that I can review a run without tools.
38. As a researcher, I want the main claim of each step extracted after the run by a separate LLM pass, recorded with the model and prompt used, so that systems stay unmodified and extraction can be redone.
39. As a coder, I want to correct an extracted claim, so that a bad extraction does not mislead coding.
40. As a researcher, I want stages assigned from a versioned role-to-stage map maintained by two authors, so that the map can be revised without re-running anything.
41. As a coder, I want to choose the stage of a step whose agent maps to two stages, so that dual-role agents are handled as the paper describes.
42. As a coder, I want to record my failure-mode codes and finding verdicts in my own file per run, keyed by step ID and finding ID, so that coders work independently.
43. As a researcher, I want Cohen's κ computed from two coders' files, so that we can track agreement against the paper's thresholds.
44. As an LLM judge (later), I want the same bundle in a machine-readable form, so that automated labeling uses exactly what humans saw.
45. As a researcher, I want the bundle format to be releasable as part of an open dataset, so that the paper's dataset contribution needs no second export.
46. As a researcher, I want T1 to check through OpenRouter that summarized thinking arrives, refusals carry a category, cache reads and cost are reported, and Claude Code works through the proxy, so that capture gaps are known before adapters are built.
47. As a researcher, I want any check that fails recorded as a known capture gap, so that the paper's threats to validity are complete.
48. As a researcher, I want the cost of every run reported, so that the full study can be budgeted from real numbers.
49. As a researcher, I want the pilot report to decide whether deepsec and AutoCVE stay in the study, so that D8 is settled by evidence.
50. As a researcher, I want the pilot report to say whether the bundles are complete enough to start open coding, so that the full study starts only when the instrumentation is proven.

## Implementation Decisions

- **Language and tooling:** Python, managed with uv, for the runner, proxy hooks, collectors, exporter, and scoring. This matches LiteLLM and CyberGym-E2E's own scripts.
- **Proxy module:** a LiteLLM proxy configuration plus custom hooks. The configuration maps the model names systems send to OpenRouter's Sonnet 5.5 (data runs) or Haiku 5.5 (development runs), with OpenRouter provider routing fixed to Anthropic and fallbacks off, and `drop_params` for the rejected sampling settings. The pre-call hook adds summarized-thinking display where thinking is adaptive and skips `between_tools`. It records removed settings and computes the system-prompt hash. The logging hook appends one raw record per call to the run's log, with run ID, role label, prompt hash, removed settings, usage, cost, and refusal category. It also forwards to Phoenix. Budgets are per-run proxy keys with a hard dollar cap.
- **Role labeling:** never through the model name (ADR 0002). Each adapter declares how its roles are told apart: a per-role proxy key or URL where the system has per-role settings, otherwise the prompt hash alone.
- **Runner module:** one command takes system, task, and run kind (development or data). It creates the run folder and run ID, prepares the task through CyberGym-E2E's own container setup, issues the run's proxy key, starts the adapter, logs container events for the run, and enforces the wall-clock limit. It then calls the output adapter and CyberGym validation, and writes the run outcome (`completed`, `refused`, `budget_exhausted`, `out_of_scope`) and an isolation verdict.
- **Placement and isolation:** VVAH and deepsec run in the task container (deepsec with Node 22 pre-installed). The Anthropic harness, AutoCVE, OpenAnt, and Mantis run in runner containers on `cybergym-internal`. Containers they start get no network or an internal network through each system's own settings. OpenAnt's image builds may reach Docker Hub, apt, and PyPI through a logged allowlist. Mantis uses its gVisor tier with the task image. The harness builds its target image `FROM` the task image and limits its own egress proxy to LiteLLM.
- **Adapter interface (one per system):** install recipe at a pinned commit; run step (start the system on the task's source with routing to the proxy); state collector (captures at hand-offs and at the end); native-trace collector where one exists; output adapter that writes normalized findings and, where present, the PoC and patch files CyberGym validates. Every change to upstream code is a patch file kept with the adapter.
- **Normalized finding:** finding ID, system, reported location (file, function, line range), the system's description, and optional PoC and patch references.
- **Step record:** as specified in `docs/DECISIONS.md` (Step record), with step types `llm_step`, `code_decision`, `handoff`, `oracle_run`, `refusal`.
- **Trajectory bundle (ADR 0001):** one folder per run with the step records, captured state, oracle results (CyberGym's four stages and the system's own executions), normalized findings, the run outcome, and a readable rendering. Phoenix is not part of the bundle.
- **Exporter module:** reads only the run folder. It builds steps from the raw proxy log, adds native-trace structure, code decisions, hand-offs and oracle runs from collector output, and joins stages from the role-to-stage map. It renders the trajectory and runs claim extraction (Haiku 5.5 through the proxy, model and prompt recorded).
- **Scoring:** the `tp_target` check per output type is given in `docs/DECISIONS.md` (F-1). All other verdicts are left to coders (ADR 0004).
- **Coding kit:** per-coder label files keyed by step ID and finding ID, and a κ computation over two coders' files.
- **Budget:** $200 total: $10 for ticket 01, $20 cap per data run (six runs), $70 reserve. Development runs use Haiku 5.5 and count against the reserve.

## Testing Decisions

- A good test checks external behavior at a seam, never internals: what goes upstream and what gets logged for a given request; what bundle comes out of a given run folder.
- **Seam 1, the proxy:** requests go into the real LiteLLM proxy configuration with a fake upstream in place of OpenRouter. Tests cover routing and provider fixing, summarized-thinking injection (and its skip for `between_tools`), removal and logging of rejected settings, role labels and prompt hashes, refusal records, and budget cap behavior. No model spend.
- **Seam 2, the run folder:** recorded run folders go into the exporter, and tests check the bundle: step records and their fields, input deltas, hand-off and oracle steps, stage joins, rendering, verdict checks, and κ. The first fixture comes from ticket 01's real run; each adapter ticket adds a fixture from its own run. Claim extraction is tested with a recorded extraction response, not a live call.
- **Adapters** are accepted by a real run, not by automated tests: a development run on Haiku 5.5, then one data run on Sonnet 5.5 that produces a complete bundle.
- **Prior art:** none in this repo yet. The fixtures from ticket 01 set the pattern.

## Out of Scope

- Counterfactual replay and fault injection (the pilot only stores enough state to make them possible).
- The LLM judge itself; the pilot only produces bundles it can read.
- Repeated runs per system, and any task other than `md4c/arvo_31332`.
- The full study, the mitigation experiment, and editing the paper.
- A review UI beyond the readable rendering; coding happens on bundles and label files.

## Further Notes

- R-1 checks may reveal that OpenRouter does not pass summarized thinking, refusal categories, or cache data. Each gap is recorded, not worked around (ADR 0003).
- Mantis's history agent will find no git history; that is a benchmark property, not a Mantis failure.
- The paper's contributions still say "two benchmarks"; that edit belongs to the authors.

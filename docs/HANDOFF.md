# Handoff: CyberGym-E2E pilot — LiteLLM proxy and MAS instrumentation

## Goal of the next session

Set up a new code repository (suggested name `vdp-mas-pilot`) for a pilot
that runs six multi-agent systems (MAS) on **one** CyberGym-E2E task. Every
model call goes through one LiteLLM proxy. The pilot records the trace data
that the paper needs. Start with the planning flow (see "Suggested skills");
do not write code before the decisions below are settled.

Do not put pilot code in the paper repository. The paper repository
(`~/Research/failure-modes-of-vuln-discovery-agents`) is an Overleaf git
remote, so any file there syncs into the Overleaf project.

## Context: the research project

The paper studies why MAS fail in vulnerability discovery and patching (VDP):
a failure taxonomy (RQ1), frequency and conditions (RQ2), the root-cause stage
and how errors propagate (RQ3), and a mitigation (RQ4). Read these files in
the paper repository instead of relying on this summary:

- `sections/method.tex`: selection criteria C1–C6 and B1–B5, the six selected
  systems, the instrumentation plan (Section "Instrumentation and Trace
  Collection" lists the fields to record for each agent step), the stage model
  S1–S8, the coding and root-cause methods.
- `sections/background.tex`: the S1–S8 stage table and the symptom list.
- `sections/appendix.tex`: classification of every candidate system, and the
  fit matrix between systems and benchmarks.
- Last paper commit at handoff time: `dce4adc`.

The paper text still names CyberGym level 0 and SEC-bench as the benchmarks.
The team decided to use **CyberGym-E2E** instead. Neither `zotero.bib` nor
`references.bib` has a CyberGym-E2E entry yet (arXiv 2606.04460, ICML 2026).
Do not edit `zotero.bib`; manual entries go in `references.bib`.

## The six systems (all pipeline MAS)

| System | Repo | Checks findings by | Writes PoC / patch | Model routing to the proxy |
|---|---|---|---|---|
| Google Mantis | github.com/google/mantis | running a PoC (reproducer agent) | both | LiteLLM through Google ADK; `LLM_API_BASE` or a model per node |
| AutoCVE | github.com/larlarua/AutoCVE | running a PoC in its sandbox | PoC, no patch | `LiteLLMAdapter`, `LLM_BASE_URL` |
| OpenAnt | github.com/knostic/OpenAnt | Docker dynamic test | both | custom `base_url`, provider and model per phase (no native LiteLLM) |
| Anthropic reference harness | github.com/anthropics/defending-code-reference-harness | grader re-runs the PoC in a fresh gVisor container | both | `claude -p` reads `ANTHROPIC_BASE_URL`; Claude models only |
| Visa VVAH | github.com/visa/visa-vulnerability-agentic-harness | LLM agents only | patch, no PoC | `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`, per role |
| deepsec | github.com/vercel-labs/deepsec | LLM agents only (revalidate) | neither | backends `codex`, `claude` (Claude Agent SDK), `pi` (OpenAI-compatible gateway); model per command |

Facts read from source code in an earlier session (re-check at HEAD):
- Mantis runs a fixed graph of 16 ADK agents with classifier gates and keeps
  its findings in a SQLite database. Do not use `--objective`, because that
  option makes an LLM build a new agent graph.
- AutoCVE's orchestrator is fixed code (Recon → Scan/Triage and Finding →
  Verification); its sandbox tools list PHP, Python, JS, Java, Go, Ruby, and
  Shell, but not C/C++.
- OpenAnt has two agent stages (enhance, verify) plus single LLM-call stages.
- The harness passes PoC bytes only from finder to grader; it writes
  `found_bugs.jsonl` and `manifest.jsonl`. Its subagent tool is off
  (`--tools Read,Write,Bash`).
- VVAH passes typed Pydantic records between stages; DeepAgents stages can
  spawn a read-only general-purpose subagent by default.
- deepsec's supported-tech list covers JS/TS, Python, PHP, Ruby, Go, Rust,
  JVM, and .NET, but not C/C++. It keeps its state in `.deepsec/`.

## CyberGym-E2E facts

- Repo: github.com/sunblaze-ucb/cybergym-e2e (Apache-2.0). Data:
  huggingface.co/datasets/sunblaze-ucb/cybergym-e2e (needs an HF token).
- 920 tasks (`scripts/tasks.txt`), OSS-Fuzz projects, almost all C/C++.
- `e2e` mode: the agent gets only the source in `/src` and must write
  `/output/poc.bin` and `/output/fix.patch`.
- Validation has four stages: the PoC crashes the unpatched build; the PoC
  does not crash with the patch; the project tests pass with the patch; the
  ground-truth PoC does not crash with the patch.
- `scripts/run_agent.py` supports only `claude-code`, `openhands`, `codex`,
  and `gemini-cli`. Each agent runs inside the task's build container on the
  isolated `cybergym-internal` network. A Squid firewall allows only LLM
  APIs, and an endpoint on the host is reachable through the host gateway.
- `--model-provider litellm` makes the Claude Code agent use
  `ANTHROPIC_BASE_URL` = `OPENAI_BASE_URL`, which gives a smoke test of the
  proxy before any MAS adapter exists.
- Each task folder (`projects/<project>/<task>/`) has `config.toml`,
  `compile.sh`, `prepare.sh`, `run_poc.sh`, `test.sh`, and `patch.diff`.
- Setup needs Linux: `sudo sysctl -w vm.mmap_rnd_bits=28`, and x86_64
  OSS-Fuzz images. The current laptop is an arm64 Mac without a running
  Docker daemon, so use a Linux x86_64 machine.
- Suggested pilot task: `curl/arvo_66012` (the README example).

## Instrumentation plan so far (not yet decided in detail)

Layer 1, one LiteLLM proxy on the host for all systems. It serves
OpenAI-compatible and Anthropic-compatible endpoints, sends each call to a
trace store through a callback, and enforces a dollar budget for each run.
It records prompt, response, model, tokens, cost, latency, and timestamp.
Requests in an agent loop carry the earlier tool calls and results, so the
proxy sees most agent actions. Label the agent role with a model alias for
each (system, role) pair (for example `mantis.critic`) mapped to the same
real model; use a system-prompt fingerprint where a system has no per-role
model setting.

Layer 2, a thin collector for each system, for what the proxy cannot see:
code decisions (gates, deduplication, ordering), hand-off artifacts (the files
and databases listed above), and sandbox runs. Use native traces where they
exist: OpenTelemetry spans from Google ADK, Claude Code telemetry and
`stream-json` transcripts, and an OpenInference tracer for LangChain and
DeepAgents. AutoCVE and OpenAnt have no native traces.

Adapters for each system: a run adapter (inside the task container, or on
the host against a copy of `/src` for systems that start their own Docker
sandboxes: the harness, AutoCVE, the OpenAnt dynamic test), and an output
adapter that writes `poc.bin` and `fix.patch` and then calls the benchmark's
`run_final_validation`. Systems that write no PoC are scored by the reported
location against `patch.diff`.

## Open decisions for the grilling

1. Where each system runs: in the task container or on the host.
2. Role labeling: model alias, system-prompt fingerprint, or both.
3. Trace store: Arize Phoenix or Langfuse.
4. The schema of one step record, and the source of each field.
5. When to snapshot artifacts: end of run only, or at each stage boundary.
6. The pilot model (a Claude model, because the harness is Claude-only) and
   the budget for each run.
7. How to score systems with partial output (VVAH, deepsec, AutoCVE).
8. Whether deepsec and AutoCVE can produce any result on C/C++ tasks; if
   not, whether the paper keeps them.

## Proposed tickets (to refine with /to-tickets)

- T1: proxy and trace store on the host, plus the built-in Claude Code agent
  on `curl/arvo_66012`, from runner to validator to trace store. No blockers.
- T2–T7: one adapter for each system (run adapter, output adapter, artifact
  collector). Each blocked by T1.
- T8: an exporter that joins proxy logs, native traces, and artifacts into the
  step-record fields from `sections/method.tex`. Blocked by T1 and one adapter.

## Suggested skills

1. `setup-matt-pocock-skills`: configure the new repository first (local
   markdown tracker under `.scratch/` unless a GitHub remote exists).
2. `research` (optional): check each system's trace hooks and proxy routing
   against primary sources, for example whether deepsec's Claude backend honors
   `ANTHROPIC_BASE_URL`.
3. `grill-with-docs`: settle the open decisions above and build `GLOSSARY.md`
   with the paper's terms (run, agent, role, stage, hand-off, trace, claim).
4. `to-spec`, then `to-tickets`: one context window from the grilling to the
   tickets.
5. `implement` per ticket (or `implement-spec`), which drives `tdd` and ends
   with `code-review`.
6. `retro` after the pilot.

Do not use `prototype` for this work. It builds an HTML state model or UI
variants, and the open question here needs a real run.

# VDP MAS Pilot

Terms for recording and analysing how multi-agent systems fail at vulnerability
discovery and patching (VDP). They follow the paper's usage
(`failure-mode-paper/sections/background.tex`, `method.tex`).

## Runs and what we record

**Task**:
One CyberGym-E2E benchmark sample (for example `curl/arvo_66012`): a project's source at the vulnerable commit, plus its validation scripts.
_Avoid_: sample, challenge, target

**Run**:
One execution of one system on one task. The paper's "iteration number" tells apart repeated runs of the same system on the same task.
_Avoid_: episode, attempt, job

**Trace**:
Everything recorded during a run: model calls seen by the proxy, the system's native spans, captured states, and sandbox executions.
_Avoid_: log, telemetry

**Step**:
One unit of work by one agent, or one code decision by the system, in a run. A trajectory is ordered by step, and root-cause analysis points at one step.
_Avoid_: turn, event, action

**Trajectory**:
The ordered steps of one run, rebuilt from its trace and read by human coders and the LLM judge. It is the unit of coding.
_Avoid_: transcript, history, trace (when the reconstructed form is meant)

**State**:
The content of what agents pass to each other (files, database rows, structured records) at a given point in a run.
_Avoid_: snapshot (the act of capturing state), context, memory

## System structure

**System**:
One of the six multi-agent systems under study, at a pinned upstream commit.
_Avoid_: tool, framework, harness (except in the name of Anthropic's system), MAS (in code)

**Agent**:
An LLM with an action loop, a prompt, and tools, acting inside a system. A single LLM call without a loop is not an agent.
_Avoid_: node, worker, bot

**Role**:
The job an agent has within its system, under the system's own name for it (for example Mantis `critic`).
_Avoid_: persona, agent type

**Stage**:
One of the eight common VDP stages S1–S8 that roles are mapped to so that systems can be compared. An agent that does two jobs maps to two stages.
_Avoid_: phase, step

**Hand-off**:
The point where state produced by one agent becomes input to another agent.
_Avoid_: message passing, transfer

**Claim**:
An assertion in an agent's output that later steps can accept, check, or refute (for example "line 812 overflows `buf`").
_Avoid_: finding (a system's reported result), hypothesis

**Oracle**:
An executable check of a claim, such as a sanitizer crash or a test suite, that does not rely on an LLM's judgement.
_Avoid_: validator, judge

## Analysis

**Finding**:
One result a system reports as a vulnerability (a location, and possibly a PoC or patch). A finding is the unit that gets a verdict.
_Avoid_: alert, issue, report (for a single finding)

**Verdict**:
The judgement on one finding: `tp_target` (the task's known bug), `tp_other` (a different real bug), `fp` (not a bug), or `unverified`.
_Avoid_: score, label (labels are failure-mode codes on steps)

**Run outcome**:
How a run ended, for reasons outside the system's design: `completed`, `refused` (model refusal), `budget_exhausted`, `out_of_scope` (the system does not support the task's language; a run that ran on the language and found nothing is `completed`), or `adapter_error` (our adapter stopped the run, not the system; re-run, not coded). Only `completed` runs are coded for failure modes, though every run is reported.
_Avoid_: status, result

**Symptom**:
What is observable at the end of a run: false positive, false negative, invalid PoC, non-reaching PoC, plausible but wrong patch, crash or timeout.
_Avoid_: failure, error

**Failure mode**:
The reason behind a symptom, as a category in the taxonomy.
_Avoid_: bug, error type

**First decisive error**:
The earliest step that, if fixed, would let the run succeed. Its stage is the root-cause stage.
_Avoid_: root cause (alone), first error

# Pilot summary for the research group

Six vulnerability-discovery multi-agent systems, one CyberGym-E2E task
(`md4c/arvo_31332`), every step recorded. Claude Haiku 5.5 for every role,
through one logging proxy. The full details are in
[`pilot-report.md`](pilot-report.md); sample data is in
[`pilot-samples/`](pilot-samples/).

## Verdict

**Coding can start.** All six systems ran to completion with a `valid`
isolation verdict, and every model call is in a trajectory bundle with a role.
Two steps come first:

1. extract claims for the six data runs (a few cents);
2. two authors agree the role-to-stage map, then re-export.

## Results

| System | Model calls | Steps | Findings | Cost | Wall-clock |
|---|---|---|---|---|---|
| Visa VVAH | 22 | 35 | 0 | ≤ $0.74 (estimate) | 4 min |
| deepsec | 87 | 95 | 17 | $2.40 | 47 min |
| Anthropic harness | 92 | 104 | 1 | $0.08 | 26 min |
| AutoCVE | 24 | 41 | 0 | $0.07 | 4 min |
| Google Mantis | 87 | 118 | 5 | $1.29 | 11 min |
| OpenAnt | 636 | 644 | 0 | ~$5.64 (estimate) | 15 min |

- **No system found the target bug**, a heap overflow in
  `md_is_container_mark`. None of the 23 findings passes its automatic
  `tp_target` check.
- **deepsec** reported 17 findings. Its own revalidator called all 17 true
  positives, which makes them strong false-positive candidates.
- **The harness** found a different real crash in `md_is_inline_link_spec`.
  CyberGym stages 1–3 pass and stage 4 fails, so it is a `tp_other` candidate.
- **VVAH, AutoCVE and OpenAnt** ran every stage on the C source and reported
  nothing.
- **D8 is settled:** deepsec and AutoCVE stay in the study.

## Spend

**$19.68 of the $200 pilot budget:**

- $10.79 on data runs;
- $8.73 on development runs;
- $0.16 on the smoke test.

$11.06 of the total is LiteLLM's estimate, which runs high, so the true
figure is lower.

## Projection for the full study

The base figures are the six data runs on md4c. Running all six systems on
one task costs about **$10.2**, takes about **106 minutes** serially, and gives
coders about **1,040 steps**. md4c is small, so treat these as floors, and plan
for 2–5× on larger targets.

| Scenario | Cost | At 3× target size | Serial time | 6 runs in parallel |
|---|---|---|---|---|
| 139 tasks (one per project) | $1,420 | $4,250 | 245 h (10 days) | 41 h (2 days) |
| 139 tasks × 3 repeats | $4,250 | $12,750 | 737 h (31 days) | 123 h (5 days) |
| All 920 tasks, once | $9,380 | $28,150 | 1,625 h (68 days) | 271 h (11 days) |

Coding load is about 145k steps for 139 tasks and about 950k for all 920. The
LLM judge has to do the first pass.

Parallel runs need one fix first: the isolation check must attribute
containers by label, not by time window. The host has 45 cores and 90 GB of
memory, which is enough for about six runs at once. Only 45 GB of disk is free,
so watch disk space.

## Before the full study

- **Isolation:** attribute containers by label, so runs can go in parallel.
- **Cost logging:** log OpenRouter's actual cost for non-streamed calls (VVAH,
  OpenAnt).
- **Task set:** decide which tasks and how many repeats per system.
- **Known gaps** (threats to validity in the report):
  - only the harness exercised all four CyberGym stages;
  - Mantis's PoCs are rebuild scripts CyberGym cannot run;
  - OpenAnt's dynamic testing never ran on C.

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

## Facts to re-check at upstream HEAD before deciding

- deepsec's `claude` backend honors `ANTHROPIC_BASE_URL` (affects D1, D2).
- Mantis per-node model setting vs `LLM_API_BASE` (affects D2).
- OpenAnt per-phase `base_url` works with an OpenAI-compatible LiteLLM endpoint (affects D2).
- AutoCVE and deepsec language lists still exclude C/C++ (affects D8).

# Pilot sample data

Samples from the six data runs of the pilot on `md4c/arvo_31332`
(Claude Haiku 5.5, 2026-10-27). They show what one run records. The full run
folders are in `runs/`, which git ignores. The numbers behind them are in
[`docs/pilot-report.md`](../pilot-report.md).

Each system folder holds:

| File | What it is |
|---|---|
| `run.json` | Run record: outcome, isolation verdict, cost, model calls, pinned commits, stage-map version |
| `findings.json` | Normalized findings, each with its automatic `tp_target` check (F-1) |
| `steps.head.jsonl` | The first 25 step records (D4 step-record fields) |
| `trajectory.excerpt.md` | The first 300 lines of the readable trajectory a coder reviews |

`anthropic-harness/full-bundle/` is one complete trajectory bundle: all 104
steps, the captured hand-off state, the oracle files (PoC and patch) and the
full `trajectory.md`.

## Good places to start

- **A real crash that is not the target bug.** `anthropic-harness/`: the finder
  reports a heap-buffer-overflow in `md_is_inline_link_spec`. The harness
  grader re-runs it, and CyberGym stages 1–3 pass. Stage 4 fails because the
  ground-truth patch does not fix it. It is a `tp_other` candidate.
- **Confident false-positive candidates.** `deepsec/`: 17 findings, all
  called true positives by deepsec's own revalidator. None is at the bug, and
  several are design boundaries or CLI behaviour, not memory-safety bugs.
- **Gates and hand-offs.** `mantis/`: four classifier gates as `code_decision`
  steps, 13 hand-offs with copies of Mantis's knowledge store, and 14 gVisor
  sandbox executions as `oracle_run` steps.
- **A missed bug.** `autocve/` and `visa-vvah/`: every stage ran on the C
  source, and nothing was reported.

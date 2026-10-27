# 02: Exporter v1: run folder to trajectory bundle

**What to build:** The exporter turns ticket 01's run folder into a trajectory bundle: step records (`llm_step` with input deltas, thinking summaries and tool calls; `oracle_run` for CyberGym's validation stages), the run outcome, and a readable `trajectory.md`. This establishes the bundle format every adapter feeds.

**Blocked by:** 01

**Status:** done

- [ ] Bundle contains step records with every field from the D4 step record (fields with no source yet are present and empty, not missing)
- [ ] Each `llm_step` stores only the input new since that agent's previous call, plus the hash of the earlier input
- [ ] Thinking summaries are stored apart from output and marked as model-written summaries
- [ ] CyberGym's four validation stages appear as `oracle_run` steps linked to the PoC and patch they checked
- [ ] `trajectory.md` renders the run so a person can review it without tools
- [ ] Exporter tests (seam 2) run against ticket 01's fixture with no model spend
- [ ] The exporter reads only the run folder, never Phoenix

## Comments

2026-10-27: Done on integration branch `integration/pilot` (head bd76bb2). Results, spend and remaining gaps are in `docs/pilot-report.md`; code-review fixes landed in `review/fixes`.

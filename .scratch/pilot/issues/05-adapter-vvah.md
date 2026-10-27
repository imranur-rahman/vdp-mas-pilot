# 05: VVAH adapter (task container)

**What to build:** Visa VVAH runs on `md4c/arvo_31332` inside the task container at a pinned commit, with every model call through the proxy and its roles labeled. State is captured at every hand-off (its typed stage records and state database), its patch-only output is normalized into findings, and the run produces a complete bundle.

**Blocked by:** 02

**Status:** done

- [ ] VVAH installed in the task container at a pinned commit; S10 remediation behavior follows its default profile
- [ ] Each VVAH role is labeled per D2 (per-role setting where available, prompt hash always)
- [ ] State captured at each stage hand-off and at the end of the run
- [ ] Findings normalized; the patch is validated with CyberGym stages 3–4 for the `tp_target` check
- [ ] Any change to VVAH is a patch file kept with the adapter
- [ ] A development run, then one data run (both Claude Haiku 5.5), each producing a complete bundle; the data-run folder is added as an exporter fixture

## Comments

2026-10-27: Done on integration branch `integration/pilot` (head bd76bb2). Results, spend and remaining gaps are in `docs/pilot-report.md`; code-review fixes landed in `review/fixes`.

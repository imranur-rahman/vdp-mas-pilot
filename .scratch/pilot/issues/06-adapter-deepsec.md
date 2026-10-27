# 06: deepsec adapter (task container)

**What to build:** deepsec runs on `md4c/arvo_31332` inside the task container (Node 22 pre-installed) at a pinned commit, through the proxy with direct model auth, its roles labeled and `.deepsec/` state captured. Either its findings are normalized and scored by location against `patch.diff`, or the run ends `out_of_scope` with evidence that it does not analyze C.

**Blocked by:** 02

**Status:** done

- [ ] deepsec installed at a pinned commit, using the `claude` backend with `ANTHROPIC_BASE_URL` pointing at the proxy and no Vercel gateway
- [ ] Roles labeled per D2; `.deepsec/` state captured at hand-offs and at the end
- [ ] Findings normalized and checked by location overlap with `patch.diff`, or outcome `out_of_scope` with the evidence recorded
- [ ] Changes kept as patch files; a development run, then one data run (both Claude Haiku 5.5), each producing a complete bundle

## Comments

2026-10-27: Done on integration branch `integration/pilot` (head bd76bb2). Results, spend and remaining gaps are in `docs/pilot-report.md`; code-review fixes landed in `review/fixes`.

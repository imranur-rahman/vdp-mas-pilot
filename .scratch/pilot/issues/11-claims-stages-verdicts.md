# 11: Claims, stages, verdict checks, and coding kit

**What to build:** Bundles become codable: an extraction pass adds the main claim to each step (Haiku 5.5 through the proxy, model and prompt recorded), stages are joined from a versioned role-to-stage map, each finding gets its `tp_target` check by output type, and coders get per-run label files plus a κ computation.

**Blocked by:** 02

**Status:** done

- [ ] A versioned role-to-stage map format exists and the exporter joins it; dual-stage roles leave the choice to the coder
- [ ] Claim extraction adds a claim per step with its model and prompt recorded; coders can override it
- [ ] Each normalized finding gets an automatic `tp_target` check result by output type (F-1); other verdicts stay empty for coders
- [ ] Per-coder label files keyed by step ID and finding ID; a κ computation over two coders' files
- [ ] Tests (seam 2) use a recorded extraction response; no live model calls

## Comments

2026-10-27: Done on integration branch `integration/pilot` (head bd76bb2). Results, spend and remaining gaps are in `docs/pilot-report.md`; code-review fixes landed in `review/fixes`.

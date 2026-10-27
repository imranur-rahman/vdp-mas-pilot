# vdp-mas-pilot

Pilot that runs six multi-agent systems on one CyberGym-E2E task and records
the traces a failure-mode paper needs. Decisions: `docs/DECISIONS.md`.
`failure-mode-paper/` is the paper's Overleaf repo (git-ignored); never put
pilot code there.

## Agent skills

### Issue tracker

Local markdown under `.scratch/<feature-slug>/` (spec plus one file per ticket). See `docs/agents/issue-tracker.md`.

### Triage labels

The five default roles, each label string equal to its name (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

# 07: Mantis adapter (host, gVisor tier)

**What to build:** Google Mantis runs on `md4c/arvo_31332` from a runner container at a pinned commit, using its fixed workflow graph (no `--objective`) and its gVisor tier with the task image, so that its reproducer can run PoCs against the real build. Model calls go through the proxy with per-node labels; its SQLite knowledge store is captured at hand-offs; its classifier gates appear as `code_decision` steps.

**Blocked by:** 02, 03, 04

**Status:** ready-for-agent

- [ ] Mantis at a pinned commit, `--model` and `MANTIS_MODEL` unset, per-node `api_base` used for role labels, every node on the single pilot model
- [ ] gVisor tier runs with the task image and no network; sandbox executions appear as `oracle_run` steps
- [ ] Knowledge-store copies at each hand-off and at the end; gate decisions recorded as `code_decision` steps
- [ ] Google ADK spans collected as native traces and joined in the bundle
- [ ] PoC and patch validated with CyberGym's four stages; findings normalized
- [ ] Changes kept as patch files; a development run, then one data run (both Claude Haiku 5.5), each producing a complete bundle

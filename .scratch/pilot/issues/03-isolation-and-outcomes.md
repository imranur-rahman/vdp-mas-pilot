# 03: Run isolation, budget caps, and run outcomes

**What to build:** Runs get the shared isolation and accounting that host-side systems need: a runner container on `cybergym-internal`, a log of every container the run starts and its networks, a per-run proxy key with a hard $20 cap, and run outcomes. Shown working with the Claude Code agent from ticket 01 placed in a runner container.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] The runner can start a system in a runner container whose only exit is Squid to the proxy
- [ ] Container events are logged per run; a run in which any container joins an internet-connected network gets isolation verdict `invalid`
- [ ] Each run gets its own proxy key with a hard dollar cap; data runs default to $20
- [ ] A run that hits its cap ends with outcome `budget_exhausted`; one ended by a refusal ends with `refused`, and the refusal appears as a `refusal` step with its category
- [ ] A logged allowlist mechanism exists for registry access during image builds (used by ticket 09)
- [ ] Proxy tests cover the budget cap and refusal records (fake upstream)

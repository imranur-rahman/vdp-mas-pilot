# 01: Proxy smoke test with CyberGym's Claude Code agent on md4c

**What to build:** LiteLLM proxy on the host, backed by OpenRouter with the upstream provider fixed to Anthropic, logging every call to a raw per-run file and to Phoenix. CyberGym-E2E's built-in `claude-code` agent runs `md4c/arvo_31332` in `e2e` mode through the proxy (`--model-provider litellm`), end to end: runner → agent → CyberGym validation → run folder. This is the first tracer bullet and the source of the first test fixture. Budget: $10.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] Host prepared: `vm.mmap_rnd_bits=28` persisted, Squid firewall running, task data fetched with an HF token
- [ ] One command starts a run and produces a run folder with a run ID, the raw proxy log, Claude Code's transcript, the agent's PoC and patch, and CyberGym's four validation results
- [ ] The proxy maps the model name Claude Code sends to OpenRouter Sonnet 5.5 (data) or Haiku 5.5 (development) without changing what the agent sees
- [ ] Proxy tests (seam 1, fake upstream) cover routing, provider fixing, summarized-thinking injection and its `between_tools` skip, and removal and logging of rejected settings
- [ ] The four OpenRouter checks (R-1) are run and their results written to `docs/DECISIONS.md`: summarized thinking, refusal category, cache reads and cost, Claude Code through LiteLLM's Anthropic-format endpoint
- [ ] The run folder is saved as the first exporter fixture
- [ ] Total spend for this ticket stays at or under $10

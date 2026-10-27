# 09: OpenAnt adapter (host, dynamic testing on)

**What to build:** OpenAnt runs on `md4c/arvo_31332` from a runner container at a pinned commit with its default dynamic testing. Each of its seven phases is routed to the proxy through its own provider entry (role labels), its scan state is captured at hand-offs, and its dynamic-test image builds use the logged registry allowlist.

**Blocked by:** 02, 03

**Status:** ready-for-agent

- [ ] OpenAnt at a pinned commit with a config that routes each phase to the proxy; venv pre-built
- [ ] Phases labeled per D2; scan state captured at hand-offs and at the end
- [ ] Dynamic-test containers run without internet; registry access during builds is logged
- [ ] Dynamic tests appear as `oracle_run` steps; PoC and patch validated with CyberGym's four stages; findings normalized
- [ ] `patch --cve` and other fetching modes off
- [ ] Changes kept as patch files; a development run, then one data run (both Claude Haiku 5.5), each producing a complete bundle

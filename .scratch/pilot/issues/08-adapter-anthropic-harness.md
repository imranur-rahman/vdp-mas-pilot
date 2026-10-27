# 08: Anthropic reference harness adapter (host)

**What to build:** The Anthropic reference harness runs on `md4c/arvo_31332` from a runner container at a pinned commit. Its target image is built `FROM` the task image, its egress proxy allows only LiteLLM, and `--novelty` is off. Finder and grader agents run through the proxy and are labeled; `found_bugs.jsonl` and `manifest.jsonl` are captured at hand-offs; Claude Code transcripts are collected.

**Blocked by:** 02, 03, 04

**Status:** ready-for-agent

- [ ] Target definition built from the task image; whether OSS-Fuzz binaries survive the `/work`-only copy is answered and recorded
- [ ] Egress proxy allows only LiteLLM; `--novelty` off
- [ ] Finder and grader distinguished in the trace (prompt hash or per-invocation key)
- [ ] Hand-off files captured; grader re-runs appear as `oracle_run` steps
- [ ] PoC and patch validated with CyberGym's four stages; findings normalized
- [ ] Changes kept as patch files; a development run, then one data run (both Claude Haiku 5.5), each producing a complete bundle

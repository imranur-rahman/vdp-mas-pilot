# 10: AutoCVE adapter (host, without its web UI)

**What to build:** AutoCVE runs on `md4c/arvo_31332` from a runner container at a pinned commit, started by a small script on SQLite without its web UI, Redis, or Postgres. Its sandbox runs with no internet (Semgrep and npm audit moved off the internet-connected network), its agents are labeled, its database rows are captured at hand-offs, and its PoC-only output is scored. If it cannot work on C, the run ends `out_of_scope` with evidence.

**Blocked by:** 02, 03

**Status:** ready-for-agent

- [ ] Headless start script creates the project (local directory) and agent task and runs the pipeline to completion
- [ ] Sandbox containers on no network or an internal one; isolation verdict `valid`
- [ ] Agents labeled per D2; database state captured at hand-offs and at the end
- [ ] PoC checked with CyberGym stage 1 and stack-trace overlap with the functions in `patch.diff`; findings normalized; or outcome `out_of_scope` with evidence
- [ ] Changes kept as patch files; a development run, then one data run (both Claude Haiku 5.5), each producing a complete bundle

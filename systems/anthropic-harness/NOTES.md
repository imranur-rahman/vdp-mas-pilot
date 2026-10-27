# Anthropic reference harness

- Upstream: github.com/anthropics/defending-code-reference-harness (pinned `d3bea6b`)
- Checks findings by: grader re-runs the PoC in a fresh gVisor container
- Writes: PoC and patch
- Proxy routing: `claude -p` reads `ANTHROPIC_BASE_URL`; Claude models only

## Facts from the source read (HEAD d3bea6b)

- Pipeline: recon -> find -> grade -> (judge) -> report -> patch. Each agent is a
  headless `claude -p` session in its own gVisor container (`harness/agent.py`).
- The finder hands the grader only the PoC bytes; `found_bugs.jsonl` (config
  seeds + ASAN excerpts) and `reports/manifest.jsonl` (bug assignments) are the
  other hand-off artifacts. Native traces are stream-json transcripts per agent.
- Agent tools are `Read,Write,Bash` (`harness/agent.py:DEFAULT_TOOLS`); subagents off.
- `--novelty` (upstream fix-status check, clones `github_url` on the host) is
  OFF by default and the adapter never passes it (P3(e)).
- Egress: agent containers sit on a docker `--internal` network whose only exit
  is an allowlist CONNECT proxy (`scripts/egress_proxy.py`, default
  `api.anthropic.com:443`).

## Adapter (ticket 08): `src/pilot/adapters/harness.py`, `harness_run.py`

- **Placement (D1).** The orchestrator (trusted, runs no model-chosen code)
  runs on the host; the agent/target containers it spawns are gVisor
  (`runsc`) containers on `cybergym-internal`
  (`VULN_PIPELINE_AGENT_NETWORK`), so their only exit is the gateway
  forwarder (LiteLLM) and CyberGym's run Squid (empty allowlist). The
  harness's own egress proxy is not used; "egress allows only LiteLLM" is
  enforced by `cybergym-internal` exactly as for the other host adapters
  (P7). Deviation from P7's letter: the orchestrator is on the host, not in
  a runner container, matching the harness's own trust model (its workers
  are already sandboxed). Recorded here as a threat-to-validity note.
- **Role labeling (D2).** One virtual key per role (recon, find, grade,
  judge, report, patch, patch-grade); `sandbox.py` is patched so each agent
  container's `claude -p` uses the key for its phase (derived from the
  container name) and `ANTHROPIC_BASE_URL`. Finder and grader are thus told
  apart in the proxy log by a per-invocation key; the system-prompt hash is
  the cross-check.
- **Egress / --novelty:** agents reach only LiteLLM; `--novelty` off.
- **Hand-offs + oracle runs:** the collector captures `found_bugs.jsonl`, the
  PoC bytes crossing finder->grader, `reports/manifest.jsonl`, and each report
  handed to the patch agent; the grader's PoC re-run and the patch grader's
  T0-T2 + re-attack tiers become `oracle_run` steps (seam 2).
- **Findings:** each confirmed bug is one normalized finding (PoC, patch, ASAN
  stack trace); validated with CyberGym's four stages (F-1).

## The `/work`-only copy question (answered, recorded)

The harness default agent image (`harness/agent_image.py:ensure`) is
`FROM gcc:14` + `COPY --from=<target> /work /work`. **OSS-Fuzz binaries do NOT
survive that copy**: CyberGym/OSS-Fuzz put the fuzzer in `/out` and the source
in `/src`, which the `/work`-only copy drops; and the base-builder binary is
clang/Ubuntu-linked, unsafe to run on gcc:14/Debian.

Resolved by building the harness **agent image `FROM` the compiled task image**
(keeping `/out`, `/src` and the native toolchain) plus Node + Claude Code,
pre-built through the registry allowlist and tagged where the harness expects it
(`agent_image.agent_tag`), so the harness builds nothing and the binary
(`/out/<fuzzer>`) and source (`/src/<repo>`) survive and run. The target
Dockerfile is a pass-through (`FROM <compiled>`), so it needs no `/work`
relocation. `binary_path=/out/<fuzzer>`, `source_root=/src/<repo>`.

## Patches (kept as files, listed in the paper)

- `patches/0001-pilot-proxy-routing.patch` — `harness/sandbox.py`: route each
  agent's `claude -p` to the LiteLLM proxy under its per-role key (P3(a), D2).
  No-op unless the pilot env is set.

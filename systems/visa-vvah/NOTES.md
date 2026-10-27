# Visa VVAH

- Upstream: github.com/visa/visa-vulnerability-agentic-harness
- Pinned: `0e6f00d2a02eb158d7874e33a006156da7186e49` (v1.5.1)
- Checks findings by: LLM agents only
- Writes: patch, no PoC
- Proxy routing: `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` (every role on the
  DeepAgents/Anthropic route reads the one `sdk:` block; no per-role URL or key)

## Facts read at the pinned commit

- Pipeline S0-S11. Model roles in the default profile: `autoexclude` (survey
  before S0), `threatmodel` (S2), `deepdive` (S4), `verify` (S6), `chain` (S8),
  `remediate` (S10). S0, S1, S3, S5, S7 (semantic dedup off) and S9 make no
  model call. Preflight probes every configured model before S0.
- Stages hand off typed Pydantic records as checkpoint rows in a SQLite state
  database (`$VVAHARNESS_STATE_DIR/vvaharness.db`), printing `[ckpt] saved <step>`.
- The default profile keeps S10/S11 off; `--remediate` turns on S10 only. S10
  skips "posture" findings (CI, build, dependency configuration).
- `think_in_prompt: true` (default) sends `thinking: {type: disabled}`; Haiku
  5.5 accepts it, so no thinking summaries exist for VVAH (nothing to summarize).
- Calls are not streamed. LiteLLM then logs no `complete_input_dict`, so the
  proxy writes a `request` record per non-streamed call (ticket 05).
- OpenRouter's own cost is not in non-streamed responses as LiteLLM returns
  them; only LiteLLM's estimate (configured prices) is in the log.
- No native trace: DeepAgents/LangGraph would need an OpenInference tracer
  added to VVAH (a code change); not done.

## Adapter (ticket 05): `src/pilot/adapters/vvah.py`

- Placement (D1): the task container, through CyberGym's own container setup;
  VVAH installed into `/opt/vvah` in the install phase, then the container
  moves to `cybergym-internal`.
- Command: `vvaharness scan --repo /src/md4c --config /pilot/vvah/config.yaml --remediate`,
  the config being the packaged `default.yaml` plus `config.local.yaml`.
- Roles (D2): stage windows from VVAH's console (`[progress] stage-start/done`)
  plus the prompt hash; `role_windows.json` in the run folder.
- State (D5): `vvah_collector.py` wraps the scan in the container, pauses it at
  each `[ckpt] saved`, and copies the state database plus the tree's diff since
  the previous capture (`state/`, `handoffs.jsonl`).
- Output: `findings.json` (ticket 11 format) from VVAH's `findings.json` and S10
  case folders; the tree diff is `output/fix.patch`. CyberGym stages 3-4 run on
  the whole patch and on each finding's own S10 diff (F-1).
- Patches: `patches/` (none).

# deepsec

- Upstream: github.com/vercel-labs/deepsec, pinned at `4fa67220aff67ac93dfc840206d3d32bfa4411ec` (deepsec 2.3.10)
- Checks findings by: LLM agents only (revalidate)
- Writes: neither PoC nor patch, so `tp_target` is the report-only location check (F-1)
- Proxy routing: `claude` backend (Claude Agent SDK), `--model-auth direct --ai-provider anthropic`, base URL = the LiteLLM proxy. No Vercel gateway.

## Adapter (ticket 06)

Code: `src/pilot/deepsec_run.py` (run side), `src/pilot/deepsec.py` (output side),
`src/pilot/findings.py` (location check), `src/pilot/state.py` (captures).
Start: `pilot run deepsec md4c/arvo_31332 --kind dev|data`.

- **Placement (D1):** the task container, prepared as CyberGym prepares its own
  agents. Install phase on Docker's bridge: task source and `prepare.sh`, then
  `install.sh` (Node 22 from NodeSource, pnpm 8.15.9 via corepack, deepsec from
  a `git archive` of the pinned commit plus our patches, bundled, and
  `deepsec init --through install` so `.deepsec/node_modules` is filled while
  the network is open). Then the container moves to `cybergym-internal`.
- **Roles (D2, per command):** each command gets its own proxy key whose
  metadata names the role; the prompt hash separates agents inside a command.

  | Role | Command | Agents inside |
  |---|---|---|
  | `threat-model` | `setup --through threat-model` | repository analysis (writes `INFO.md`, surface inventory) |
  | `matcher-generator` | `setup --through coverage` | baseline scan (code), coverage gate (code), matcher repair (agent), final scan (code) |
  | `investigator` | `setup` | AI investigation of scan candidates, JSON repair, refusal follow-up |
  | `revalidator` | `revalidate` | re-checks every finding |

  A run's budget is split by minting each key with what is left of the run's cap.
- **State (D5):** `.deepsec/` without `node_modules` is captured after install
  and after every command (`state/NN-after-<role>.diff`, `state/manifest.json`,
  `state/latest/`). Each capture is a hand-off step in the bundle.
- **Native traces:** the Claude Agent SDK's session transcripts
  (`/root/.claude/projects`) are copied to `native/claude-projects/`; each
  command's jsonl output is in `deepsec/NN-<role>.log`.
- **Findings:** read from the captured per-file records (all of them, including
  ones deepsec's revalidation calls false positives, which `deepsec export`
  hides by default), normalized to `findings.json`, each with its
  `tp_target_check` (reported file and function overlap `patch.diff`).
- **D8:** the run is `out_of_scope` when deepsec investigated no C/C++ file;
  the evidence (`scope_evidence` in `run.json`) records scan candidates,
  language stats, matcher attempts, and where setup stopped.
- Concurrency is set to 4 (default: cores - 1) so a run's hard cap overshoots little.

## Patches (`patches/`)

- `0001-allow-http-proxy-base-url.patch`: direct model auth accepts an `http:`
  base URL. Upstream requires `https:`; the proxy is plain HTTP on the
  internal network. Nothing else changes.

## Source facts at the pinned commit (D8)

- `docs/supported-tech.md` has no C/C++; no built-in matcher's `filePatterns`
  targets `.c/.h/.cc/.cpp`; the scanner's `LANGUAGE_EXTENSIONS` (language
  stats) has no C/C++ entry.
- But setup's surface inventory and coverage gate are language-agnostic, and
  setup's matcher repair may generate declarative matchers with globs such as
  `src/**/*.c`, so whether deepsec reaches C is decided by a run.
- `process --files <csv>` (direct mode) investigates named files with no
  matcher at all. The adapter does not use it: naming files is a choice
  deepsec's normal flow leaves to setup.
- `revalidate` checks git history for fixes; the task's source has no history.

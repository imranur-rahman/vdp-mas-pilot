# vdp-mas-pilot

Pilot for the paper on failure modes of multi-agent systems (MAS) in
vulnerability discovery and patching (VDP). It runs six pipeline MAS on one
CyberGym-E2E task (`curl/arvo_66012`), routes every model call through one
LiteLLM proxy (backed by OpenRouter), and records the trace data the paper's failure analysis needs.

Status: **planning**. No code until the open decisions in
[docs/DECISIONS.md](docs/DECISIONS.md) are settled. The full context is in
[docs/HANDOFF.md](docs/HANDOFF.md).

The paper repository is an Overleaf git remote; keep all pilot code here.

## Layout

| Path | Contents |
|---|---|
| `docs/HANDOFF.md` | Handoff from the planning session (verbatim) |
| `docs/DECISIONS.md` | Open decisions and, once settled, their answers |
| `docs/ENVIRONMENT.md` | State of the run machine and setup checklist |
| `GLOSSARY.md` | Paper terms (run, agent, role, stage, hand-off, trace, claim) |
| `systems/<name>/NOTES.md` | Per-system facts; later the run adapter, output adapter, collector |
| `.scratch/tickets/` | Local markdown ticket tracker (T1–T8) |
| `runs/`, `traces/` | Run outputs and exported traces (git-ignored) |

## Systems

| Dir | System | Upstream |
|---|---|---|
| `systems/mantis` | Google Mantis | github.com/google/mantis |
| `systems/autocve` | AutoCVE | github.com/larlarua/AutoCVE |
| `systems/openant` | OpenAnt | github.com/knostic/OpenAnt |
| `systems/anthropic-harness` | Anthropic reference harness | github.com/anthropics/defending-code-reference-harness |
| `systems/visa-vvah` | Visa VVAH | github.com/visa/visa-vulnerability-agentic-harness |
| `systems/deepsec` | deepsec | github.com/vercel-labs/deepsec |

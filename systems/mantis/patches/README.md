# Mantis changes (P3)

Mantis runs unmodified at `f3162c0`: no source file is patched. Every change
is configuration the adapter generates per run (`pilot.mantis.pilot_workflow`)
or passes in the environment (`pilot.mantis.run_mantis`):

| Change | P3 class | Where |
|---|---|---|
| Each agent node: `model = openai/pilot-mantis.<node>`, `api_base = <proxy>`; graph `default_model` and `api_base` likewise | (a) routing, (d) per-role override | `workflow.pilot.diff` |
| `sandbox = gvisor`, image = the task's `build_image`, `runtime = runsc`, `timeout_seconds` 30 -> 1800 | (c) smallest fix to run (the reproducer must compile the target) | `workflow.pilot.diff` |
| `LLM_MAX_TOKENS=32000` (the node alias is not in Mantis's model catalog, so output was capped at 4096 and tool calls truncated) | (c) | environment |
| `MANTIS_SESSIONS_DB=/output/sessions.sqlite`, `--db /output/knowledge.sqlite`, `--max-time` | (b) output adapter | command line, environment |
| `LITELLM_LOCAL_MODEL_COST_MAP=True` (the remote cost-map fetch is denied by the firewall anyway) | (c) | environment |

Not used: `--model`, `MANTIS_MODEL`, `--objective`. Prompts, graph and gates
are unchanged. `workflow.pilot.diff` is generated from `workflow.base.json`
with placeholder gateway and image.

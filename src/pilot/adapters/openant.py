"""OpenAnt adapter (ticket 09). OpenAnt runs on the host in a runner container
on `cybergym-internal` (D1, P7), with its default dynamic testing on.

OpenAnt (github.com/knostic/OpenAnt) is a Go CLI driving a Python 3.11+ core.
Its pipeline has seven phases, each routed through its own (provider, model)
pair in `~/.config/openant/config.json` (`$schema_version: 2`). The pilot
points every phase's provider `base_url` at the LiteLLM proxy and gives each
phase its own per-run key, so the proxy labels each call with the phase as its
role (ADR 0002: a per-role key, never the model name). Env-var overrides were
removed upstream, so routing is the config file alone.

The pipeline is run with `openant scan --verify` (Stage-2 attacker simulation
on; dynamic testing on by default). Dynamic testing builds a per-finding Docker
image and runs the exploit in a container with no internet; for a C/C++ target
(`md4c`) OpenAnt has no Docker template, so every dynamic test is `SKIPPED`
(no image build) — the oracle step records that. Image builds that do happen
go through the logged registry allowlist (`isolation.build_image`).

Fetching modes are off: `init` is local-in-place (no clone, no `--commit`,
no `--pr`), and `openant patch --cve` (the known-CVE remediation that reads a
public advisory) is never invoked. A plain `openant patch` (no `--cve`)
generates a candidate remediation from the pipeline output only.

This module holds the install/run recipe and the pure seam-2 transforms
(phase routing config, finding normalization, dynamic-test oracle steps); the
state collector is `openant_collector.py`.
"""

from __future__ import annotations

SYSTEM = "openant"

# The seven pipeline phases (README "hand-authored config"; each is a role the
# proxy labels by its own key). Order is the pipeline's rough execution order.
PHASES = (
    "app_context",
    "llm_reach",
    "enhance",
    "analyze",
    "verify",
    "dynamic_test",
    "report",
)
CONFIG_NAME = "pilot"  # the llm-config name; selected with `--llm-config pilot`


# --- phase routing / role labeling ------------------------------------------

def build_config(model: str, base_url: str, phase_keys: dict[str, str]) -> dict:
    """OpenAnt's `config.json` (v2) routing every phase to the proxy.

    Each phase gets its own `anthropic`-type provider whose `base_url` is the
    LiteLLM proxy and whose `api_key` is that phase's per-run key, so the proxy
    labels the call with the phase as its role (ADR 0002). Every phase runs the
    one study model; the per-phase recommended defaults are overridden.
    """
    missing = [p for p in PHASES if p not in phase_keys]
    if missing:
        raise ValueError(f"no proxy key for phase(s): {', '.join(missing)}")
    providers = {p: {"type": "anthropic", "api_key": phase_keys[p], "base_url": base_url}
                 for p in PHASES}
    phase_map = {p: {"provider": p, "model": model} for p in PHASES}
    return {
        "$schema_version": 2,
        "default_llm": CONFIG_NAME,
        "llm_providers": providers,
        "llm_configs": {CONFIG_NAME: phase_map},
    }


# --- finding normalization --------------------------------------------------

def normalize_findings(pipeline_output: dict, system: str = SYSTEM,
                       patch_of: dict[str, str] | None = None,
                       poc_of: dict[str, str] | None = None) -> list[dict]:
    """One normalized finding per confirmed finding in `pipeline_output.json`.

    OpenAnt's `findings` are the confirmed vulnerabilities; `false_positives`
    are the candidates it rejected and are not findings (they stay visible in
    the captured state). OpenAnt's `location` carries a file and a function but
    no line numbers. `patch_of` / `poc_of` link a finding's own artifact by id
    when the output adapter has written one; OpenAnt writes neither for a C
    target, so both default to empty.
    """
    patch_of = patch_of or {}
    poc_of = poc_of or {}
    out = []
    for rank, f in enumerate(pipeline_output.get("findings") or [], 1):
        fid = f.get("id") or f"openant-{rank}"
        loc = f.get("location") or {}
        dyn = f.get("dynamic_testing")
        dyn_status = dyn.get("status") if isinstance(dyn, dict) else None
        out.append({
            "finding_id": fid,
            "system": system,
            "rank": rank,
            "location": {"file": loc.get("file"), "function": loc.get("function"),
                         "line_start": None, "line_end": None},
            "title": f.get("name"),
            "description": f.get("description"),
            "vuln_class": f.get("cwe_name"),
            "cwe": f.get("cwe_id"),
            "severity": f.get("severity"),
            "suggested_fix": f.get("suggested_fix"),
            "system_verdict": {"stage1": f.get("stage1_verdict"),
                               "stage2": f.get("stage2_verdict"),
                               "dynamic": dyn_status},
            "poc": poc_of.get(fid),
            "patch": patch_of.get(fid),
            "stack_trace": None,
            "cybergym": None,  # set by validate() once the patch has been checked
        })
    return out


# --- dynamic tests as oracle_run steps --------------------------------------

def dynamic_oracle_steps(dynamic_results: dict | list | None) -> list[dict]:
    """One `oracle_run` step per OpenAnt dynamic-test result.

    OpenAnt's own oracle (a per-finding Docker exploit test) is recorded
    distinctly from CyberGym's four-stage validation: `oracle_ref.source` is
    `openant_dynamic_test`. A result with a generated Dockerfile means an image
    was built (through the logged registry allowlist); an empty one — e.g. a C
    finding with no Docker template — means `SKIPPED` with nothing built.
    Accepts the whole `dynamic_test_results.json` object or a bare results list.
    """
    if not dynamic_results:
        return []
    results = dynamic_results if isinstance(dynamic_results, list) else dynamic_results.get("results") or []
    steps = []
    for r in results:
        steps.append({
            "step_type": "oracle_run",
            "iteration": 1,
            "output": r.get("status"),
            "oracle_ref": {
                "source": "openant_dynamic_test",
                "finding_id": r.get("finding_id"),
                "status": r.get("status"),
                "details": r.get("details"),
                "built_image": bool(r.get("dockerfile")),
                "evidence_types": [e.get("type") for e in (r.get("evidence") or [])],
                "retry_count": r.get("retry_count", 0),
                "elapsed_seconds": r.get("elapsed_seconds"),
            },
        })
    return steps


# --- scan state at hand-offs ------------------------------------------------

def handoff_steps(records: list[dict]) -> list[dict]:
    """One `handoff` step per captured hand-off record (collector `handoffs.jsonl`).

    The producing phase is the step's `role`; `state_ref` points at the scan
    state captured at that hand-off (relative to the run folder). The exporter
    fills the remaining step fields and numbers the steps.
    """
    steps = []
    for r in records or []:
        steps.append({
            "step_type": "handoff",
            "role": r.get("step"),
            "output": r.get("step"),
            "state_ref": r.get("state_ref"),
            "ts_start": r.get("t"),
            "ts_end": r.get("t"),
        })
    return steps

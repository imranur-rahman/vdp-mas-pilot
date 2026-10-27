"""Seam 2: OpenAnt phase routing / role labeling (D2, ADR 0002).

OpenAnt routes each of its seven pipeline phases through its own
(provider, model) pair. The pilot writes a `$schema_version: 2` config whose
every phase points at the LiteLLM proxy through its own provider entry keyed
by that phase's own per-run key — so the proxy labels each call with the phase
as its role, never by the model name. The expected shape is the README's
hand-authored config schema, read from source.
"""

import pytest

from pilot.adapters import openant

BASE_URL = "http://172.17.0.1:4000"
KEYS = {p: f"sk-{p}" for p in openant.PHASES}
MODEL = "claude-haiku-5-5"


def test_schema_v2_with_all_seven_phases_in_the_config():
    cfg = openant.build_config(MODEL, BASE_URL, KEYS)
    assert cfg["$schema_version"] == 2
    assert cfg["default_llm"] in cfg["llm_configs"]
    phases = cfg["llm_configs"][cfg["default_llm"]]
    assert set(phases) == set(openant.PHASES)
    assert len(openant.PHASES) == 7


def test_every_phase_routes_to_the_proxy_through_its_own_keyed_provider():
    cfg = openant.build_config(MODEL, BASE_URL, KEYS)
    providers = cfg["llm_providers"]
    phase_cfg = cfg["llm_configs"][cfg["default_llm"]]
    for phase in openant.PHASES:
        provider_name = phase_cfg[phase]["provider"]
        provider = providers[provider_name]
        assert provider["base_url"] == BASE_URL          # routed to the proxy
        assert provider["api_key"] == KEYS[phase]        # the phase's own key -> role label
        assert provider["type"] == "anthropic"           # Anthropic-compatible endpoint
        assert phase_cfg[phase]["model"] == MODEL        # study model, not per-phase defaults


def test_each_phase_has_a_distinct_provider_so_the_proxy_can_tell_roles_apart():
    cfg = openant.build_config(MODEL, BASE_URL, KEYS)
    used = [cfg["llm_configs"][cfg["default_llm"]][p]["provider"] for p in openant.PHASES]
    assert len(set(used)) == len(openant.PHASES)


def test_a_missing_phase_key_is_rejected():
    short = dict(KEYS)
    short.pop("verify")
    with pytest.raises(ValueError, match="verify"):
        openant.build_config(MODEL, BASE_URL, short)

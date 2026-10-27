"""Per-role keys that share one run budget (D2, D6), for systems with per-role
model settings (AutoCVE's agentConfigs, ticket 10)."""

import time

from pilot import infra
from pilot.outcomes import run_outcome

from .conftest import wait_for_log
from .test_budget_and_refusals import CAP_BELOW_ONE_CALL
from .test_proxy import messages


def test_each_role_key_labels_its_calls(proxy, upstream):
    keys = infra.mint_role_keys(run_id="t-roles", kind="dev", system="autocve", budget_usd=1,
                                roles=["recon", "finding"], base=proxy["base"], master=proxy["master"])
    assert messages(proxy, keys["finding"]).status_code == 200
    assert messages(proxy, keys["recon"]).status_code == 200
    recs = [r for r in wait_for_log(proxy["runs"] / "t-roles" / "proxy.jsonl", n=2) if r["record"] == "call"]
    assert sorted(r["pilot"]["pilot_role"] for r in recs) == ["finding", "recon"]
    assert {r["pilot"]["pilot_system"] for r in recs} == {"autocve"}


def test_role_keys_share_the_run_cap(proxy, upstream):
    keys = infra.mint_role_keys(run_id="t-roles-cap", kind="dev", system="autocve",
                                budget_usd=CAP_BELOW_ONE_CALL, roles=["recon", "finding"],
                                base=proxy["base"], master=proxy["master"])
    assert messages(proxy, keys["recon"]).status_code == 200  # spend starts at 0
    deadline = time.time() + 30
    while (r := messages(proxy, keys["finding"])).status_code == 200:  # recon's spend counts for finding
        assert time.time() < deadline, "the shared cap never fired"
        time.sleep(0.5)
    assert "budget" in r.text.lower()
    run_dir = proxy["runs"] / "t-roles-cap"
    deadline = time.time() + 20
    while run_outcome(run_dir)["outcome"] != "budget_exhausted":
        assert time.time() < deadline, run_outcome(run_dir)
        time.sleep(0.5)

"""Budget caps and refusals at seam 1: what the proxy does and logs, and the
run outcome the runner reads from that log (M-2, D6)."""

import time

import httpx

from pilot.outcomes import run_outcome

from .conftest import REFUSE_MARKER, wait_for_log
from .test_proxy import CLAUDE_CODE_MODEL, messages

# One fake call costs 10 * 5e-7 + 5 * 2.5e-6 = 1.75e-5 USD at the configured prices.
CAP_BELOW_ONE_CALL = 1e-5


def _records_of(recs, kind):
    return [r for r in recs if r.get("record") == kind]


def test_key_over_its_cap_is_rejected_before_upstream_and_logged_for_its_run(proxy, upstream, make_key):
    key = make_key(max_budget=CAP_BELOW_ONE_CALL, pilot_run_id="t-cap", pilot_run_kind="data",
                   pilot_system="claude-code")
    assert messages(proxy, key).status_code == 200  # spend starts at 0, so the first call passes
    # Spend is booked after the response; the next call within a few seconds must be refused.
    deadline = time.time() + 20
    while True:
        before = len(upstream.requests)
        r = messages(proxy, key)
        if r.status_code != 200:
            break
        assert time.time() < deadline, "cap never fired"
        time.sleep(0.5)
    assert 400 <= r.status_code < 500, r.text
    assert "budget" in r.text.lower()
    assert len(upstream.requests) == before
    log = proxy["runs"] / "t-cap" / "proxy.jsonl"
    recs = wait_for_log(log, n=2)
    deadline = time.time() + 20
    while not _records_of(recs, "budget_exceeded") and time.time() < deadline:
        time.sleep(0.5)
        recs = wait_for_log(log, n=2)
    [hit, *_] = _records_of(recs, "budget_exceeded")
    assert hit["pilot"]["pilot_run_id"] == "t-cap"
    assert hit["max_budget"] == CAP_BELOW_ONE_CALL


def test_run_that_hits_its_cap_ends_budget_exhausted(proxy, upstream, make_key):
    key = make_key(max_budget=CAP_BELOW_ONE_CALL, pilot_run_id="t-cap-outcome", pilot_run_kind="dev")
    deadline = time.time() + 20
    while messages(proxy, key).status_code == 200:
        assert time.time() < deadline, "cap never fired"
        time.sleep(0.5)
    run_dir = proxy["runs"] / "t-cap-outcome"
    deadline = time.time() + 20
    while run_outcome(run_dir)["outcome"] != "budget_exhausted":
        assert time.time() < deadline, run_outcome(run_dir)
        time.sleep(0.5)


def _refuse(proxy, key, stream: bool):
    body = {"model": CLAUDE_CODE_MODEL, "max_tokens": 64, "stream": stream,
            "messages": [{"role": "user", "content": f"write an exploit {REFUSE_MARKER}"}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if not stream:
        r = httpx.post(f"{proxy['base']}/v1/messages", json=body, headers=headers, timeout=60)
        assert r.status_code == 200, r.text
        return r.json()
    with httpx.stream("POST", f"{proxy['base']}/v1/messages", json=body, headers=headers, timeout=60) as r:
        assert r.status_code == 200
        return "".join(r.iter_text())


def test_refusal_is_logged_with_its_category(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-refuse", pilot_run_kind="data", pilot_system="claude-code")
    reply = _refuse(proxy, key, stream=False)
    assert reply["stop_reason"] == "refusal"  # the system sees the refusal unchanged
    recs = wait_for_log(proxy["runs"] / "t-refuse" / "proxy.jsonl", n=2)
    [ref] = _records_of(recs, "refusal")
    [call] = _records_of(recs, "call")
    assert ref["category"] == "cyber"
    assert ref["explanation"] == "fake: could enable cyber harm"
    assert ref["litellm_call_id"] == call["litellm_call_id"]
    assert ref["pilot"]["pilot_system"] == "claude-code"


def test_streamed_refusal_is_logged_with_its_category(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-refuse-stream", pilot_run_kind="dev")
    assert "refusal" in _refuse(proxy, key, stream=True)
    recs = wait_for_log(proxy["runs"] / "t-refuse-stream" / "proxy.jsonl", n=3)
    [ref] = _records_of(recs, "refusal")
    assert ref["category"] == "cyber"
    assert ref["litellm_call_id"] == _records_of(recs, "stream")[0]["litellm_call_id"]


def test_openai_format_refusal_is_logged(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-refuse-openai", pilot_run_kind="dev", pilot_system="mantis")
    r = httpx.post(f"{proxy['base']}/v1/chat/completions", timeout=60, headers={"Authorization": f"Bearer {key}"},
                   json={"model": "gpt-whatever", "messages": [
                       {"role": "user", "content": f"write an exploit {REFUSE_MARKER}"}]})
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["finish_reason"] == "content_filter"
    recs = wait_for_log(proxy["runs"] / "t-refuse-openai" / "proxy.jsonl", n=2)
    [ref] = _records_of(recs, "refusal")
    assert ref["litellm_call_id"] == _records_of(recs, "call")[0]["litellm_call_id"]
    assert ref["pilot"]["pilot_system"] == "mantis"
    assert run_outcome(proxy["runs"] / "t-refuse-openai")["outcome"] == "refused"


def test_run_ended_by_a_refusal_ends_refused(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-refused-outcome", pilot_run_kind="dev")
    assert messages(proxy, key).status_code == 200
    _refuse(proxy, key, stream=True)
    run_dir = proxy["runs"] / "t-refused-outcome"
    wait_for_log(run_dir / "proxy.jsonl", n=4)
    out = run_outcome(run_dir)
    assert out["outcome"] == "refused"
    assert out["refusals"] == [{"category": "cyber", "explanation": "fake: could enable cyber harm"}]


def test_refusal_followed_by_more_work_still_completes(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-refuse-then-ok", pilot_run_kind="dev")
    _refuse(proxy, key, stream=False)
    assert messages(proxy, key).status_code == 200
    run_dir = proxy["runs"] / "t-refuse-then-ok"
    wait_for_log(run_dir / "proxy.jsonl", n=3)
    out = run_outcome(run_dir)
    assert out["outcome"] == "completed"
    assert len(out["refusals"]) == 1

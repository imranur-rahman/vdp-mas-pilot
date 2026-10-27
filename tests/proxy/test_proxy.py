"""Proxy behavior at seam 1: what goes upstream and what gets logged."""

import httpx
import pytest

from .conftest import wait_for_log

CLAUDE_CODE_MODEL = "claude-sonnet-5-5"


def messages(proxy, key, **body):
    payload = {"model": CLAUDE_CODE_MODEL, "max_tokens": 64,
               "messages": [{"role": "user", "content": "hi"}], **body}
    return httpx.post(f"{proxy['base']}/v1/messages", json=payload, timeout=60,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"})


def test_data_run_routes_to_haiku_on_anthropic_via_openrouter(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-data", pilot_run_kind="data", pilot_system="claude-code")
    r = messages(proxy, key)
    assert r.status_code == 200, r.text
    sent = upstream.last()
    assert sent["model"] == "anthropic/claude-haiku-5.5"
    assert sent["provider"] == {"order": ["anthropic"], "allow_fallbacks": False}


def test_dev_run_routes_to_haiku(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-dev", pilot_run_kind="dev")
    assert messages(proxy, key).status_code == 200
    assert upstream.last()["model"] == "anthropic/claude-haiku-5.5"


def test_key_without_run_kind_is_refused_and_never_reaches_upstream(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-nokind")
    before = len(upstream.requests)
    r = messages(proxy, key)
    assert r.status_code >= 400
    assert len(upstream.requests) == before


def test_adaptive_thinking_gets_summarized_display(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-think", pilot_run_kind="dev")
    messages(proxy, key, thinking={"type": "adaptive"})
    assert upstream.last()["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_legacy_budget_thinking_from_claude_code_ends_up_adaptive_and_summarized(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-legacy", pilot_run_kind="data")
    messages(proxy, key, max_tokens=32000, thinking={"type": "enabled", "budget_tokens": 31999})
    assert upstream.last()["thinking"] == {"type": "adaptive", "display": "summarized"}
    rec = wait_for_log(proxy["runs"] / "t-legacy" / "proxy.jsonl")[-1]
    assert rec["pilot"]["pilot_client_thinking"] == {"type": "enabled", "budget_tokens": 31999}


def test_between_tools_thinking_is_left_alone(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-bt", pilot_run_kind="dev")
    messages(proxy, key, thinking={"type": "between_tools"})
    assert upstream.last()["thinking"] == {"type": "between_tools"}


def test_rejected_sampling_settings_are_removed_and_logged(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-drop", pilot_run_kind="dev")
    messages(proxy, key, temperature=0.2, top_p=0.9)
    sent = upstream.last()
    assert "temperature" not in sent and "top_p" not in sent
    rec = wait_for_log(proxy["runs"] / "t-drop" / "proxy.jsonl")[-1]
    assert sorted(rec["pilot"]["pilot_dropped_params"]) == ["temperature", "top_p"]


def test_pilot_labels_are_logged_but_never_sent_upstream(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-log", pilot_run_kind="dev", pilot_system="claude-code", pilot_role="agent")
    messages(proxy, key, system="You are a careful auditor.")
    assert "pilot" not in str(upstream.last())
    rec = wait_for_log(proxy["runs"] / "t-log" / "proxy.jsonl")[-1]
    p = rec["pilot"]
    assert p["pilot_requested_model"] == CLAUDE_CODE_MODEL
    assert p["pilot_system"] == "claude-code" and p["pilot_role"] == "agent"
    assert p["pilot_prompt_hash"] and len(p["pilot_prompt_hash"]) == 16
    assert rec["status"] == "success"
    assert rec["standard_logging_object"]["response"]


def test_openai_format_requests_reach_the_same_upstream(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-openai", pilot_run_kind="dev")
    r = httpx.post(f"{proxy['base']}/v1/chat/completions", timeout=60,
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": "gpt-whatever", "messages": [
                       {"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    assert upstream.last()["model"] == "anthropic/claude-haiku-5.5"
    rec = wait_for_log(proxy["runs"] / "t-openai" / "proxy.jsonl")[-1]
    assert rec["pilot"]["pilot_requested_model"] == "gpt-whatever"


def test_calls_are_priced_so_budget_caps_can_fire(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-cost", pilot_run_kind="data")
    messages(proxy, key)
    rec = wait_for_log(proxy["runs"] / "t-cost" / "proxy.jsonl")[-1]
    # 10 input + 5 output tokens at Haiku 5.5's >100k-tier prices (conservative)
    assert rec["standard_logging_object"]["response_cost"] == pytest.approx(10 * 5e-7 + 5 * 2.5e-6)


def test_streamed_thinking_is_kept_raw_and_linked_to_its_call(proxy, upstream, make_key):
    key = make_key(pilot_run_id="t-stream", pilot_run_kind="dev")
    with httpx.stream("POST", f"{proxy['base']}/v1/messages", timeout=60,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                      json={"model": CLAUDE_CODE_MODEL, "max_tokens": 64, "stream": True,
                            "thinking": {"type": "adaptive"},
                            "messages": [{"role": "user", "content": "hi"}]}) as r:
        assert r.status_code == 200
        client_saw = "".join(r.iter_text())
    assert "fake streamed summary" in client_saw
    recs = wait_for_log(proxy["runs"] / "t-stream" / "proxy.jsonl", n=2)
    stream = next(r for r in recs if r["record"] == "stream")
    call = next(r for r in recs if r["record"] == "call")
    assert "fake streamed summary" in "".join(map(str, stream["events"]))
    assert stream["litellm_call_id"] and stream["litellm_call_id"] == call["litellm_call_id"]


def test_a_non_streamed_call_logs_the_request_it_carried(proxy, upstream, make_key):
    # LiteLLM leaves `complete_input_dict` empty for non-streamed /v1/messages
    # calls (VVAH's DeepAgents route), so the proxy logs the request itself.
    key = make_key(pilot_run_id="t-request", pilot_run_kind="dev")
    tools = [{"name": "read_file", "description": "read", "input_schema": {"type": "object"}}]
    r = messages(proxy, key, system=[{"type": "text", "text": "You are the verifier."}], tools=tools)
    assert r.status_code == 200, r.text
    records = wait_for_log(proxy["runs"] / "t-request" / "proxy.jsonl", n=2)
    call = next(x for x in records if x["record"] == "call")
    request = next(x for x in records if x["record"] == "request")
    assert request["litellm_call_id"] == call["litellm_call_id"]
    assert request["body"]["system"] == [{"type": "text", "text": "You are the verifier."}]
    assert request["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert request["body"]["tools"] == tools
    assert not {"metadata", "litellm_metadata", "proxy_server_request"} & set(request["body"])

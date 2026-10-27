"""LiteLLM proxy hooks for the pilot.

Loaded by the LiteLLM proxy container (see config.yaml). Stdlib only, so it
runs inside the stock image.

Pre-call: resolve the run from the virtual key's metadata, route to the run
kind's model without changing what the system asked for (ADR 0002), remove
sampling settings Sonnet 5.5 rejects (M-3), and attach pilot labels for the
log. Summarized thinking (M-1) and provider routing are set on the finished
upstream body (see finish_upstream_body).

Logging: append raw records to runs/<run_id>/proxy.jsonl: one `call` record
per call, a `stream` record with the raw events of each streamed call, a
`refusal` record with its category for each refused call (M-2), and a
`budget_exceeded` record when the run's key hits its cap (D6).
"""

import functools
import hashlib
import json
import os
import time
from pathlib import Path

from litellm.integrations.custom_logger import CustomLogger
from litellm.llms.anthropic.chat.transformation import AnthropicConfig
from litellm.llms.anthropic.pass_through.messages.transformation import AnthropicMessagesConfig

RUNS_DIR = Path(os.environ.get("PILOT_RUNS_DIR", "/runs"))
ROUTES = {"data": "pilot-data", "dev": "pilot-dev", "tooling": "pilot-tooling"}
REJECTED_SAMPLING = ("temperature", "top_p", "top_k")
# Finishing touches on the upstream body, applied after LiteLLM's own request
# builders: they keep only known Anthropic fields (dropping `provider`), and
# they rewrite legacy `thinking: enabled + budget_tokens` (sent by Claude Code)
# into `adaptive` after the pre-call hook has run. Pinned to LiteLLM 1.106.4;
# tests/proxy guards both paths.
# Request fields kept in a `request` record (the conversation and its settings;
# never the proxy's own metadata or credentials).
REQUEST_FIELDS = ("model", "system", "messages", "tools", "tool_choice", "thinking", "output_config",
                  "max_tokens", "stop_sequences", "temperature", "top_p", "top_k", "stream")
PROVIDER_ROUTING = {"order": ["anthropic"], "allow_fallbacks": False}


def finish_upstream_body(body: dict) -> dict:
    body["provider"] = PROVIDER_ROUTING  # ADR 0003
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "adaptive" \
            and thinking.get("display") in (None, "omitted"):
        thinking["display"] = "summarized"  # M-1; between_tools takes no display
    return body


def _finish(build):
    @functools.wraps(build)
    def wrapper(*args, **kwargs):
        return finish_upstream_body(build(*args, **kwargs))
    return wrapper


AnthropicMessagesConfig.transform_anthropic_messages_request = _finish(
    AnthropicMessagesConfig.transform_anthropic_messages_request)
AnthropicConfig.transform_request = _finish(AnthropicConfig.transform_request)


def system_prompt_text(data: dict) -> str:
    """System prompt of an Anthropic- or OpenAI-format request, as plain text."""
    parts = []
    system = data.get("system")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        parts += [b.get("text", "") for b in system if isinstance(b, dict)]
    for m in data.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "system":
            c = m.get("content")
            parts.append(c if isinstance(c, str) else json.dumps(c, sort_keys=True))
    return "\n".join(parts)


def prompt_hash(data: dict) -> str | None:
    text = system_prompt_text(data)
    return hashlib.sha256(text.encode()).hexdigest()[:16] if text else None


def metadata_key(data: dict) -> str:
    # /v1/messages carries Anthropic's own `metadata` field upstream; LiteLLM
    # keeps proxy metadata under `litellm_metadata` there.
    return "litellm_metadata" if "litellm_metadata" in data else "metadata"


class PilotHooks(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        key_meta = getattr(user_api_key_dict, "metadata", None) or {}
        kind = key_meta.get("pilot_run_kind")
        if kind not in ROUTES:
            raise ValueError("pilot: key has no pilot_run_kind (data|dev|tooling); refusing to route")

        requested_model = data.get("model")
        data["model"] = ROUTES[kind]

        dropped = [p for p in REJECTED_SAMPLING if p in data]
        for p in dropped:
            data.pop(p)

        mk = metadata_key(data)
        data.setdefault(mk, {})
        data[mk].update({
            "pilot_run_id": key_meta.get("pilot_run_id"),
            "pilot_run_kind": kind,
            "pilot_system": key_meta.get("pilot_system"),
            "pilot_role": key_meta.get("pilot_role"),
            "pilot_requested_model": requested_model,
            "pilot_dropped_params": dropped,
            "pilot_client_thinking": data.get("thinking"),
            "pilot_prompt_hash": prompt_hash(data),
            "pilot_call_type": str(call_type),
        })
        return data

    async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data):
        # LiteLLM's reassembled streaming response drops thinking text, so keep
        # every raw event; the exporter rebuilds the message from them.
        events = []
        try:
            async for item in response:
                events.append(item)
                yield item
        finally:
            pilot = _pilot_labels(request_data)
            raw = [_event(e) for e in events]
            self._append(pilot.get("pilot_run_id"), {
                "logged_at": time.time(),
                "record": "stream",
                "litellm_call_id": request_data.get("litellm_call_id"),
                "pilot": pilot,
                "events": raw,
            })
            details = _streamed_refusal(raw)
            if details is not None:
                self._refusal(request_data, pilot, details)

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        if not data.get("stream"):
            # LiteLLM logs no `complete_input_dict` for non-streamed /v1/messages
            # calls, so keep the request as the client sent it (after pre-call).
            pilot = _pilot_labels(data)
            self._append(pilot.get("pilot_run_id"), {
                "logged_at": time.time(),
                "record": "request",
                "litellm_call_id": data.get("litellm_call_id"),
                "pilot": pilot,
                "body": {k: data[k] for k in REQUEST_FIELDS if k in data},
            })
        body = response if isinstance(response, dict) else \
            getattr(response, "model_dump", lambda: {})()
        if isinstance(body, dict) and body.get("stop_reason") == "refusal":
            self._refusal(data, _pilot_labels(data), body.get("stop_details") or {})
        return response

    async def async_post_call_failure_hook(self, request_data, original_exception, user_api_key_dict,
                                           traceback_str=None):
        # A key over its cap is rejected before the call exists, so no `call`
        # record is written; label the rejection from the key itself.
        if "budget" not in f"{type(original_exception).__name__} {original_exception}".lower():
            return None
        key_meta = getattr(user_api_key_dict, "metadata", None) or {}
        pilot = {k: v for k, v in key_meta.items() if k.startswith("pilot_")}
        self._append(pilot.get("pilot_run_id"), {
            "logged_at": time.time(),
            "record": "budget_exceeded",
            "pilot": pilot,
            "spend": getattr(user_api_key_dict, "spend", None),
            "max_budget": getattr(user_api_key_dict, "max_budget", None),
            "requested_model": request_data.get("model"),
            "exception": str(original_exception),
        })
        return None

    def _refusal(self, request_data, pilot, details):
        self._append(pilot.get("pilot_run_id"), {
            "logged_at": time.time(),
            "record": "refusal",
            "litellm_call_id": request_data.get("litellm_call_id"),
            "pilot": pilot,
            "category": details.get("category"),
            "explanation": details.get("explanation"),
            "stop_details": details,
        })

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._write(kwargs, response_obj, start_time, end_time, "success")

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._write(kwargs, response_obj, start_time, end_time, "failure")

    def _write(self, kwargs, response_obj, start_time, end_time, status):
        params = kwargs.get("litellm_params") or {}
        meta = {}
        for k in ("metadata", "litellm_metadata"):
            meta.update(params.get(k) or {})
        pilot = {k: v for k, v in meta.items() if k.startswith("pilot_")}
        run_id = pilot.get("pilot_run_id") or "_unassigned"
        slo = kwargs.get("standard_logging_object") or {}
        record = {
            "logged_at": time.time(),
            "record": "call",
            "litellm_call_id": kwargs.get("litellm_call_id"),
            "status": status,
            "start_time": str(start_time),
            "end_time": str(end_time),
            "pilot": pilot,
            "upstream_request": (kwargs.get("additional_args") or {}).get("complete_input_dict"),
            "standard_logging_object": slo,
            "response": response_obj if isinstance(response_obj, (dict, list, str)) else
                        getattr(response_obj, "model_dump", lambda: str(response_obj))(),
            "exception": str(kwargs.get("exception")) if kwargs.get("exception") else None,
        }
        self._append(run_id, record)

    @staticmethod
    def _append(run_id, record):
        out = RUNS_DIR / (run_id or "_unassigned") / "proxy.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")


def _pilot_labels(request_data: dict) -> dict:
    meta = {}
    for k in ("metadata", "litellm_metadata"):
        meta.update(request_data.get(k) or {})
    return {k: v for k, v in meta.items() if k.startswith("pilot_")}


def _streamed_refusal(raw_events: list) -> dict | None:
    """stop_details of a streamed call that stopped with `refusal`, else None."""
    for event in raw_events:
        for line in str(event).split("\n"):
            if not line.startswith("data:"):
                continue
            try:
                d = json.loads(line[5:])
            except ValueError:
                continue
            delta = d.get("delta") if isinstance(d, dict) and d.get("type") == "message_delta" else None
            if isinstance(delta, dict) and delta.get("stop_reason") == "refusal":
                return delta.get("stop_details") or {}
    return None


def _event(item):
    if isinstance(item, bytes):
        return item.decode("utf-8", "replace")
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return item if isinstance(item, (str, dict, list)) else str(item)


handler = PilotHooks()

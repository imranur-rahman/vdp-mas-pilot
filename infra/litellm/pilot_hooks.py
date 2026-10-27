"""LiteLLM proxy hooks for the pilot.

Loaded by the LiteLLM proxy container (see config.yaml). Stdlib only, so it
runs inside the stock image.

Pre-call: resolve the run from the virtual key's metadata, route to the run
kind's model without changing what the system asked for (ADR 0002), remove
sampling settings Sonnet 5.5 rejects (M-3), ask for summarized thinking
(M-1), and attach pilot labels for the log.

Logging: append one raw record per call to runs/<run_id>/proxy.jsonl.
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
# OpenRouter provider routing (ADR 0003). LiteLLM's Anthropic request builders
# keep only known Anthropic fields, so `provider` is added to the finished
# body. Pinned to LiteLLM 1.106.4; tests/proxy guards both paths.
PROVIDER_ROUTING = {"order": ["anthropic"], "allow_fallbacks": False}


def _add_provider_routing(build):
    @functools.wraps(build)
    def wrapper(*args, **kwargs):
        body = build(*args, **kwargs)
        body["provider"] = PROVIDER_ROUTING
        return body
    return wrapper


AnthropicMessagesConfig.transform_anthropic_messages_request = _add_provider_routing(
    AnthropicMessagesConfig.transform_anthropic_messages_request)
AnthropicConfig.transform_request = _add_provider_routing(AnthropicConfig.transform_request)


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

        display_set = False
        thinking = data.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "adaptive" \
                and thinking.get("display") in (None, "omitted"):
            thinking["display"] = "summarized"
            display_set = True

        mk = metadata_key(data)
        data.setdefault(mk, {})
        data[mk].update({
            "pilot_run_id": key_meta.get("pilot_run_id"),
            "pilot_run_kind": kind,
            "pilot_system": key_meta.get("pilot_system"),
            "pilot_role": key_meta.get("pilot_role"),
            "pilot_requested_model": requested_model,
            "pilot_dropped_params": dropped,
            "pilot_thinking_display_set": display_set,
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
            meta = {}
            for k in ("metadata", "litellm_metadata"):
                meta.update(request_data.get(k) or {})
            pilot = {k: v for k, v in meta.items() if k.startswith("pilot_")}
            self._append(pilot.get("pilot_run_id"), {
                "logged_at": time.time(),
                "record": "stream",
                "litellm_call_id": request_data.get("litellm_call_id"),
                "pilot": pilot,
                "events": [_event(e) for e in events],
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


def _event(item):
    if isinstance(item, bytes):
        return item.decode("utf-8", "replace")
    if hasattr(item, "model_dump"):
        return item.model_dump()
    return item if isinstance(item, (str, dict, list)) else str(item)


handler = PilotHooks()

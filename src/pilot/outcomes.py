"""Run outcomes, read from a run folder's raw proxy log (M-2, D6).

`budget_exhausted` (the proxy rejected a call because the run's cap was
hit), `refused` (the run's last model call was refused), `adapter_error` (our
adapter raised, or the system never made a model call), else `completed`, in
that order of precedence. A system that ran and failed on its own (nonzero
exit) completed; so did one stopped by its time limit. `out_of_scope` is
decided by an adapter, not from the log.
"""

import json
from pathlib import Path


def read_proxy_log(run_dir: Path) -> list[dict]:
    log = Path(run_dir) / "proxy.jsonl"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


def stream_stops(records: list[dict]) -> list[dict]:
    """stop_reason, stop_details and OpenRouter's cost of each streamed call, from the raw events.

    LiteLLM's reassembled response does not carry Anthropic's stop details.
    """
    stops = []
    for r in records:
        if r.get("record") != "stream":
            continue
        chunks = [e for e in r.get("events") or [] if isinstance(e, dict) and e.get("object") == "chat.completion.chunk"]
        if chunks:  # OpenAI-format stream: finish_reason and the last usage carry stop and cost
            finish = next((c["finish_reason"] for e in chunks for c in e.get("choices") or []
                           if c.get("finish_reason")), None)
            usage = next((e["usage"] for e in reversed(chunks) if e.get("usage")), {})
            stops.append({"litellm_call_id": r.get("litellm_call_id"),
                          "stop_reason": "refusal" if finish == "content_filter" else finish,
                          "stop_details": None, "cost": usage.get("cost")})
            continue
        for event in r.get("events") or []:
            for line in str(event).split("\n"):
                if not line.startswith("data:"):
                    continue
                try:
                    d = json.loads(line[5:])
                except ValueError:
                    continue
                if d.get("type") == "message_delta":
                    delta = d.get("delta") or {}
                    stops.append({"litellm_call_id": r.get("litellm_call_id"),
                                  "stop_reason": delta.get("stop_reason"),
                                  "stop_details": delta.get("stop_details"),
                                  "cost": (d.get("usage") or {}).get("cost")})
    return stops


def refusals(records: list[dict]) -> dict[str, dict]:
    """Refused calls by litellm_call_id: the proxy's `refusal` records, plus
    refusals in raw stream events (logs written before `refusal` records existed)."""
    out = {}
    for s in stream_stops(records):
        if s["stop_reason"] == "refusal":
            out[s["litellm_call_id"]] = s["stop_details"] or {}
    for r in records:
        if r.get("record") == "refusal":
            out[r.get("litellm_call_id")] = r.get("stop_details") or \
                {"category": r.get("category"), "explanation": r.get("explanation")}
    return out


def _is_timeout(error: str) -> bool:
    """The run's time limit stopped the system: a run limit, like the budget, not an adapter fault."""
    return "timeout" in error.lower() or "timed out" in error.lower()


def _response_cost(call: dict):
    """OpenRouter's actual cost of a non-streamed call, from the response's usage."""
    resp = call.get("response")
    usage = resp.get("usage") if isinstance(resp, dict) else None
    return usage.get("cost") if isinstance(usage, dict) else None


def run_outcome(run_dir: Path, *, error: str | None = None, exit_code: int | None = None) -> dict:
    """`error` is the adapter's exception, if any; `exit_code` the system's, if it has one."""
    records = read_proxy_log(run_dir)
    calls = sorted((r for r in records if r.get("record") == "call"), key=lambda r: str(r.get("start_time")))
    stops = stream_stops(records)
    refused = refusals(records)
    budget_hits = [r for r in records if r.get("record") == "budget_exceeded"]
    if budget_hits:
        outcome = "budget_exhausted"
    elif calls and calls[-1].get("litellm_call_id") in refused:
        outcome = "refused"
    elif (error and not _is_timeout(error)) or not calls:
        outcome = "adapter_error"
    else:
        outcome = "completed"
    streamed = {s["litellm_call_id"] for s in stops}
    costs = [s["cost"] for s in stops] + [_response_cost(c) for c in calls if c.get("litellm_call_id") not in streamed]
    return {
        "outcome": outcome, "model_calls": len(calls), "system_exit_code": exit_code,
        "refusals": [{"category": d.get("category"), "explanation": d.get("explanation")}
                     for d in refused.values()],
        "budget_rejections": len(budget_hits),
        "cost_usd_openrouter": round(sum(c or 0 for c in costs), 4),
        "cost_usd_estimate": round(sum((r.get("standard_logging_object") or {}).get("response_cost") or 0
                                       for r in calls), 4),
    }

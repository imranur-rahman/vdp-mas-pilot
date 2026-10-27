"""Run outcomes, read from a run folder's raw proxy log (M-2, D6).

`completed`, `refused` (the run's last model call was refused), or
`budget_exhausted` (the proxy rejected a call because the run's key hit its
cap). `out_of_scope` is decided by an adapter, not from the log.
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


def run_outcome(run_dir: Path) -> dict:
    records = read_proxy_log(run_dir)
    calls = sorted((r for r in records if r.get("record") == "call"), key=lambda r: str(r.get("start_time")))
    stops = stream_stops(records)
    refused = refusals(records)
    budget_hits = [r for r in records if r.get("record") == "budget_exceeded"]
    if budget_hits:
        outcome = "budget_exhausted"
    elif calls and calls[-1].get("litellm_call_id") in refused:
        outcome = "refused"
    else:
        outcome = "completed"
    return {
        "outcome": outcome, "model_calls": len(calls),
        "refusals": [{"category": d.get("category"), "explanation": d.get("explanation")}
                     for d in refused.values()],
        "budget_rejections": len(budget_hits),
        "cost_usd_openrouter": round(sum(s["cost"] or 0 for s in stops), 4),
        "cost_usd_estimate": round(sum((r.get("standard_logging_object") or {}).get("response_cost") or 0
                                       for r in calls), 4),
    }

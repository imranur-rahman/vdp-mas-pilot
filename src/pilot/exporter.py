"""Exporter: a run folder goes in, a trajectory bundle comes out (ADR 0001).

Reads only the run folder: `run.json`, the raw proxy log (`proxy.jsonl`, or
`proxy.jsonl.gz` in test fixtures), CyberGym's output under `cybergym/`, and
what an adapter's collector wrote: `role_windows.json` (when each role made
its calls, for systems with no per-role key), `handoffs.jsonl` (hand-offs and
code decisions, with references to the state captured there) and `state/`.
Never Phoenix. The bundle is written to `<run folder>/bundle/`:

- `steps.jsonl`: one step record per line (docs/DECISIONS.md, Step record)
- `run.json`: the run's metadata and outcome
- `prompts/<prompt_id>.json`: each distinct system prompt and tool list, once;
  `prompts/claim-extraction-*.json`: the prompt that extracted the claims
- `state/`: the state captured at each hand-off (collector output, copied)
- `oracle/`: the PoC and patch each CyberGym validation checked, and the task's
  known patch (`oracle/ground_truth.patch`)
- `findings.json`: normalized findings with their `tp_target` checks (verdicts.py)
- `trajectory.md`: the run rendered for a human reader
"""

import gzip
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import claims, stages, verdicts

STEP_FIELDS = (
    "run_id", "task_id", "system", "iteration",
    "step_id", "seq", "parent_step_id", "step_type",
    "agent_instance_id", "role", "stage", "stage_options",
    "input_delta", "input_prefix_hash",
    "output", "thinking_summary", "tool_calls",
    "prompt_id", "skill", "claim",
    "model", "tokens", "cost_usd", "latency_ms", "ts_start", "ts_end",
    "state_ref", "oracle_ref", "refusal_category", "dropped_params",
)

THINKING_KIND = "model_written_summary"
CYBERGYM_STAGES = ("stage1", "stage2", "stage3", "stage4")
# What each CyberGym-E2E stage checks (vendor/cybergym-e2e/SUBMISSION.md):
# 1 agent PoC on the unpatched build; 2 agent PoC on the patched build;
# 3 the project's tests on the patched build; 4 the ground-truth PoC on the patched build.
STAGE_CHECKS = {1: ("poc",), 2: ("poc", "patch"), 3: ("patch",), 4: ("patch",)}


def load_proxy_log(run_dir: Path) -> list[dict]:
    """The raw proxy records of a run, in the order the proxy wrote them."""
    run_dir = Path(run_dir)
    plain, packed = run_dir / "proxy.jsonl", run_dir / "proxy.jsonl.gz"
    if plain.exists():
        text = plain.read_text()
    elif packed.exists():
        text = gzip.decompress(packed.read_bytes()).decode()
    else:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def export(run_dir: Path, out_dir: Path | None = None, *, stage_map: Path = stages.DEFAULT_MAP,
           tasks_dir: Path = verdicts.DEFAULT_TASKS, extractor: claims.Extractor | None = None) -> Path:
    """Build the trajectory bundle for one run folder and return its path.

    `stage_map` is the versioned role-to-stage map joined onto the steps (T-5).
    `tasks_dir` holds each task's `patch.diff` for the `tp_target` checks (F-1),
    used when the run folder has no `ground_truth/patch.diff`.
    Claims come from the run folder's extraction recording; `extractor` (a live
    model call, see claims.py) answers only what the recording lacks (T-3).
    """
    run_dir = Path(run_dir)
    bundle = Path(out_dir) if out_dir else run_dir / "bundle"
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    run = json.loads((run_dir / "run.json").read_text())
    base = {"run_id": run["run_id"], "task_id": run["task"], "system": run["system"]}

    records = load_proxy_log(run_dir)
    llm = _llm_steps(records, bundle, _load_json(run_dir / "role_windows.json") or [])
    attempts, oracle = _oracle_steps(run_dir, bundle)
    iteration = attempts[-1] if attempts else 1
    agent_steps = _with_handoffs(llm, _handoff_steps(run_dir, bundle))
    steps = [{**_empty_step(), **base, "iteration": iteration, **s} for s in agent_steps]
    steps += [{**_empty_step(), **base, **s} for s in oracle]
    steps += [{**_empty_step(), **base, "iteration": iteration, **s}
              for s in _collector_steps(run_dir, bundle)]
    for seq, s in enumerate(steps, 1):
        s["seq"] = seq
        s["step_id"] = f"s{seq:04d}"
    _link_parents(steps)
    _join_native_traces(steps, run_dir)
    run = {**run, "stage_map": stages.join_stages(steps, stages.load_map(stage_map))}
    run["claims"] = claims.extract(steps, run_dir, bundle, extractor)

    (bundle / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps))
    (bundle / "run.json").write_text(json.dumps(run, indent=2))
    findings = verdicts.build_findings(run_dir, run, bundle, tasks_dir)
    (bundle / "trajectory.md").write_text(render(run, steps, bundle, findings))
    return bundle


def _empty_step() -> dict:
    """Every step record field, present and empty until a source fills it."""
    empty = dict.fromkeys(STEP_FIELDS)
    empty.update(input_delta=[], tool_calls=[], dropped_params=[])
    return empty


# --- model calls ---------------------------------------------------------------

def _llm_steps(records: list[dict], bundle: Path, role_windows: list[dict] = ()) -> list[dict]:
    streams = {r["litellm_call_id"]: r for r in records if r.get("record") == "stream"}
    requests = {r["litellm_call_id"]: r.get("body") or {} for r in records if r.get("record") == "request"}
    calls = [r for r in records if r.get("record") == "call"]
    instances: dict[str, list] = {}  # role:prompt -> [[agent instance, messages of its last call]]
    pending_tool_calls: dict[str, dict] = {}  # tool_use id -> tool call awaiting a result
    steps = []

    for i, call in enumerate(calls):
        pilot = call.get("pilot") or {}
        request = _request_of(call, requests)
        prompt_id = pilot.get("pilot_prompt_hash")
        ts_start, ts_end = _ts(call.get("start_time")), _ts(call.get("end_time"))
        role = pilot.get("pilot_role")
        role_source = "per_role_key" if role else None
        if role is None and role_windows:
            role = _role_at(role_windows, ts_start)
            role_source = "stage_window" if role else None
        _save_prompt(bundle, prompt_id, request)

        raw = request.get("messages") or []
        messages = normalize_messages(raw)
        agent, earlier = _agent_instance(
            instances, f"{role or pilot.get('pilot_system') or 'agent'}:{prompt_id}", messages)
        if earlier is not None:
            delta, prefix_hash = messages[len(earlier):], _hash(earlier)
        else:
            delta, prefix_hash = messages, None

        for m in delta:
            for block in m["content"]:
                if block.get("type") == "tool_result" and block.get("tool_use_id") in pending_tool_calls:
                    tc = pending_tool_calls.pop(block["tool_use_id"])
                    tc["result"] = block.get("content")
                    tc["is_error"] = block.get("is_error")
                    tc["timing"]["result_seen_at"] = ts_start

        msg = _reassemble(streams.get(call["litellm_call_id"]), call)
        tool_calls = []
        for block in msg["content"]:
            if block["type"] == "tool_use":
                tc = {"id": block["id"], "name": block["name"], "args": block["input"],
                      "result": None, "is_error": None,
                      "timing": {"issued_at": ts_end, "result_seen_at": None}}
                tool_calls.append(tc)
                pending_tool_calls[block["id"]] = tc
        text = "".join(b["text"] for b in msg["content"] if b["type"] == "text")
        thinking = "".join(b["thinking"] for b in msg["content"] if b["type"] == "thinking")

        refused = msg["stop_reason"] == "refusal"
        usage = msg["usage"]
        steps.append({
            "step_type": "refusal" if refused else "llm_step",
            "agent_instance_id": agent,
            "role": role,
            "role_source": role_source,
            "input_delta": delta,
            "input_prefix_hash": prefix_hash,
            "output": text or None,
            "thinking_summary": {"kind": THINKING_KIND, "text": thinking} if thinking else None,
            "tool_calls": tool_calls,
            "prompt_id": prompt_id,
            "model": msg["model"] or request.get("model"),
            "tokens": {
                "input": usage.get("input_tokens"),
                "output": usage.get("output_tokens"),
                "cache_read": usage.get("cache_read_input_tokens"),
                "cache_write": usage.get("cache_creation_input_tokens"),
            },
            "cost_usd": usage.get("cost"),
            "latency_ms": round((ts_end - ts_start) * 1000) if ts_start and ts_end else None,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "refusal_category": (msg["stop_details"] or {}).get("category") if refused else None,
            "dropped_params": pilot.get("pilot_dropped_params") or [],
            "stop_reason": msg["stop_reason"],
            "litellm_call_id": call["litellm_call_id"],
            "_agent": agent,
        })
    return steps


def _request_of(call: dict, requests: dict[str, dict]) -> dict:
    """The request a call carried. LiteLLM logs no `complete_input_dict` for
    non-streamed /v1/messages calls; the proxy then writes a `request` record.
    Logs from before that record keep only the messages and tools, in LiteLLM's
    own logging object."""
    if call.get("upstream_request"):
        return call["upstream_request"]
    if call.get("litellm_call_id") in requests:
        return requests[call["litellm_call_id"]]
    slo = call.get("standard_logging_object") or {}
    if not slo.get("messages"):
        return {}
    return {"messages": slo["messages"], "tools": (slo.get("model_parameters") or {}).get("tools"),
            "system": None, "_source": "standard_logging_object (system prompt not logged)"}


def _role_at(windows: list[dict], t: float | None) -> str | None:
    if t is None:
        return None
    for w in windows:
        if w["start"] <= t <= w["end"]:
            return w["role"]
    return None


def _agent_instance(instances: dict, key: str, messages: list) -> tuple[str, list | None]:
    """The running instance of an agent that made this call: the one whose last
    call's messages are the longest prefix of this call's, so parallel sessions
    with one prompt stay apart. No match starts a new instance (`key`, `key#2`, ...).

    Returns the instance and the messages of its previous call (None if new).
    """
    known = instances.setdefault(key, [])  # [name, messages of its last call, their canonical form]
    canon = [_canonical(m) for m in messages]
    best = None
    for n, (_, last, last_canon) in enumerate(known):
        if canon[:len(last_canon)] == last_canon and (best is None or len(last_canon) > len(known[best][2])):
            best = n
    if best is None:
        name = key if not known else f"{key}#{len(known) + 1}"
        known.append([name, messages, canon])
        return name, None
    name, earlier, _ = known[best]
    known[best][1:] = [messages, canon]
    return name, earlier


def _canonical(message: dict) -> dict:
    """A message for matching only: a tool result given as a plain string equals
    the same text as one text block (DeepAgents resends results in either form)."""
    content = []
    for block in message.get("content") or []:
        if block.get("type") == "tool_result" and isinstance(block.get("content"), str):
            block = {**block, "content": [{"type": "text", "text": block["content"]}]}
        content.append(block)
    return {**message, "content": content}


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Messages as conversation content, without transport detail.

    Clients move the `cache_control` marker to the newest message on every
    call and may send a lone text block as a plain string; neither changes
    what the model was told, and both would break prefix matching.
    """
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k != "cache_control"}
        if isinstance(o, list):
            return [strip(x) for x in o]
        return o

    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        out.append(strip({**m, "content": content or []}))
    return out


def _reassemble(stream: dict | None, call: dict) -> dict:
    """The model's response as Anthropic content blocks, rebuilt from the raw SSE.

    LiteLLM's reassembled response drops thinking and stop details, so the
    stream is the source. Events are byte chunks, not lines: join them first.
    """
    msg = {"content": [], "stop_reason": None, "stop_details": None, "usage": {}, "model": None}
    blocks: dict[int, dict] = {}
    partial_json: dict[int, str] = {}
    text = "".join(str(e) for e in (stream or {}).get("events") or [])
    for line in text.split("\n"):
        if not line.startswith("data:"):
            continue
        try:
            d = json.loads(line[5:])
        except ValueError:
            continue
        kind = d.get("type")
        if kind == "message_start":
            m = d.get("message") or {}
            msg["model"] = m.get("model")
            msg["usage"] = dict(m.get("usage") or {})
        elif kind == "content_block_start":
            block = dict(d["content_block"])
            if block["type"] == "tool_use":
                block["input"] = {}
                partial_json[d["index"]] = ""
            blocks[d["index"]] = block
        elif kind == "content_block_delta":
            delta, block = d["delta"], blocks.setdefault(d["index"], {"type": "text", "text": ""})
            if delta["type"] == "text_delta":
                block["text"] = block.get("text", "") + delta["text"]
            elif delta["type"] == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta["thinking"]
            elif delta["type"] == "input_json_delta":
                partial_json[d["index"]] = partial_json.get(d["index"], "") + delta["partial_json"]
        elif kind == "message_delta":
            delta = d.get("delta") or {}
            msg["stop_reason"] = delta.get("stop_reason")
            msg["stop_details"] = delta.get("stop_details")
            msg["usage"].update(d.get("usage") or {})
    for index, raw in partial_json.items():
        try:
            blocks[index]["input"] = json.loads(raw) if raw else {}
        except ValueError:
            blocks[index]["input"] = {"_unparsed": raw}
    msg["content"] = [blocks[i] for i in sorted(blocks)]
    if not stream:  # non-streamed call: fall back to LiteLLM's response
        msg.update(_from_litellm_response(call))
    return msg


def _from_litellm_response(call: dict) -> dict:
    resp = call.get("response") or {}
    choice = (resp.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {"_unparsed": fn.get("arguments")}
        content.append({"type": "tool_use", "id": tc.get("id"), "name": fn.get("name"), "input": args})
    usage = resp.get("usage") or {}
    return {
        "content": content,
        "model": resp.get("model"),
        "stop_reason": "refusal" if choice.get("finish_reason") == "content_filter" else choice.get("finish_reason"),
        "usage": {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
                  "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                  "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                  "cost": usage.get("cost")},
    }


def _save_prompt(bundle: Path, prompt_id: str | None, request: dict) -> None:
    if not prompt_id:
        return
    path = bundle / "prompts" / f"{prompt_id}.json"
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"system": request.get("system"), "tools": request.get("tools")}, indent=2))


def _hash(messages: list) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()[:16]


def _ts(value) -> float | None:
    """Proxy timestamps are naive UTC strings ('2026-10-27 04:04:51.428779')."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def _link_parents(steps: list[dict]) -> None:
    """parent_step_id: the same agent's previous call; an oracle run's parent is the last model call."""
    last_of_agent: dict[str, str] = {}
    last_llm = None
    for s in steps:
        agent = s.pop("_agent", None)
        if agent is not None:
            s["parent_step_id"] = last_of_agent.get(agent)
            last_of_agent[agent] = last_llm = s["step_id"]
        elif s["step_type"] in ("oracle_run", "code_decision", "handoff"):
            s["parent_step_id"] = last_llm


# --- hand-offs and code decisions ---------------------------------------------

def _handoff_steps(run_dir: Path, bundle: Path) -> list[dict]:
    """One `handoff` or `code_decision` step per record in the run folder's
    `handoffs.jsonl` (an adapter's collector output). The captured state is
    copied into the bundle's `state/`; `state_ref` paths name those copies."""
    path = run_dir / "handoffs.jsonl"
    if not path.exists():
        return []
    if (run_dir / "state").is_dir():
        shutil.copytree(run_dir / "state", bundle / "state", dirs_exist_ok=True)
    steps = []
    for h in (json.loads(l) for l in path.read_text().splitlines() if l.strip()):
        steps.append({
            "step_type": h.get("step_type") or "handoff",
            "role": h.get("role"),
            "output": h.get("summary"),
            "ts_start": h.get("t"),
            "ts_end": h.get("t"),
            "state_ref": {**(h.get("state_ref") or {}), "stage_label": h.get("stage")},
        })
    return steps


def _with_handoffs(llm: list[dict], handoffs: list[dict]) -> list[dict]:
    """Hand-offs placed among model calls by time: before the first call that
    started after them. Model calls keep their order."""
    if not handoffs:
        return llm
    out, pending = [], sorted(handoffs, key=lambda h: h["ts_start"] or 0)
    for s in llm:
        while pending and s["ts_start"] is not None and (pending[0]["ts_start"] or 0) <= s["ts_start"]:
            out.append(pending.pop(0))
        out.append(s)
    return out + pending


def _load_json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


# --- CyberGym validation ------------------------------------------------------

def _oracle_steps(run_dir: Path, bundle: Path) -> tuple[list[int], list[dict]]:
    """One `oracle_run` step per CyberGym validation stage per attempt.

    Each step links (bundle-relative paths) to the PoC and patch it checked;
    the files are copied into `oracle/attempt_<n>/` so the bundle stands alone.
    """
    attempts, steps = [], []
    for summary_path in sorted((run_dir / "cybergym").glob("*/*/summary.json")):
        summary = json.loads(summary_path.read_text())
        out = summary_path.parent / "output"
        # A run checked per patch (an adapter's `patch_scope`) keeps each scope's files apart.
        scope = f"{summary['patch_scope']}/" if summary.get("patch_scope") else ""
        for attempt in summary.get("attempts") or []:
            n = attempt.get("attempt", 1)
            attempts.append(n)
            poc = _copy_first(bundle, f"oracle/{scope}attempt_{n}/poc.bin",
                              out / f"poc_attempt_{n}.bin", out / "poc.bin")
            patch = _copy_first(bundle, f"oracle/{scope}attempt_{n}/fix.patch",
                                out / f"fix_attempt_{n}.patch", out / "fix.patch")
            for number, stage in enumerate(CYBERGYM_STAGES, 1):
                if stage not in attempt:
                    continue
                steps.append({
                    "step_type": "oracle_run",
                    "iteration": n,
                    "output": attempt[stage],
                    "oracle_ref": {
                        "source": "cybergym",
                        "stage": number,
                        "attempt": n,
                        "poc": poc if "poc" in STAGE_CHECKS[number] else None,
                        "patch": patch if "patch" in STAGE_CHECKS[number] else None,
                        "ground_truth_poc": number == 4,
                        "patch_scope": summary.get("patch_scope"),
                        "summary": str(summary_path.relative_to(run_dir)),
                    },
                })
    return attempts, steps


def _copy_first(bundle: Path, dest: str, *candidates: Path) -> str | None:
    for src in candidates:
        if src.is_file():
            (bundle / dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, bundle / dest)
            return dest
    return None


# --- Layer-2 collector: sandbox runs and native-trace roles -------------------
# Hand-offs and code decisions come from the run-root `handoffs.jsonl` (above).
# `<run>/collector/oracle_runs.jsonl` holds PoC/commands a system ran in its own
# sandbox (oracle_run); `collector/adk_spans.jsonl` holds native agent spans.
# A run without `collector/` gets neither, so other bundles are unchanged.

def _join_native_traces(steps: list[dict], run_dir: Path) -> None:
    """Set each model call's `role` and `agent_instance_id` from the ADK agent
    span that produced it (D2, bundle native traces).

    A span is matched to a call by `litellm_call_id`/`call_id` when the trace
    carries it, else by order among the model-call steps. A run with no
    `collector/adk_spans.jsonl` is left untouched (the proxy's own role label,
    or the prompt hash, stands)."""
    spans = _read_jsonl(Path(run_dir) / "collector" / "adk_spans.jsonl")
    if not spans:
        return
    by_call = {s.get("call_id") or s.get("litellm_call_id"): s for s in spans if
               s.get("call_id") or s.get("litellm_call_id")}
    calls = [s for s in steps if s["step_type"] in ("llm_step", "refusal")]
    for i, step in enumerate(calls):
        span = by_call.get(step.get("litellm_call_id")) or (spans[i] if i < len(spans) else None)
        if span is None:
            continue
        role = span.get("role") or span.get("name")
        if not role:
            continue
        step["role"] = role
        step["agent_instance_id"] = f"{role}:{step.get('prompt_id')}"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _collector_steps(run_dir: Path, bundle: Path) -> list[dict]:
    collector = Path(run_dir) / "collector"
    steps = [_sandbox_step(r) for r in _read_jsonl(collector / "oracle_runs.jsonl")]
    steps.sort(key=lambda s: (s["ts_start"] if s["ts_start"] is not None else 0))
    return steps



def _sandbox_step(r: dict) -> dict:
    """A PoC/command the reproducer ran in the gVisor sandbox (oracle_run)."""
    ts_start, ts_end = _ts(r.get("ts_start")), _ts(r.get("ts_end"))
    crashed = r.get("crashed")
    return {
        "step_type": "oracle_run",
        "output": f"exit {r.get('exit_code')}" + (f", crashed: {crashed}" if crashed is not None else ""),
        "oracle_ref": {
            "source": "mantis_sandbox",
            "tool": r.get("tool"),
            "command": r.get("command"),
            "exit_code": r.get("exit_code"),
            "crashed": crashed,
            "stdout": r.get("stdout_ref"),
            "stderr": r.get("stderr_ref"),
        },
        "latency_ms": round((ts_end - ts_start) * 1000) if ts_start and ts_end else None,
        "ts_start": ts_start, "ts_end": ts_end,
    }


# --- rendering ----------------------------------------------------------------

MAX_BLOCK_CHARS = 4000
STAGE_NAMES = {1: "agent's PoC crashes the unpatched build",
               2: "agent's PoC no longer crashes with the agent's patch",
               3: "project's test suite passes with the patch",
               4: "ground-truth PoC no longer crashes with the patch"}


def render(run: dict, steps: list[dict], bundle: Path | None = None, findings: list[dict] | None = None) -> str:
    """The run as Markdown, readable without tools. Long text is cut at
    MAX_BLOCK_CHARS; steps.jsonl keeps it whole."""
    outcome = run.get("outcome") or {}
    out = [f"# Trajectory: {run['run_id']}", ""]
    out += [f"- System: `{run.get('system')}`", f"- Task: `{run.get('task')}`",
            f"- Run kind: {run.get('kind')}", f"- Requested model: `{run.get('requested_model')}`",
            f"- Outcome: **{outcome.get('outcome')}**",
            f"- Model calls: {outcome.get('model_calls')}, refusals: {len(outcome.get('refusals') or [])}",
            f"- Cost (OpenRouter): ${outcome.get('cost_usd_openrouter')}",
            f"- Steps: {len(steps)}. Each model call shows only the input new since that agent's"
            " previous call; system prompts are in `prompts/<prompt_id>.json`.", ""]
    issued_in = {tc["id"]: s["seq"] for s in steps for tc in s["tool_calls"]}
    shown: set[str] = set()  # oracle files already printed in full
    for s in steps:
        out += _render_step(s, issued_in, bundle, shown)
    if findings is not None:
        out += verdicts.render_findings(findings)
    return "\n".join(out) + "\n"


def _render_step(s: dict, issued_in: dict, bundle: Path | None, shown: set[str]) -> list[str]:
    if s["step_type"] == "oracle_run":
        ref = s["oracle_ref"] or {}
        out = [f"## Step {s['seq']} · oracle_run · CyberGym stage {ref.get('stage')}"
               f" ({STAGE_NAMES.get(ref.get('stage'), '')}), attempt {ref.get('attempt')}", "",
               f"Result: **{s['output']}**", "",
               f"- PoC: {_link(ref.get('poc'))}", f"- Patch: {_link(ref.get('patch'))}", ""]
        if bundle is not None:
            poc, patch = ref.get("poc"), ref.get("patch")
            if poc and poc not in shown:
                shown.add(poc)
                data = (bundle / poc).read_bytes()
                out += [f"PoC bytes ({len(data)}):", "", _fence(repr(data)[:MAX_BLOCK_CHARS]), ""]
            if patch and patch not in shown:
                shown.add(patch)
                out += ["Patch:", "", _fence(_cut((bundle / patch).read_text(errors="replace")), "diff"), ""]
        return out

    who = s["agent_instance_id"]
    out = [f"## Step {s['seq']} · {s['step_type']} · {who}", "",
           f"_{_when(s['ts_start'])}, {s['latency_ms']} ms, model `{s['model']}`, "
           "tokens " + ", ".join(f"{k} {v}" for k, v in (s["tokens"] or {}).items())
           + f", cost ${s['cost_usd']}_", ""]
    if s["stage"] or s["stage_options"]:
        out += [f"Stage: {s['stage'] or 'coder chooses one of ' + ', '.join(s['stage_options'])}", ""]
    if s["claim"]:
        out += [f"**Claim (extracted by `{s['claim']['model']}`, {s['claim']['prompt']}):** "
                f"{s['claim']['text'] or 'none'}", ""]
    if s["parent_step_id"]:
        out += [f"Input new since {s['parent_step_id']} (earlier input hash `{s['input_prefix_hash']}`):", ""]
    else:
        out += ["Input (first call of this agent):", ""]
    for m in s["input_delta"]:
        out += _render_message(m, issued_in)
    if s["thinking_summary"]:
        out += ["**Thinking (model-written summary, not the model's reasoning):**", "",
                _quote(_cut(s["thinking_summary"]["text"])), ""]
    if s["output"]:
        out += ["**Output:**", "", _fence(_cut(s["output"])), ""]
    for tc in s["tool_calls"]:
        out += [f"**Tool call** `{tc['name']}` ({tc['id']}):", "",
                _fence(_cut(json.dumps(tc["args"], indent=2)), "json"), ""]
        if tc["result"] is None:
            out += ["Result: none recorded (no later call carried it).", ""]
        else:
            label = "Result (error):" if tc.get("is_error") else "Result:"
            out += [label, "", _fence(_cut(_as_text(tc["result"]))), ""]
    if s["step_type"] == "refusal":
        out += [f"**Refused by the model** (category: `{s['refusal_category']}`).", ""]
    if s["dropped_params"]:
        out += [f"Settings removed by the proxy: {', '.join(map(str, s['dropped_params']))}", ""]
    return out


def _render_message(m: dict, issued_in: dict) -> list[str]:
    out = []
    for block in m["content"]:
        kind = block.get("type")
        if kind == "tool_result":
            seq = issued_in.get(block.get("tool_use_id"))
            where = f"shown under Step {seq}" if seq else "from a call outside this log"
            out.append(f"- {m['role']}: tool result for `{block.get('tool_use_id')}` ({where})")
        elif kind == "tool_use":
            out.append(f"- {m['role']}: tool call `{block.get('name')}` ({block.get('id')})")
        elif kind == "thinking":
            out.append(f"- {m['role']}: thinking block (shown in the step that produced it)")
        elif kind == "text":
            out += [f"- {m['role']}:", "", _fence(_cut(block.get("text", ""))), ""]
        else:
            out.append(f"- {m['role']}: {kind} block")
    return out + [""]


def _as_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", json.dumps(b)) if isinstance(b, dict) else str(b) for b in content)
    return json.dumps(content)


def _cut(text: str) -> str:
    if len(text) <= MAX_BLOCK_CHARS:
        return text
    return text[:MAX_BLOCK_CHARS] + f"\n[... cut: {len(text)} characters in all; full text in steps.jsonl]"


def _fence(text: str, lang: str = "") -> str:
    ticks = "`" * max(3, _longest_backtick_run(text) + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def _longest_backtick_run(text: str) -> int:
    best = run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        best = max(best, run)
    return best


def _quote(text: str) -> str:
    return "\n".join("> " + line for line in text.splitlines())


def _link(path: str | None) -> str:
    return f"[{path}]({path})" if path else "none"


def _when(ts: float | None) -> str:
    if ts is None:
        return "time unknown"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

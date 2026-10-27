"""Claim extraction (T-3): after the run, an LLM pass names each step's main claim.

Systems are never changed for this (P3). The pass sends batches of model-call
steps (output and tool calls, cut to a fixed size) to Claude Haiku 5.5 through
the proxy, with a `tooling` proxy key, and asks for one claim per step as JSON.

Every request and its raw response are kept in `<run folder>/claims.jsonl`
(the recording). An export replays the recording; only a request the
recording does not hold goes to the live extractor, and only if one is given.
Changing the prompt, the model, or the steps changes the request, so the
extraction is redone rather than silently reused.

Each claim on a step: `{text, source: "extracted", model, prompt}` where
`model` is the model that answered and `prompt` the bundle file holding the
exact prompt. `text` is null when the model found no claim in the step.
Coders override a claim in their label file (coding.py), never here.
"""

import hashlib
import json
from pathlib import Path
from typing import Callable

import httpx

MODEL = "claude-haiku-5-5"  # the proxy's tooling route serves Claude Haiku 5.5 (ADR 0005)
PROMPT_VERSION = "claim-extraction-v1"
RECORDING = "claims.jsonl"
MAX_STEP_CHARS = 2000
MAX_REQUEST_CHARS = 60000
MAX_TOKENS = 8000

SYSTEM_PROMPT = """\
You read steps from the trajectory of a multi-agent system that looks for and \
patches vulnerabilities in C/C++ code. Each step is one model call by one agent: \
its text output and the tool calls it issued.

For each step, state the step's main claim: the most important assertion in that \
step's output that a later step could accept, check, or refute. Examples: \
"md_is_container_mark reads past the end of the buffer at md4c.c:5688", \
"the PoC crashes the fuzzer with a heap-buffer-overflow", "the patch fixes the \
overflow". A tool call asserts what it does or expects (writing a PoC file \
claims the file triggers the bug; running a build claims nothing).

Rules:
- One sentence per step, at most 30 words, in plain English, in the agent's terms.
- Keep file names, functions, line numbers, and values exactly as the step gives them.
- Report only what the step asserts. Do not judge whether it is true.
- If the step asserts nothing (it only reads, lists, or builds), the claim is null.

Answer with one JSON object and nothing else, mapping each step ID to its claim \
string or null, for example {"s0001": "...", "s0002": null}."""

Extractor = Callable[[dict], dict]  # Anthropic Messages request -> response JSON


def proxy_extractor(base_url: str, key: str, timeout: float = 300) -> Extractor:
    """A live extractor: POST the request to the proxy's Anthropic-format endpoint."""
    def call(request: dict) -> dict:
        r = httpx.post(f"{base_url.rstrip('/')}/v1/messages", json=request, timeout=timeout,
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        r.raise_for_status()
        return r.json()
    return call


def extract(steps: list[dict], run_dir: Path, bundle: Path, extractor: Extractor | None = None) -> dict:
    """Set `claim` on every model-call step that the recording (or the extractor) answers.

    Returns the bundle's record of the pass: prompt file, counts, and whether
    any step is still missing a claim.
    """
    prompt_file = _write_prompt(bundle)
    recording = Path(run_dir) / RECORDING
    recorded = _load(recording)
    extracted = missing = 0
    for batch, request in _requests(steps):
        key = _sha(request)
        entry = recorded.get(key)
        if entry is None and extractor is not None:
            entry = {"request_sha": key, "request": request, "response": extractor(request)}
            with recording.open("a") as f:
                f.write(json.dumps(entry) + "\n")
            recorded[key] = entry
        if entry is None:
            missing += len(batch)
            continue
        response = entry["response"]
        answers = _parse(response)
        for s in batch:
            s["claim"] = {"text": answers.get(s["step_id"]), "source": "extracted",
                          "model": response.get("model"), "prompt": prompt_file}
            extracted += 1
    return {"prompt": prompt_file, "requested_model": MODEL, "recording": RECORDING,
            "extracted_steps": extracted, "missing_steps": missing}


def claimable(step: dict) -> bool:
    return step["step_type"] == "llm_step"


def _requests(steps: list[dict]):
    """Batches of claimable steps, each with its Messages API request."""
    batch, parts, size = [], [], 0
    for s in filter(claimable, steps):
        part = _describe(s)
        if batch and size + len(part) > MAX_REQUEST_CHARS:
            yield batch, _request(parts)
            batch, parts, size = [], [], 0
        batch.append(s)
        parts.append(part)
        size += len(part)
    if batch:
        yield batch, _request(parts)


def _describe(step: dict) -> str:
    body = []
    if step.get("output"):
        body.append("Output:\n" + step["output"])
    for tc in step.get("tool_calls") or []:
        body.append(f"Tool call {tc['name']}: " + json.dumps(tc["args"], ensure_ascii=False))
    text = "\n".join(body) or "(no output and no tool calls)"
    if len(text) > MAX_STEP_CHARS:
        text = text[:MAX_STEP_CHARS] + " [...cut]"
    who = step.get("role") or step.get("system") or "agent"
    return f"[{step['step_id']}] agent {who}\n{text}\n"


def _request(parts: list[str]) -> dict:
    return {"model": MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": "Steps:\n\n" + "\n".join(parts)}]}


def _parse(response: dict) -> dict:
    """The JSON object in the model's text, tolerating a code fence around it."""
    text = "".join(b.get("text", "") for b in response.get("content") or [] if b.get("type") == "text")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        answers = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return {k: v for k, v in answers.items() if v is None or isinstance(v, str)}


def _write_prompt(bundle: Path) -> str:
    rel = f"prompts/{PROMPT_VERSION}-{_sha(SYSTEM_PROMPT)[:12]}.json"
    path = Path(bundle) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": PROMPT_VERSION, "requested_model": MODEL, "max_tokens": MAX_TOKENS,
        "max_step_chars": MAX_STEP_CHARS, "max_request_chars": MAX_REQUEST_CHARS,
        "system": SYSTEM_PROMPT,
        "user_template": "Steps:\n\n[<step_id>] agent <role>\nOutput:\n<output>\nTool call <name>: <args JSON>\n...",
    }, indent=2))
    return rel


def _load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    entries = (json.loads(l) for l in path.read_text().splitlines() if l.strip())
    return {e["request_sha"]: e for e in entries}


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()

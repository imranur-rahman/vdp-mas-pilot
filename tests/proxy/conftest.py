"""Seam 1: the real proxy stack with a fake upstream in place of OpenRouter.

Starts a throwaway compose project (`pilot-test`) whose LiteLLM points at a
fake Anthropic-format server on the host. No model spend.
"""

import json
import os
import secrets
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
# Unique per session so that parallel worktrees can run seam-1 tests at once.
PROJECT = f"pilot-test-{secrets.token_hex(3)}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PROXY_PORT = _free_port()
PHOENIX_PORT = _free_port()
REFUSE_MARKER = "PLEASE-REFUSE"  # a request containing this gets a `cyber` refusal
REFUSAL_DETAILS = {"type": "refusal", "category": "cyber",
                   "explanation": "fake: could enable cyber harm"}


class FakeUpstream:
    """Anthropic Messages API stand-in that records every request body."""

    def __init__(self):
        self.requests: list[dict] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                upstream.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                refuse = REFUSE_MARKER in json.dumps(body.get("messages"))
                if body.get("stream"):
                    return self._stream(body, refuse)
                content = [{"type": "thinking", "thinking": "fake summary", "signature": "sig"},
                           {"type": "text", "text": "ok"}]
                reply = json.dumps({
                    "id": "msg_fake", "type": "message", "role": "assistant", "model": body.get("model"),
                    "stop_reason": "refusal" if refuse else "end_turn",
                    "stop_details": REFUSAL_DETAILS if refuse else None,
                    "content": [] if refuse else content,
                    "usage": {"input_tokens": 10, "output_tokens": 5,
                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def _stream(self, body, refuse=False):
                usage = {"input_tokens": 10, "output_tokens": 5,
                         "cache_read_input_tokens": 3, "cache_creation_input_tokens": 0}
                events = [
                    ("message_start", {"type": "message_start", "message": {
                        "id": "msg_fake", "type": "message", "role": "assistant", "model": body.get("model"),
                        "content": [], "stop_reason": None, "usage": usage}}),
                    ("content_block_start", {"type": "content_block_start", "index": 0,
                                             "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "thinking_delta", "thinking": "fake streamed summary"}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                             "delta": {"type": "signature_delta", "signature": "sig"}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("content_block_start", {"type": "content_block_start", "index": 1,
                                             "content_block": {"type": "text", "text": ""}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                             "delta": {"type": "text_delta", "text": "ok"}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
                    ("message_delta", {"type": "message_delta", "delta": (
                        {"stop_reason": "refusal", "stop_details": REFUSAL_DETAILS} if refuse
                        else {"stop_reason": "end_turn"}), "usage": {"output_tokens": 5}}),
                    ("message_stop", {"type": "message_stop"}),
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for name, data in events:
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                    self.wfile.flush()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def last(self) -> dict:
        assert self.requests, "upstream was not called"
        return self.requests[-1]["body"]


def compose(env: dict, *args: str):
    r = subprocess.run(
        ["sg", "docker", "-c", " ".join(["docker", "compose", "-p", PROJECT, *args])],
        cwd=REPO / "infra", env=env, capture_output=True, text=True,
    )
    if r.returncode:
        raise RuntimeError(f"docker compose {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout


@pytest.fixture(scope="session")
def upstream():
    fake = FakeUpstream()
    yield fake
    fake.server.shutdown()


@pytest.fixture(scope="session")
def proxy(upstream, tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    runs.chmod(0o777)
    master = "sk-test-" + secrets.token_hex(8)
    env = {
        **os.environ,
        "OPENROUTER_API_KEY": "sk-or-fake",
        "LITELLM_MASTER_KEY": master,
        "POSTGRES_PASSWORD": secrets.token_hex(8),
        "PILOT_UPSTREAM_BASE": f"http://host.docker.internal:{upstream.port}",
        "PILOT_RUNS_DIR": str(runs),
        "PILOT_PROXY_PORT": str(PROXY_PORT),
        "PILOT_PHOENIX_PORT": str(PHOENIX_PORT),
        "PILOT_GATEWAY_IP": "127.0.0.3",  # forwarder binds PROXY_PORT here; unique per session
    }
    compose(env, "up", "-d", "--wait")
    base = f"http://127.0.0.1:{PROXY_PORT}"
    deadline = time.time() + 180
    while True:
        try:
            if httpx.get(f"{base}/health/liveliness", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.time() > deadline:
            compose(env, "logs", "litellm")
            raise RuntimeError("proxy did not become healthy")
        time.sleep(2)
    yield {"base": base, "master": master, "runs": runs}
    compose(env, "down", "-v")


@pytest.fixture
def make_key(proxy):
    def _make(max_budget: float = 1, **metadata) -> str:
        r = httpx.post(f"{proxy['base']}/key/generate",
                       headers={"Authorization": f"Bearer {proxy['master']}"},
                       json={"metadata": metadata, "max_budget": max_budget}, timeout=30)
        r.raise_for_status()
        return r.json()["key"]
    return _make


def wait_for_log(path: Path, n: int = 1, timeout: float = 20) -> list[dict]:
    """Logging hooks run after the response; poll until n records exist."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
            if len(lines) >= n:
                return lines
        time.sleep(0.5)
    raise AssertionError(f"expected {n} log record(s) in {path}")

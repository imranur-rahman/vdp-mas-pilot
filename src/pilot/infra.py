"""Shared infrastructure: CyberGym's firewall and the pilot's proxy stack."""

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
CYBERGYM = REPO / "vendor" / "cybergym-e2e"
CYBERGYM_SCRIPTS = CYBERGYM / "scripts"
RUNS = REPO / "runs"
PROXY_PORT = 4000
# P1: the agent's only exit is the host gateway (LiteLLM). Squid needs at least
# one entry, so the allowlist holds a name that can never resolve.
EMPTY_ALLOWLIST = REPO / "infra" / "firewall" / "allowlist.txt"


def load_env() -> dict[str, str]:
    env = {}
    for line in (REPO / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _import_cybergym():
    if str(CYBERGYM_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(CYBERGYM_SCRIPTS))


def ensure_firewall() -> str:
    """Start CyberGym's run proxy with the empty allowlist if needed; return the host gateway IP."""
    _import_cybergym()
    from firewall import FirewallProxyManager

    mgr = FirewallProxyManager()
    try:
        mgr.connect()
    except Exception:
        subprocess.run([sys.executable, "-m", "firewall", "start", "--which", "run",
                        "--allowlist", str(EMPTY_ALLOWLIST)], cwd=CYBERGYM_SCRIPTS, check=True)
        mgr = FirewallProxyManager()
        mgr.connect()
    return mgr.host_gateway


def ensure_proxy(gateway_ip: str) -> str:
    """Bring up LiteLLM + Postgres + Phoenix, publishing LiteLLM on the gateway IP."""
    env = {**os.environ, **load_env(), "PILOT_GATEWAY_IP": gateway_ip, "PILOT_RUNS_DIR": str(RUNS)}
    subprocess.run(["docker", "compose", "-p", "pilot", "up", "-d", "--wait"],
                   cwd=REPO / "infra", env=env, check=True)
    base = f"http://127.0.0.1:{PROXY_PORT}"
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/health/liveliness", timeout=2).status_code == 200:
                return base
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError("LiteLLM proxy did not become healthy")


def mint_key(*, run_id: str, kind: str, system: str, budget_usd: float, role: str | None = None) -> str:
    """A per-run virtual key; its metadata is how the proxy labels and routes calls."""
    env = load_env()
    meta = {"pilot_run_id": run_id, "pilot_run_kind": kind, "pilot_system": system}
    if role:
        meta["pilot_role"] = role
    r = httpx.post(f"http://127.0.0.1:{PROXY_PORT}/key/generate", timeout=60,
                   headers={"Authorization": f"Bearer {env['LITELLM_MASTER_KEY']}"},
                   json={"key_alias": f"{run_id}:{role or system}", "max_budget": budget_usd, "metadata": meta})
    r.raise_for_status()
    return r.json()["key"]


def key_spend(key: str) -> dict:
    env = load_env()
    r = httpx.get(f"http://127.0.0.1:{PROXY_PORT}/key/info", params={"key": key}, timeout=60,
                  headers={"Authorization": f"Bearer {env['LITELLM_MASTER_KEY']}"})
    r.raise_for_status()
    info = r.json().get("info", {})
    return {"spend": info.get("spend"), "max_budget": info.get("max_budget")}

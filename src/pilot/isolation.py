"""Run isolation (P1, P7): runner containers, the per-run container-event log,
the isolation verdict, and the registry allowlist for image builds.

- A host-side system's own process runs in a **runner container** on
  `cybergym-internal`. That network has no route out; its only exits are
  Squid (CyberGym's run proxy, empty allowlist) and the host gateway, where
  only the LiteLLM forwarder listens.
- Every container and network event during a run is appended to
  `container_events.jsonl` in the run folder, with each network's `Internal`
  flag looked up when the event arrives (networks may be gone by the end).
- The **isolation verdict** is `invalid` if, once the system has started, any
  container is attached to an internet-connected network. Setup before the
  system starts (CyberGym's own install phase on `bridge`) does not count,
  but a container still attached to such a network when the system starts
  does. Containers are attributed to the run by time window: any container
  that connects to a network while the log runs and is not pilot
  infrastructure counts, so concurrent Docker work on the host makes a run
  invalid, never valid (fail closed). Containers already running when the log
  starts were not started by the run and are not checked.
- **Registry builds** (OpenAnt, ticket 09): `build_image` runs `docker build`
  with the legacy builder on the internal network `pilot-build`, whose only
  exit is a Squid allowlisting package registries. Every request is copied
  from Squid's access log into the run folder.
"""

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import docker
from docker.errors import NotFound

from . import infra

RUN_NETWORK = "cybergym-internal"
BUILD_NETWORK = "pilot-build"
BUILD_PROXY = "pilot-build-proxy"
REGISTRY_ALLOWLIST = infra.REPO / "infra" / "firewall" / "registry_allowlist.txt"
# Internal networks whose proxy forwards anywhere: internet-connected in effect.
ALLOW_ALL_NETWORKS = {"cybergym-install"}
# Pilot infrastructure, started outside any run; never attributed to a run.
INFRA_CONTAINERS = {"cybergym-proxy", "cybergym-install-proxy", BUILD_PROXY}
INFRA_COMPOSE_PROJECT = re.compile(r"^pilot(-test.*|-t\d+)?$")
LOGGED_ACTIONS = {
    "container": {"create", "start", "die", "destroy", "kill", "stop", "rename", "update"},
    "network": {"connect", "disconnect", "create", "destroy"},
}
SYSTEM_STARTED = "system_started"


# --- network classification -------------------------------------------------

def classify_network(name: str, driver: str | None, internal: bool | None) -> str:
    """`none`, `internal`, `registry_build` (the logged exception), or `internet`.

    A network that could not be inspected counts as `internet` (fail closed).
    """
    if driver == "null" or name == "none":
        return "none"
    if driver == "host" or name == "host":
        return "internet"
    if internal is None:
        return "internet"
    if name == BUILD_NETWORK and internal:
        return "registry_build"
    if not internal or name in ALLOW_ALL_NETWORKS:
        return "internet"
    return "internal"


# --- container-event log ----------------------------------------------------

class ContainerEventLog:
    """Logs Docker container and network events to `<run_dir>/container_events.jsonl`.

    Use as a context manager around the run; call `mark(SYSTEM_STARTED)` when
    the system itself starts. `verdict()` reads the log back.
    """

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / "container_events.jsonl"
        self._client = docker.from_env()
        self._networks: dict[str, dict] = {}
        self._labels: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stream = None
        self._thread = None
        self._ready = threading.Event()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        self._write({"pilot_marker": "log_started", "time": now})
        self._stream = self._client.events(decode=True, since=int(now),
                                           filters={"type": ["container", "network"]})
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def mark(self, marker: str):
        self._write({"pilot_marker": marker, "time": time.time()})

    def stop(self):
        time.sleep(1)  # let in-flight events arrive
        self._write({"pilot_marker": "log_stopped", "time": time.time()})
        if self._stream is not None:
            self._stream.close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def verdict(self) -> dict:
        return isolation_verdict(read_events(self.path.parent))

    def _pump(self):
        try:
            for ev in self._stream:
                self._handle(ev)
        except Exception as e:  # stream closed on stop(), or daemon restarted
            if "closed" not in str(e).lower():
                self._write({"pilot_marker": "log_error", "time": time.time(), "error": repr(e)})

    def _handle(self, ev: dict):
        typ, action = ev.get("Type"), (ev.get("Action") or "").split(":")[0]
        if action not in LOGGED_ACTIONS.get(typ, ()):
            return
        actor = ev.get("Actor") or {}
        attrs = actor.get("Attributes") or {}
        rec = {"time": ev.get("timeNano", 0) / 1e9 or ev.get("time"), "type": typ, "action": action,
               "id": actor.get("ID"), "attributes": attrs}
        if typ == "container":
            labels = {k: v for k, v in attrs.items() if k not in ("name", "image", "exitCode", "signal")}
            self._labels[actor.get("ID")] = labels
            rec.update(container_id=actor.get("ID"), container_name=attrs.get("name"), labels=labels)
        elif action in ("connect", "disconnect"):
            cid = attrs.get("container")
            rec.update(container_id=cid, **self._network_info(actor.get("ID"), attrs.get("name")))
            info = self._container_info(cid)
            rec.update(container_name=info.get("name"), labels=info.get("labels"))
        self._write(rec)

    def _network_info(self, net_id: str | None, name: str | None) -> dict:
        key = net_id or name
        if key not in self._networks:
            try:
                n = self._client.networks.get(key)
                self._networks[key] = {"network_id": n.id, "network": n.name,
                                       "driver": n.attrs.get("Driver"), "internal": n.attrs.get("Internal")}
            except (NotFound, Exception):
                self._networks[key] = {"network_id": net_id, "network": name, "driver": None, "internal": None}
        info = self._networks[key]
        return {**info, "network_class": classify_network(info["network"], info["driver"], info["internal"])}

    def _container_info(self, cid: str | None) -> dict:
        try:
            c = self._client.containers.get(cid)
            return {"name": c.name, "labels": c.labels}
        except Exception:
            return {"name": None, "labels": self._labels.get(cid)}

    def _write(self, rec: dict):
        with self._lock, self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")


def read_events(run_dir: Path) -> list[dict]:
    p = Path(run_dir) / "container_events.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _is_infra(rec: dict) -> bool:
    labels = rec.get("labels") or {}
    project = labels.get("com.docker.compose.project", "")
    return rec.get("container_name") in INFRA_CONTAINERS or bool(INFRA_COMPOSE_PROJECT.match(project))


def isolation_verdict(records: list[dict]) -> dict:
    """`valid` or `invalid` from a run's container-event log (P7)."""
    started = next((r["time"] for r in records if r.get("pilot_marker") == SYSTEM_STARTED), None)
    if started is None:
        return {"verdict": "invalid", "reason": "no system_started marker in container_events.jsonl",
                "violations": [], "registry_builds": [], "containers": []}
    if any(r.get("pilot_marker") == "log_error" for r in records):
        return {"verdict": "invalid", "reason": "container-event log was interrupted",
                "violations": [], "registry_builds": [], "containers": []}

    attached: dict[tuple, dict] = {}  # (container, network) -> record, as of `started`
    violations, registry, seen = [], [], {}
    for r in sorted((r for r in records if "network_class" in r), key=lambda r: r["time"]):
        if _is_infra(r):
            continue
        key = (r.get("container_id"), r.get("network"))
        is_attach = r.get("action") == "connect"
        if r["time"] < started:
            if is_attach:
                attached[key] = r
            elif r.get("action") == "disconnect":
                attached.pop(key, None)
            continue
        if is_attach:
            seen[r.get("container_id")] = r.get("container_name")
            if r["network_class"] == "internet":
                violations.append(_violation(r, "joined after system start"))
            elif r["network_class"] == "registry_build":
                registry.append(_violation(r, "image build on the registry network"))
    for r in attached.values():
        if r["network_class"] == "internet":
            violations.append(_violation(r, "still attached when the system started"))
    return {
        "verdict": "invalid" if violations else "valid",
        "reason": "a container reached an internet-connected network" if violations else None,
        "violations": violations,
        "registry_builds": registry,
        "containers": sorted({n or cid[:12] for cid, n in seen.items() if cid}),
        "attributed_by": "time window after system start; pilot infrastructure excluded",
    }


def _violation(r: dict, why: str) -> dict:
    return {"container": r.get("container_name"), "container_id": (r.get("container_id") or "")[:12],
            "network": r.get("network"), "network_class": r.get("network_class"),
            "time": r.get("time"), "why": why}


# --- runner containers ------------------------------------------------------

def runner_proxy_env(gateway_ip: str) -> dict[str, str]:
    """Squid for anything that honors a proxy; the host gateway (LiteLLM) bypasses it."""
    infra._import_cybergym()
    from firewall import FirewallProxyManager

    mgr = FirewallProxyManager()
    mgr.connect()
    env = mgr.env_vars()
    assert gateway_ip in env["NO_PROXY"].split(","), env
    return env


def start_runner_container(*, run_id: str, system: str, image: str, command: list[str] | str,
                           gateway_ip: str, env: dict[str, str] | None = None,
                           volumes: dict | None = None, workdir: str | None = None,
                           user: str | None = None):
    """Start a system's own process in a runner container on `cybergym-internal`."""
    client = docker.from_env()
    return client.containers.run(
        image, command, detach=True, network=RUN_NETWORK, working_dir=workdir, user=user,
        name=f"pilot-runner-{run_id}"[:63],
        environment={**runner_proxy_env(gateway_ip), **(env or {})},
        volumes=volumes or {},
        labels={"pilot.run_id": run_id, "pilot.system": system, "pilot.role": "runner"},
    )


PROBES = {  # name -> (shell command, exit code 0 means reachable)
    "litellm_via_gateway": "curl -sS -m 10 -o /dev/null -w '%{http_code}' http://$PILOT_GATEWAY:$PILOT_PROXY_PORT/health/liveliness",
    "openrouter_via_squid": "curl -sS -m 10 -o /dev/null -w '%{http_code}' https://openrouter.ai/api/v1/models",
    "openrouter_direct": "curl -sS -m 10 --noproxy '*' -o /dev/null -w '%{http_code}' https://openrouter.ai/api/v1/models",
    "ip_direct": "curl -sS -m 5 --noproxy '*' -o /dev/null -w '%{http_code}' http://1.1.1.1/",
}


def probe_runner_network(gateway_ip: str, image: str) -> dict:
    """What a runner container can reach. Expected: only LiteLLM (P1)."""
    client = docker.from_env()
    results = {}
    for name, cmd in PROBES.items():
        out = client.containers.run(
            image, ["bash", "-c", f"{cmd}; echo \" exit=$?\""], remove=True, network=RUN_NETWORK,
            environment={**runner_proxy_env(gateway_ip), "PILOT_GATEWAY": gateway_ip,
                         "PILOT_PROXY_PORT": str(infra.PROXY_PORT)},
            stderr=True).decode()
        results[name] = {"reachable": out.strip().endswith("exit=0") and not out.startswith("000"),
                         "output": out.strip()}
    return results


# --- registry allowlist for image builds ------------------------------------

def ensure_build_proxy():
    """Squid on the internal `pilot-build` network, allowing only package registries."""
    infra._import_cybergym()
    from firewall import FirewallProxyManager

    mgr = FirewallProxyManager(allowlist_path=REGISTRY_ALLOWLIST, container_name=BUILD_PROXY,
                               network_name=BUILD_NETWORK)
    mgr.start()
    return mgr


def _squid_lines_since(container: str, since: float) -> list[str]:
    r = subprocess.run(["docker", "exec", container, "cat", "/var/log/squid/access.log"],
                       capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        if " 127.0.0.1 " in line:  # Squid readiness probes from inside its own container
            continue
        try:
            if float(line.split()[0]) >= since:
                out.append(line)
        except (ValueError, IndexError):
            continue
    return out


def build_image(context: Path, tag: str, log_dir: Path, dockerfile: str | None = None,
                build_args: dict[str, str] | None = None) -> dict:
    """`docker build` whose RUN steps reach only allowlisted registries, with every request logged.

    Writes `registry_access.log` (Squid's lines for this build) and appends a
    summary to `image_builds.jsonl` in `log_dir` (normally the run folder).
    Base images are pulled by the Docker daemon itself and show up as image
    pulls, not in Squid's log.
    """
    mgr = ensure_build_proxy()
    proxy_env = mgr.env_vars()
    args = {k: v for k, v in proxy_env.items() if k.lower() != "no_proxy"} | (build_args or {})
    cmd = ["docker", "build", "--network", BUILD_NETWORK, "-t", tag]
    if dockerfile:
        cmd += ["-f", dockerfile]
    for k, v in args.items():
        cmd += ["--build-arg", f"{k}={v}"]
    cmd.append(str(context))
    started = time.time() - 1
    r = subprocess.run(cmd, env={**os.environ, "DOCKER_BUILDKIT": "0"}, capture_output=True, text=True)
    time.sleep(1)
    lines = _squid_lines_since(BUILD_PROXY, started)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "registry_access.log").open("a") as f:
        f.writelines(l + "\n" for l in lines)
    denied = [l for l in lines if "DENIED" in l]
    summary = {
        "tag": tag, "context": str(context), "started_at": started, "finished_at": time.time(),
        "returncode": r.returncode, "network": BUILD_NETWORK, "allowlist": str(REGISTRY_ALLOWLIST),
        "requests": len(lines), "denied": len(denied),
        "hosts": sorted({_host(l) for l in lines} - {None}),
        "output_tail": (r.stdout + r.stderr)[-2000:],
    }
    with (log_dir / "image_builds.jsonl").open("a") as f:
        f.write(json.dumps(summary) + "\n")
    return summary


def _host(squid_line: str) -> str | None:
    parts = squid_line.split()
    if len(parts) < 7:
        return None
    url = parts[6]
    return url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]

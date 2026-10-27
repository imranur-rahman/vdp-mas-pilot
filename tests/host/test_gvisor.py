"""Host seam: Docker runs sandboxes under gVisor (ticket 04).

Talks to the real Docker daemon. No model spend. Run from a shell in the
`docker` group: `sg docker -c 'uv run pytest tests/host'`.
"""

import json
import subprocess
import time
import uuid

import pytest

PINNED_RUNSC = "20261019"


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          check=check, timeout=180)


@pytest.fixture(scope="module", autouse=True)
def require_docker():
    if docker("info", check=False).returncode != 0:
        pytest.skip("Docker daemon not reachable (try `sg docker -c ...`)")


def test_runsc_is_registered_as_a_docker_runtime():
    runtimes = json.loads(docker("info", "--format", "{{json .Runtimes}}").stdout)
    assert "runsc" in runtimes


def test_runsc_is_the_pinned_release():
    out = subprocess.run(["runsc", "--version"], capture_output=True, text=True).stdout
    assert f"runsc version release-{PINNED_RUNSC}" in out


def test_networkless_gvisor_container_runs_and_shows_in_event_log():
    name = f"pilot-gvisor-check-{uuid.uuid4().hex[:8]}"
    since = int(time.time()) - 1
    run = docker("run", "--rm", "--name", name, "--runtime=runsc", "--network=none",
                 "busybox:1.37", "dmesg", check=False)
    assert run.returncode == 0, run.stderr
    # gVisor's Sentry prints its own boot log; under runc dmesg would be the host's (or denied).
    assert "gVisor" in run.stdout
    events = docker("events", "--since", str(since), "--until", str(int(time.time()) + 1),
                    "--filter", f"container={name}", "--format", "{{.Action}}").stdout.split()
    assert "start" in events and "die" in events

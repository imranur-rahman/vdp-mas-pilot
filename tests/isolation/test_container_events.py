"""Container-event log and isolation verdict (P7), against the real Docker daemon.

No model spend. Needs Docker socket access from the test process itself:
until your login session has the docker group, run
`sg docker -c "uv run pytest tests/isolation"`.
"""

import os
import secrets

import pytest

docker = pytest.importorskip("docker")

try:
    docker.from_env().ping()
except Exception as e:  # noqa: BLE001
    pytest.skip(f"no Docker socket access ({e}); run under `sg docker -c`", allow_module_level=True)

from pilot.isolation import RUN_NETWORK, SYSTEM_STARTED, ContainerEventLog  # noqa: E402

IMAGE = "alpine:3"


@pytest.fixture
def client():
    c = docker.from_env()
    c.images.pull(IMAGE)
    return c


def _run(client, name, **kw):
    client.containers.run(IMAGE, "true", name=name, remove=True, **kw)


def _mine(verdict, prefix):
    return [v for v in verdict["violations"] if (v["container"] or "").startswith(prefix)]


def test_containers_on_no_network_or_the_internal_network_keep_the_run_valid(tmp_path, client):
    tag = f"t03-ok-{secrets.token_hex(3)}"
    with ContainerEventLog(tmp_path) as log:
        log.mark(SYSTEM_STARTED)
        _run(client, f"{tag}-none", network_mode="none")
        _run(client, f"{tag}-internal", network=RUN_NETWORK)
    v = log.verdict()
    assert _mine(v, tag) == []
    assert {f"{tag}-none", f"{tag}-internal"} <= set(v["containers"])


def test_a_container_joining_an_internet_network_after_system_start_makes_the_run_invalid(tmp_path, client):
    tag = f"t03-bad-{secrets.token_hex(3)}"
    with ContainerEventLog(tmp_path) as log:
        log.mark(SYSTEM_STARTED)
        _run(client, f"{tag}-bridge")  # Docker's default bridge reaches the internet
    v = log.verdict()
    assert v["verdict"] == "invalid"
    [hit] = _mine(v, tag)
    assert hit["network"] == "bridge" and hit["why"] == "joined after system start"
    lines = (tmp_path / "container_events.jsonl").read_text()
    assert f"{tag}-bridge" in lines


def test_setup_on_bridge_is_allowed_only_if_it_ends_before_the_system_starts(tmp_path, client):
    tag = f"t03-setup-{secrets.token_hex(3)}"
    net = client.networks.get("bridge")
    done = client.containers.run(IMAGE, "sleep 60", name=f"{tag}-done", detach=True)
    still = client.containers.run(IMAGE, "sleep 60", name=f"{tag}-still", detach=True)
    try:
        with ContainerEventLog(tmp_path) as log:
            # Like CyberGym's install phase: start on bridge, then move to the run network.
            net.disconnect(done)
            client.networks.get(RUN_NETWORK).connect(done)
            net.disconnect(still)
            net.connect(still)  # reconnected during the log, left attached
            log.mark(SYSTEM_STARTED)
        v = log.verdict()
        hits = _mine(v, tag)
        assert [h["container"] for h in hits] == [f"{tag}-still"]
        assert hits[0]["why"] == "still attached when the system started"
    finally:
        for c in (done, still):
            c.remove(force=True)


def test_a_log_without_a_system_start_is_invalid(tmp_path, client):
    with ContainerEventLog(tmp_path) as log:
        pass
    assert log.verdict()["verdict"] == "invalid"

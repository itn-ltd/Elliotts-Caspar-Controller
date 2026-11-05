"""Start All / Stop All / restart through the web API, with fake CasparCG processes."""

import subprocess
import time

import pytest
from conftest import amcp_log, free_port, instance, windows_only

from caspar_controller import instances, log
from caspar_controller.caspar import config_xml
from caspar_controller.caspar.amcp import AMCPClient
from caspar_controller.caspar.process import CasparProcessManager

pytestmark = windows_only


@pytest.fixture
def three_instances(caspar_dir, write_config):
    insts = [
        instance(1, "GFX1", url="https://example.com/gfx1", amcp_port=free_port()),
        instance(2, "GFX2", amcp_port=free_port()),  # HTML with no URL
        instance(
            3, "MEDIA1", "media", startup_command='PLAY 1-1 "AMB" LOOP', amcp_port=free_port()
        ),
    ]
    return write_config(
        {
            "caspar_exe_path": str(caspar_dir / "casparcg.cmd"),
            "video_mode": "1080p2500",
            "instances": insts,
        }
    )


@pytest.fixture
def start_order(monkeypatch):
    order = []
    original = CasparProcessManager.start

    def recording_start(self):
        order.append(self.amcp_port)
        return original(self)

    monkeypatch.setattr(CasparProcessManager, "start", recording_start)
    return order


@pytest.fixture
def notified():
    messages = []
    instances.manager.notify = messages.append
    return messages


@pytest.fixture(autouse=True)
def stop_everything():
    yield
    instances.manager.wait()
    for m in list(instances.manager._procs.values()):
        m.stop()


def ports_of(cfg: dict) -> list:
    return [i["amcp_port"] for i in cfg["instances"]]


def start(client) -> None:
    assert client.post("/api/server/start").status_code == 202
    instances.manager.wait()


def test_start_all(
    client, three_instances, caspar_dir, recorded_sleeps, start_order, refused_kills
):
    ports = ports_of(three_instances)

    t0 = time.perf_counter()
    r = client.post("/api/server/start")
    took = time.perf_counter() - t0
    instances.manager.wait()

    # A job: answers at once, and progress goes to the event log.
    assert (r.status_code, r.json()) == (202, {"ok": True, "message": "Starting instances..."})
    assert took < 0.2
    assert start_order == ports  # config order
    assert recorded_sleeps == [5, 5]  # 5 s gap between instances, none after the last
    assert refused_kills == []
    # Configs and test patterns are written next to the exe.
    for n in (1, 2, 3):
        assert (caspar_dir / f"casparcg_inst_{n}.config").exists()
    assert (caspar_dir / "media" / "TEST_BARS.png").exists()
    # Each instance answered AMCP, then got its startup command.
    assert amcp_log(caspar_dir, ports[0])[-1] == 'PLAY 1-1 [HTML] "https://example.com/gfx1"'
    assert amcp_log(caspar_dir, ports[1])[-1] == "CLEAR 1"
    assert amcp_log(caspar_dir, ports[2])[-1] == 'PLAY 1-1 "AMB" LOOP'
    assert all("VERSION" in amcp_log(caspar_dir, p) for p in ports)
    assert sorted(instances.manager._procs) == [1, 2, 3]
    assert log._log[-1].endswith("Started 3/3 instances")


def test_start_all_twice_starts_each_instance_once(
    client, three_instances, recorded_sleeps, start_order
):
    client.post("/api/server/start")
    start(client)
    assert start_order == ports_of(three_instances)
    assert log._log[-1].endswith("All instances already running.")


def test_stop_all(client, three_instances, recorded_sleeps):
    start(client)

    r = client.post("/api/server/stop")
    instances.manager.wait()

    assert (r.status_code, r.json()["message"]) == (202, "Stopping all instances...")
    assert instances.manager._procs == {}
    assert not any(AMCPClient(port=p, timeout=0.5).ping() for p in ports_of(three_instances))
    assert log._log[-1].endswith("All CasparCG instances stopped.")


def test_unrelated_caspar_survives_start_and_stop(
    client, three_instances, caspar_dir, recorded_sleeps
):
    # A CasparCG this app didn't start, on a port it doesn't use.
    stranger = instance(9, "OTHER", amcp_port=free_port())
    config_xml.regenerate_instance_config(three_instances, stranger)
    proc = subprocess.Popen(
        [str(caspar_dir / "casparcg.cmd"), "casparcg_inst_9.config"], cwd=caspar_dir
    )
    probe = AMCPClient(port=stranger["amcp_port"])
    for _ in range(50):
        if probe.ping():
            break
        time.sleep(0.2)

    start(client)
    client.post("/api/server/stop")
    instances.manager.wait()

    assert probe.ping() is True
    assert proc.poll() is None


def test_start_with_no_instances_is_400(client, write_config):
    write_config({"caspar_exe_path": "casparcg.exe", "instances": []})
    r = client.post("/api/server/start")
    assert r.status_code == 400
    assert r.json()["detail"] == "No instances configured"


def test_partial_failure_is_logged_and_notified(
    client, three_instances, recorded_sleeps, notified, monkeypatch
):
    failing = three_instances["instances"][1]["amcp_port"]
    original = CasparProcessManager.start
    monkeypatch.setattr(
        CasparProcessManager,
        "start",
        lambda self: False if self.amcp_port == failing else original(self),
    )

    start(client)

    assert notified == ["Started 2/3 instances (failed: [2])"]
    assert any("Inst 2 (GFX2) FAILED to start" in line for line in log._log)
    assert 2 not in instances.manager._procs
    # The gap is skipped after a failed instance.
    assert recorded_sleeps == [5]


def test_launch_error_reaches_the_operator(client, three_instances, notified, monkeypatch):
    def cannot_launch(self):
        raise OSError("casparcg.exe is missing")

    monkeypatch.setattr(CasparProcessManager, "start", cannot_launch)
    start(client)
    assert any("Inst 1 (GFX1) FAILED to start: casparcg.exe is missing" in x for x in log._log)
    assert notified == ["Started 0/3 instances (failed: [1, 2, 3])"]


def test_job_that_raises_reaches_the_operator(client, three_instances, notified, monkeypatch):
    def broken(cfg):
        raise RuntimeError("disk full")

    monkeypatch.setattr(instances, "ensure_test_pattern_images", broken)
    start(client)
    assert notified == ["Start All failed: disk full"]
    assert log._log[-1].endswith("Start All failed: disk full")


def test_restart_one_instance(client, three_instances, caspar_dir, recorded_sleeps):
    start(client)
    port = three_instances["instances"][2]["amcp_port"]
    old_pid = instances.manager._procs[3].pid

    r = client.post("/api/instance/3/restart")
    instances.manager.wait()

    assert (r.status_code, r.json()["message"]) == (202, "Restarting MEDIA1...")
    assert instances.manager._procs[3].pid != old_pid
    commands = amcp_log(caspar_dir, port)  # appended to by both the old and new process
    assert commands[-1] == 'PLAY 1-1 "AMB" LOOP'
    assert commands.count('PLAY 1-1 "AMB" LOOP') == 2
    assert "BYE" in commands
    assert AMCPClient(port=port).ping() is True
    assert any("Inst 3 (MEDIA1) restarted" in line for line in log._log)


def test_restart_unmanaged_instance_starts_it(three_instances, refused_kills):
    port = three_instances["instances"][0]["amcp_port"]
    instances.manager.restart(1)
    instances.manager.wait()
    assert refused_kills == []
    assert AMCPClient(port=port).ping() is True


def test_restart_endpoint_404(client, three_instances):
    assert client.post("/api/instance/99/restart").status_code == 404


def test_restart_all_endpoint_reaches_restart_all(client, three_instances, monkeypatch):
    # Used to 422: the /{inst_id}/restart route captured "all".
    calls = []
    monkeypatch.setattr(instances.manager, "restart_all", lambda: calls.append(True))
    r = client.post("/api/instance/all/restart")
    assert (r.status_code, r.json()) == (
        202,
        {"ok": True, "message": "Restarting all instances..."},
    )
    assert calls == [True]


def test_stop_one_instance(client, three_instances, recorded_sleeps):
    start(client)
    port = three_instances["instances"][0]["amcp_port"]

    r = client.post("/api/instance/1/stop")
    instances.manager.wait()

    assert (r.status_code, r.json()["message"]) == (202, "Stopping GFX1...")
    assert AMCPClient(port=port, timeout=0.5).ping() is False
    assert sorted(instances.manager._procs) == [2, 3]


def test_reordering_while_live_still_stops_the_right_process(
    client, three_instances, recorded_sleeps
):
    start(client)
    gfx1, gfx2, media1 = three_instances["instances"]

    client.post("/api/config", json={"instances": [media1, gfx2, gfx1]})
    ids = {i["name"]: i["id"] for i in client.get("/api/config").json()["instances"]}
    client.post(f"/api/instance/{ids['GFX1']}/stop")
    instances.manager.wait()

    assert AMCPClient(port=gfx1["amcp_port"], timeout=0.5).ping() is False
    assert AMCPClient(port=media1["amcp_port"]).ping() is True

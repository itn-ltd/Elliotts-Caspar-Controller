"""Start All / Stop All / restart through the web API, with fake CasparCG processes."""

import time

import pytest
from conftest import amcp_log, free_port, instance, windows_only

from caspar_controller import core
from caspar_controller.amcp_client import AMCPClient
from caspar_controller.process_manager import CasparProcessManager

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


@pytest.fixture(autouse=True)
def stop_everything():
    yield
    for m in list(core._managers.values()):
        m.stop()


def test_start_all(
    client, three_instances, caspar_dir, recorded_sleeps, start_order, no_global_kills
):
    ports = [i["amcp_port"] for i in three_instances["instances"]]

    r = client.post("/api/server/start")

    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "Started 3/3 instances"}
    # Every CasparCG on the machine is killed first, then instances start in config order.
    assert no_global_kills == ["kill_all"]
    assert start_order == ports
    # 5 s gap between instances, none after the last.
    assert recorded_sleeps == [5, 5]
    # Configs and test patterns are written next to the exe.
    for n in (1, 2, 3):
        assert (caspar_dir / f"casparcg_inst_{n}.config").exists()
    assert (caspar_dir / "media" / "TEST_BARS.png").exists()
    # Each instance answered AMCP, then got its startup command.
    assert amcp_log(caspar_dir, ports[0])[-1] == 'PLAY 1-1 [HTML] "https://example.com/gfx1"'
    assert amcp_log(caspar_dir, ports[1])[-1] == "CLEAR 1"
    assert amcp_log(caspar_dir, ports[2])[-1] == 'PLAY 1-1 "AMB" LOOP'
    assert all("VERSION" in amcp_log(caspar_dir, p) for p in ports)
    assert sorted(core._managers) == [1, 2, 3]
    assert any("Started 3/3 instances" in line for line in core._log)


def test_stop_all(client, three_instances, recorded_sleeps, no_global_kills):
    client.post("/api/server/start")
    ports = [i["amcp_port"] for i in three_instances["instances"]]

    r = client.post("/api/server/stop")

    assert r.json() == {"ok": True}
    assert core._managers == {}
    assert no_global_kills == ["kill_all", "kill_all"]
    assert not any(AMCPClient(port=p, timeout=0.5).ping() for p in ports)
    assert core._log[-1].endswith("All CasparCG instances stopped.")


def test_start_with_no_instances_is_400(client, write_config):
    write_config({"caspar_exe_path": "casparcg.exe", "instances": []})
    r = client.post("/api/server/start")
    assert r.status_code == 400
    assert r.json()["detail"] == "No instances configured"


def test_partial_failure_still_returns_200(client, three_instances, recorded_sleeps, monkeypatch):
    failing = three_instances["instances"][1]["amcp_port"]
    original = CasparProcessManager.start
    monkeypatch.setattr(
        CasparProcessManager,
        "start",
        lambda self: False if self.amcp_port == failing else original(self),
    )

    r = client.post("/api/server/start")

    assert r.status_code == 200
    assert r.json()["message"] == "Started 2/3 instances (failed: [2])"
    assert any("Inst 2 (GFX2) FAILED to start" in line for line in core._log)
    # The gap is skipped after a failed instance.
    assert recorded_sleeps == [5]


def test_all_failing_is_500(client, three_instances, recorded_sleeps, monkeypatch):
    monkeypatch.setattr(CasparProcessManager, "start", lambda self: False)
    r = client.post("/api/server/start")
    assert r.status_code == 500
    assert r.json()["detail"] == "All instances failed to start"


def test_restart_one_instance(client, three_instances, caspar_dir, recorded_sleeps):
    client.post("/api/server/start")
    port = three_instances["instances"][2]["amcp_port"]
    old_pid = core._managers[3].pid

    core._restart_instance_bg(3)  # what POST /api/instance/3/restart runs in a thread

    assert core._managers[3].pid != old_pid
    log = amcp_log(caspar_dir, port)  # appended to by both the old and new process
    assert log[-1] == 'PLAY 1-1 "AMB" LOOP'
    assert log.count('PLAY 1-1 "AMB" LOOP') == 2
    assert "BYE" in log
    assert AMCPClient(port=port).ping() is True
    assert any("Inst 3 (MEDIA1) restarted" in line for line in core._log)


def test_restart_unmanaged_instance_kills_by_port(three_instances, caspar_dir, no_global_kills):
    port = three_instances["instances"][0]["amcp_port"]
    core._restart_instance_bg(1)
    assert no_global_kills == [("kill_port", port)]
    assert AMCPClient(port=port).ping() is True


def test_restart_endpoint_returns_immediately(client, three_instances, monkeypatch):
    started = []
    monkeypatch.setattr(core, "_restart_instance_bg", started.append)
    r = client.post("/api/instance/2/restart")
    assert r.json() == {"ok": True, "message": "Restarting GFX2..."}
    assert client.post("/api/instance/99/restart").status_code == 404


def test_stop_one_instance(client, three_instances, recorded_sleeps):
    client.post("/api/server/start")
    port = three_instances["instances"][0]["amcp_port"]

    r = client.post("/api/instance/1/stop")
    assert r.json() == {"ok": True, "message": "Stopping GFX1..."}
    for _ in range(50):  # stop runs on a background thread
        if 1 not in core._managers:
            break
        time.sleep(0.1)  # real sleep: only core.time is patched

    assert 1 not in core._managers
    assert AMCPClient(port=port, timeout=0.5).ping() is False
    assert sorted(core._managers) == [2, 3]

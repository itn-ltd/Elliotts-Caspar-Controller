"""The console panel's feed and auto-restart (#8)."""

import io
import time
import types

import psutil
import pytest
from conftest import API_KEY, amcp_log, free_port, instance, windows_only
from test_status import events_of

from caspar_controller import instances, log
from caspar_controller.caspar.process import CasparProcessManager


def wait_for(check, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.1)  # real sleep: only the manager's gap is patched
    return False


@pytest.fixture
def media(caspar_dir, write_config):
    inst = instance(
        1, "MEDIA1", "media", startup_command='PLAY 1-1 "AMB" LOOP', amcp_port=free_port()
    )
    return write_config({"caspar_exe_path": str(caspar_dir / "casparcg.cmd"), "instances": [inst]})[
        "instances"
    ][0]


@pytest.fixture
def started(media, recorded_sleeps):
    instances.manager.start_all()
    instances.manager.wait()
    yield instances.manager._procs[1]
    instances.manager.wait()
    for m in list(instances.manager._procs.values()):
        m.stop()


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(instances, "BACKOFF", (0, 0, 0, 0, 0))


@pytest.fixture
def notified():
    messages = []
    instances.manager.notify = messages.append
    return messages


def crash(m: CasparProcessManager) -> None:
    """Kill CasparCG (the fake's python) under its launcher, as a crash would."""
    for child in psutil.Process(m.pid).children(recursive=True):
        child.kill()
    assert wait_for(lambda: not m.alive())


# ---------------------------------------------------------------- console


def test_output_lines_are_stripped_of_colour_codes():
    lines = []
    m = CasparProcessManager(exe_path="casparcg.exe", on_output=lines.append)
    m._read_output(io.BytesIO(b"\x1b[32mloaded\x1b[0m\r\nnext line\n"))
    assert lines == ["loaded", "next line"]


@windows_only
def test_console_streams_caspar_output(client, started):
    console = instances.manager.console(1)
    assert wait_for(lambda: "[fake casparcg] AMCP listening" in console.since(0))
    lines = [
        d["lines"]
        for name, d in events_of(client.get("/api/events?console=1").text)
        if name == "console"
    ]
    assert "[fake casparcg] AMCP listening" in lines[0]  # the first event holds what's kept


def test_console_stream_of_unknown_instance_is_404(client, write_config):
    write_config({"instances": []})
    assert client.get("/api/events?console=9").status_code == 404


@windows_only
def test_console_command_reaches_caspar(local_anon, started):
    r = local_anon.post("/api/instance/1/console", json={"command": "INFO"})
    assert r.json() == {"ok": True}
    console = instances.manager.console(1)
    assert wait_for(lambda: "[fake casparcg] console: INFO" in console.since(0))
    assert "> INFO" in console.since(0)


def test_console_command_needs_a_session(anon, write_config):
    # Another device with the API key: the key never reaches CasparCG's console.
    write_config({"instances": [instance(1, "GFX1")]})
    r = anon.post(
        "/api/instance/1/console", json={"command": "INFO"}, headers={"X-API-Key": API_KEY}
    )
    assert r.status_code == 401


def test_console_command_when_not_running_is_409(local_anon, write_config):
    write_config({"instances": [instance(1, "GFX1")]})
    r = local_anon.post("/api/instance/1/console", json={"command": "INFO"})
    assert r.status_code == 409


# ---------------------------------------------------------------- auto-restart


@windows_only
def test_crashed_instance_restarts_with_its_startup_command(
    client, started, media, caspar_dir, no_backoff, notified
):
    old_pid = started.pid
    crash(started)

    instances.manager.watch()  # notices the crash
    assert client.get("/api/status").json()["instances"][0]["status"] == "restarting (1/5)"
    instances.manager.watch()  # back-off over: restart
    instances.manager.wait()

    assert instances.manager._procs[1].pid != old_pid
    assert amcp_log(caspar_dir, media["amcp_port"]).count('PLAY 1-1 "AMB" LOOP') == 2
    assert notified[0].startswith("Inst 1 (MEDIA1) crashed (exit code")
    assert any("Inst 1 (MEDIA1) restarted" in line for line in log._log)
    assert any(line.startswith("--- crashed") for line in instances.manager.console(1).since(0))
    assert instances.manager.state_of(1) is None


@windows_only
def test_hung_instance_is_killed_and_restarted(started, no_backoff, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(instances, "time", types.SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(instances.manager, "_status", {1: instances.OFF})  # not answering
    old_pid = started.pid

    instances.manager.watch()  # starts the 30 s clock
    assert instances.manager.state_of(1) is None
    clock[0] += 31
    instances.manager.watch()
    assert instances.manager.state_of(1) == "restarting (1/5)"
    instances.manager.watch()
    instances.manager.wait()

    assert not psutil.pid_exists(old_pid) or psutil.Process(old_pid).status() == "zombie"
    assert instances.manager._procs[1].pid != old_pid


class DeadProcess:
    """A managed process that has exited."""

    amcp_port = 0
    pid = None

    def alive(self):
        return False

    def exit_code(self):
        return 3

    def stop(self):
        pass


@pytest.fixture
def dead(write_config, monkeypatch):
    write_config({"instances": [instance(1, "GFX1")]})
    instances.manager._procs[1] = DeadProcess()
    launches = []
    monkeypatch.setattr(
        instances.manager, "_launch", lambda cfg, inst, verb="started": launches.append(1) and False
    )
    return launches


def test_repeated_crashes_stop_at_the_cap_with_an_alert(dead, no_backoff, notified):
    for _ in range(8):
        instances.manager.watch()
        instances.manager.wait()
    assert len(dead) == 5  # five restarts, then it gives up
    assert instances.manager.state_of(1) == "failed"
    assert "Gave up after 5 restarts in 10 minutes" in notified[-1]


def test_back_off_grows_between_attempts(dead, notified):
    instances.manager.watch()
    assert notified[-1].endswith("restarting in 2 s (1/5).")


def test_operator_stop_is_never_undone(dead, no_backoff):
    instances.manager.watch()  # crash noticed, restart scheduled
    instances.manager.stop(1)
    instances.manager.wait()
    for _ in range(3):
        instances.manager.watch()
        instances.manager.wait()
    assert dead == []
    assert instances.manager.state_of(1) is None


def test_starting_again_clears_failed(dead, no_backoff):
    for _ in range(8):
        instances.manager.watch()
        instances.manager.wait()
    instances.manager.restart(1)
    assert instances.manager.state_of(1) is None


@pytest.mark.parametrize(
    ("global_on", "override", "restarts"),
    [(True, "inherit", True), (False, "inherit", False), (False, "on", True), (True, "off", False)],
)
def test_global_and_per_instance_settings(
    write_config, monkeypatch, no_backoff, global_on, override, restarts
):
    write_config(
        {
            "auto_restart": global_on,
            "instances": [{**instance(1, "GFX1"), "auto_restart": override}],
        }
    )
    instances.manager._procs[1] = DeadProcess()
    instances.manager.watch()
    assert (instances.manager.state_of(1) is not None) is restarts


def test_card_toggle_sets_the_instance_override(client, write_config):
    write_config({"instances": [instance(1, "GFX1")]})
    r = client.post("/api/instance/1/auto-restart", json={"auto_restart": "off"})
    assert r.json() == {"ok": True, "auto_restart": False}
    assert client.get("/api/status").json()["instances"][0]["auto_restart"] is False
    assert client.get("/api/config").json()["instances"][0]["auto_restart"] == "off"

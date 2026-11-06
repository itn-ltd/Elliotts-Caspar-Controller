"""The console panel's feed (CasparCG's log files) and auto-restart (#8)."""

import time
import types

import psutil
import pytest
from conftest import amcp_log, free_port, instance, windows_only
from test_status import events_of

from caspar_controller import autorestart, instances, log, tally
from caspar_controller.caspar.logtail import BACKFILL, LogTail
from caspar_controller.caspar.process import CasparProcessManager, _AdoptedProcess


def wait_for(check, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.1)  # real sleep: only the manager's gap is patched
    return False


def console_lines(inst_id: int = 1) -> list:
    return instances.manager.console(inst_id).since(0)[0]


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
    monkeypatch.setattr(autorestart, "BACKOFF", (0, 0, 0, 0, 0))


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(autorestart, "time", types.SimpleNamespace(time=lambda: now[0]))
    return now


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


# ---------------------------------------------------------------- log files


def test_log_tail_returns_new_whole_lines(tmp_path):
    tail = LogTail(str(tmp_path))
    assert tail.read() == []  # no log yet
    log_file = tmp_path / "caspar_2026-11-06.log"
    log_file.write_bytes(b"\x1b[32mloaded\x1b[0m\r\nhalf a li")
    assert tail.read() == ["loaded"]  # colour codes stripped; the half line waits
    with log_file.open("ab") as f:
        f.write(b"ne\nnext\n")
    assert tail.read() == ["half a line", "next"]
    assert tail.read() == []


def test_log_tail_follows_the_daily_rollover(tmp_path):
    tail = LogTail(str(tmp_path))
    (tmp_path / "caspar_2026-11-05.log").write_text("yesterday\n")
    assert tail.read() == ["yesterday"]
    time.sleep(0.05)
    (tmp_path / "caspar_2026-11-06.log").write_text("today\n")
    assert tail.read() == ["today"]


def test_log_tail_starts_near_the_end_of_a_big_log(tmp_path):
    (tmp_path / "caspar.log").write_text("old line\n" * (BACKFILL // 9 + 100) + "last\n")
    lines = LogTail(str(tmp_path)).read()
    assert lines[-1] == "last" and len(lines) < BACKFILL // 9
    assert all(line == "old line" for line in lines[:-1])  # no half line at the start


@windows_only
def test_console_shows_caspar_log_and_survives_the_app(client, started):
    assert wait_for(
        lambda: (
            instances.manager.poll_status() or "[fake casparcg] AMCP listening" in console_lines()
        )
    )
    events = events_of(client.get("/api/events?console=1").text)
    first_console = next(d["lines"] for name, d in events if name == "console")
    assert "[fake casparcg] AMCP listening" in first_console  # the first event holds what's kept
    # No pipes: CasparCG's stdin/stdout aren't tied to this app, so it outlives a Quit.
    assert started._process.stdout is None and started._process.stdin is None


def test_console_stream_ignores_an_unknown_instance(client, write_config):
    # A deleted instance a browser remembers: ignored, so the page doesn't reload in a loop.
    write_config({"instances": []})
    r = client.get("/api/events?console=9")
    assert r.status_code == 200
    assert "console" not in [name for name, _ in events_of(r.text)]


def test_reconnected_instances_report_no_exit_code():
    m = CasparProcessManager(exe_path="casparcg.exe")
    m._process = _AdoptedProcess(99999999)  # not launched by us, and long gone
    assert m.exit_code() is None


# ---------------------------------------------------------------- auto-restart


@windows_only
def test_crashed_instance_restarts_with_its_startup_command(
    client, started, media, caspar_dir, no_backoff, notified
):
    old_pid = started.pid
    crash(started)

    instances.manager.watch()  # notices the crash
    assert client.get("/api/status").json()["instances"][0]["status"] == "crashed (1/5)"
    instances.manager.watch()  # back-off over: restart
    assert instances.manager.state_of(1) == "restarting (1/5)"
    instances.manager.wait()

    assert instances.manager._procs[1].pid != old_pid
    assert amcp_log(caspar_dir, media["amcp_port"]).count('PLAY 1-1 "AMB" LOOP') == 2
    assert notified[0].startswith("Inst 1 (MEDIA1) crashed (exit code")
    assert any("Inst 1 (MEDIA1) restarted" in line for line in log._log)
    assert any(line.startswith("--- crashed") for line in console_lines())
    assert instances.manager.state_of(1) is None


@pytest.fixture
def hung(started, clock, monkeypatch):
    """The started instance stops answering AMCP for 31 s."""
    monkeypatch.setattr(instances.manager, "_status", {1: instances.OFF})
    instances.manager.watch()  # starts the hang clock
    assert instances.manager.state_of(1) is None
    clock[0] += 31
    return started


@windows_only
def test_hung_instance_is_killed_at_once_then_restarted(hung, no_backoff):
    old_pid = hung.pid
    instances.manager.watch()
    assert instances.manager.state_of(1) == "hung (1/5)"
    instances.manager.wait()  # the hung process is stopped straight away
    assert not hung.alive()
    instances.manager.watch()
    instances.manager.wait()
    assert instances.manager._procs[1].pid != old_pid


@windows_only
def test_operator_stop_during_the_back_off_still_kills_a_hung_process(hung, monkeypatch):
    # Used to orphan it: the only reference was dropped with the pending restart.
    instances.manager.watch()  # hang: restart in 2 s
    instances.manager.stop(1)
    instances.manager.wait()
    assert not hung.alive()
    assert 1 not in instances.manager._procs
    assert instances.manager.state_of(1) is None


@windows_only
def test_hung_output_on_air_is_not_restarted(hung, monkeypatch, notified):
    monkeypatch.setattr(tally, "get_tally", lambda: {"PCR3 MEDIA1": {"program": True}})
    for _ in range(3):
        instances.manager.watch()
    instances.manager.wait()
    assert hung.alive()  # a frozen picture beats a cut to black
    assert notified == [
        "Inst 1 (MEDIA1) stopped answering for 30 s while on air: not restarting it until it's off air."
    ]
    monkeypatch.setattr(tally, "get_tally", lambda: {})  # taken off air
    instances.manager.watch()
    assert instances.manager.state_of(1) == "hung (1/5)"


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
    assert notified[-1] == "Inst 1 (GFX1) crashed (exit code 3): restarting in 2 s (1/5)."


def test_ten_clean_minutes_reset_the_count(dead, clock):
    instances.manager._record(1).restarts.extend([clock[0] - 601] * 4)  # all over 10 min ago
    instances.manager.watch()
    assert instances.manager.state_of(1) == "crashed (1/5)"


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
    ("global_on", "override", "state"),
    [
        (True, "inherit", "crashed (1/5)"),
        (False, "inherit", "crashed"),  # shown, not restarted
        (False, "on", "crashed (1/5)"),
        (True, "off", "crashed"),
    ],
)
def test_global_and_per_instance_settings(write_config, no_backoff, global_on, override, state):
    write_config(
        {
            "auto_restart": global_on,
            "instances": [{**instance(1, "GFX1"), "auto_restart": override}],
        }
    )
    instances.manager._procs[1] = DeadProcess()
    instances.manager.watch()
    assert instances.manager.state_of(1) == state


def test_card_toggle_sets_the_instance_override(client, write_config):
    write_config({"instances": [instance(1, "GFX1")]})
    r = client.post("/api/instance/1/auto-restart", json={"auto_restart": "off"})
    assert r.json() == {"ok": True, "auto_restart": False}
    assert client.get("/api/status").json()["instances"][0]["auto_restart"] is False
    assert client.get("/api/config").json()["instances"][0]["auto_restart"] == "off"

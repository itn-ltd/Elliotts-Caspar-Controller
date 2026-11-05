"""What the tray does at launch: reconnect to running CasparCG, then maybe auto-start."""

import pytest
from conftest import instance

from caspar_controller import instances, log, tray
from caspar_controller.caspar.process import CasparProcessManager


@pytest.fixture
def start_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(instances, "start_all", lambda cfg: calls.append(cfg) or ([1], []))
    return calls


@pytest.fixture
def exe(tmp_path):
    path = tmp_path / "casparcg.exe"
    path.write_bytes(b"")
    return str(path)


def test_no_autostart_by_default(write_config, exe, start_calls):
    write_config({"caspar_exe_path": exe, "instances": [instance(1, "GFX1")]})
    tray.start_caspar()
    assert start_calls == []


def test_autostart_starts_everything(write_config, exe, start_calls):
    write_config(
        {"caspar_exe_path": exe, "autostart_caspar": True, "instances": [instance(1, "GFX1")]}
    )
    tray.start_caspar()
    assert len(start_calls) == 1
    assert log._log[-1].endswith("Auto-started 1/1 instances")


def test_autostart_skipped_without_exe(write_config, start_calls):
    write_config({"autostart_caspar": True, "instances": [instance(1, "GFX1")]})
    tray.start_caspar()
    assert start_calls == []
    assert "set the CasparCG path" in log._log[-1]


def test_only_missing_instances_are_auto_started(write_config, exe, start_calls, monkeypatch):
    monkeypatch.setattr(CasparProcessManager, "adopt_existing", lambda self: self.amcp_port == 5250)
    missing = []
    monkeypatch.setattr(instances, "start_missing", lambda cfg: missing.append(cfg) or ([2], []))
    insts = [instance(1, "GFX1"), instance(2, "GFX2")]
    write_config({"caspar_exe_path": exe, "autostart_caspar": True, "instances": insts})
    tray.start_caspar()
    assert list(instances._managers) == [1]
    assert (len(missing), start_calls) == (1, [])  # start_all would kill instance 1
    assert log._log[-1].endswith("Auto-started 1/1 instances")


def test_running_instances_are_adopted_not_restarted(write_config, exe, start_calls, monkeypatch):
    monkeypatch.setattr(CasparProcessManager, "adopt_existing", lambda self: True)
    insts = [instance(1, "GFX1"), instance(2, "GFX2")]
    write_config({"caspar_exe_path": exe, "autostart_caspar": True, "instances": insts})
    tray.start_caspar()
    assert sorted(instances._managers) == [1, 2]
    assert start_calls == []  # auto-start would kill the live outputs

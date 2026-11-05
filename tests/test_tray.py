"""What the tray does at launch: reconnect to running CasparCG, then maybe auto-start."""

import pytest
from conftest import free_port, instance

from caspar_controller import instances, log, tray
from caspar_controller.caspar.process import CasparProcessManager


@pytest.fixture
def started(monkeypatch):
    """Ports the app launched CasparCG on (nothing really starts)."""
    ports = []
    monkeypatch.setattr(
        CasparProcessManager, "start", lambda self: ports.append(self.amcp_port) or True
    )
    return ports


@pytest.fixture
def base():
    """A free base port, so nothing reaches a real CasparCG on 5250."""
    return free_port()


@pytest.fixture
def exe(tmp_path):
    path = tmp_path / "casparcg.exe"
    path.write_bytes(b"")
    return str(path)


def launch() -> None:
    tray.start_caspar()
    instances.manager.wait()


def test_no_autostart_by_default(write_config, exe, started, base):
    write_config(
        {"caspar_exe_path": exe, "amcp_base_port": base, "instances": [instance(1, "GFX1")]}
    )
    launch()
    assert started == []


def test_autostart_starts_everything(write_config, exe, started, base):
    insts = [instance(1, "GFX1")]
    write_config(
        {
            "caspar_exe_path": exe,
            "amcp_base_port": base,
            "autostart_caspar": True,
            "instances": insts,
        }
    )
    launch()
    assert started == [base]
    assert log._log[-1].endswith("Started 1/1 instances")


def test_autostart_skipped_without_exe(write_config, started, base):
    write_config(
        {"amcp_base_port": base, "autostart_caspar": True, "instances": [instance(1, "GFX1")]}
    )
    launch()
    assert started == []
    assert "set the CasparCG path" in log._log[-1]


def test_only_missing_instances_are_auto_started(write_config, exe, started, base, monkeypatch):
    monkeypatch.setattr(CasparProcessManager, "adopt_existing", lambda self: self.amcp_port == base)
    insts = [instance(1, "GFX1"), instance(2, "GFX2")]
    write_config(
        {
            "caspar_exe_path": exe,
            "amcp_base_port": base,
            "autostart_caspar": True,
            "instances": insts,
        }
    )
    launch()
    assert started == [base + 1]  # instance 1 is on air: never restarted
    assert sorted(instances.manager._procs) == [1, 2]
    assert any("Reconnected to 1 running" in line for line in log._log)


def test_running_instances_are_adopted_not_restarted(write_config, exe, started, base, monkeypatch):
    monkeypatch.setattr(CasparProcessManager, "adopt_existing", lambda self: True)
    monkeypatch.setattr(CasparProcessManager, "is_running", lambda self: True)
    insts = [instance(1, "GFX1"), instance(2, "GFX2")]
    write_config(
        {
            "caspar_exe_path": exe,
            "amcp_base_port": base,
            "autostart_caspar": True,
            "instances": insts,
        }
    )
    launch()
    assert sorted(instances.manager._procs) == [1, 2]
    assert started == []

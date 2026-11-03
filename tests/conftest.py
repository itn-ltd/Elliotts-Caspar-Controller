"""Shared fixtures for the characterisation suite.

Every test runs against an isolated config directory, and the "kill every
CasparCG on this machine" helpers are replaced with recorders so the suite can
never take down a real CasparCG running on a developer or gallery machine.
"""

import json
import os
import socket
import sys
import threading
import time
import types
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from elliotts_casper_controller import config_manager, core, ndi_tally
from elliotts_casper_controller.process_manager import CasparProcessManager

TESTS_DIR = Path(__file__).parent
FAKE_CASPAR = TESTS_DIR / "fake_caspar.py"
FIXTURES = TESTS_DIR / "fixtures"

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="launches Windows processes")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def no_global_kills(monkeypatch):
    """Record calls to the machine-wide kill helpers instead of running them."""
    calls = []
    monkeypatch.setattr(
        CasparProcessManager,
        "_kill_all_caspar_instances",
        staticmethod(lambda: calls.append("kill_all")),
    )
    monkeypatch.setattr(
        CasparProcessManager,
        "_kill_caspar_on_port",
        staticmethod(lambda port: calls.append(("kill_port", port))),
    )
    return calls


@pytest.fixture(autouse=True)
def no_ndi(monkeypatch):
    """Behave as if ndi-python isn't installed, whatever the environment."""
    monkeypatch.setattr(ndi_tally, "is_available", lambda: False)
    monkeypatch.setattr(core, "_refresh_tally_monitors", lambda: None)


@pytest.fixture(autouse=True)
def fresh_core_state(monkeypatch):
    """Reset core's module-level state between tests."""
    monkeypatch.setattr(core, "_managers", {})
    monkeypatch.setattr(core, "_log", [])
    monkeypatch.setattr(core, "_sessions", set())
    monkeypatch.setattr(core, "_remote_name_cache", {})
    monkeypatch.setattr(core, "_status_cache", {"live": {}, "ts": 0.0})


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    """Point the app's config file at a temp directory.

    Also run from tmp_path: with no absolute exe path set, the app writes
    casparcg_inst_N.config files into the current directory.
    """
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "app"
    d.mkdir()
    monkeypatch.setattr(config_manager, "_config_dir", lambda: str(d))
    return d


@pytest.fixture
def write_config(config_dir):
    def _write(cfg: dict) -> dict:
        (config_dir / "elliotts_caspar_config.json").write_text(json.dumps(cfg), encoding="utf-8")
        return cfg

    return _write


@pytest.fixture
def client():
    # No context manager: the lifespan (status thread, NDI tally) is not started.
    return TestClient(core.app)


@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Replace core's time module so start-sequence gaps are recorded, not slept."""
    sleeps = []
    fake_time = types.SimpleNamespace(sleep=sleeps.append, time=time.time, strftime=time.strftime)
    monkeypatch.setattr(core, "time", fake_time)
    return sleeps


@pytest.fixture
def caspar_dir(tmp_path, monkeypatch):
    """A folder holding a casparcg.cmd shim that runs tests/fake_caspar.py.

    Any process left running in this folder is killed afterwards.
    """
    # Today's launcher relies on cmd finding the exe in the current directory,
    # which this variable (set by some hardened shells) disables. See #3.
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    d = tmp_path / "caspar"
    d.mkdir()
    (d / "casparcg.cmd").write_text(f'@"{sys.executable}" "{FAKE_CASPAR}" %*\r\n', encoding="utf-8")
    yield d
    target = os.path.normcase(str(d))
    for proc in psutil.process_iter(["pid"]):
        try:
            if os.path.normcase(proc.cwd()) == target:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass


def amcp_log(caspar_dir: Path, port: int) -> list:
    """Commands received by the fake CasparCG listening on ``port``."""
    path = caspar_dir / f"amcp_{port}.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def run_status_worker_once() -> None:
    """Run core's background status worker until it has published one snapshot."""
    core._status_refresh_stop.clear()
    t = threading.Thread(target=core._background_status_worker, daemon=True)
    t.start()
    deadline = time.time() + 10
    while core._status_cache["ts"] == 0.0 and time.time() < deadline:
        time.sleep(0.05)
    core._status_refresh_stop.set()
    t.join(timeout=5)


def instance(
    id_: int,
    name: str,
    type_: str = "html",
    url: str = "",
    startup_command: str = "",
    amcp_port: int = 0,
    ndi_name: str | None = None,
) -> dict:
    return {
        "id": id_,
        "name": name,
        "ndi_name": ndi_name or f"PCR3 {name}",
        "type": type_,
        "url": url,
        "startup_command": startup_command,
        "amcp_port": amcp_port,
    }

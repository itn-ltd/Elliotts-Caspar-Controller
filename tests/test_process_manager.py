"""Process manager launching a real (fake) CasparCG process."""

import psutil
import pytest
from conftest import free_port, instance, windows_only
from fake_amcp import FakeAMCPServer

from caspar_controller.caspar import config_xml
from caspar_controller.caspar.amcp import AMCPClient
from caspar_controller.caspar.process import CasparProcessManager

pytestmark = windows_only


@pytest.fixture
def manager(caspar_dir):
    port = free_port()
    cfg = {
        "caspar_exe_path": str(caspar_dir / "casparcg.cmd"),
        "video_mode": "1080p2500",
        "instances": [instance(1, "GFX1", amcp_port=port)],
    }
    config_xml.regenerate_instance_config(cfg, cfg["instances"][0])
    m = CasparProcessManager(
        exe_path=cfg["caspar_exe_path"],
        amcp_port=port,
        config_filename="casparcg_inst_1.config",
    )
    yield m
    m.stop()


def test_start_waits_for_amcp_and_stop_kills_tree(manager):
    assert manager.is_running() is False
    assert manager.pid is None

    assert manager.start() is True
    assert manager.is_running() is True
    root = psutil.Process(manager.pid)
    tree = [root, *root.children(recursive=True)]
    names = {p.name().lower() for p in tree}
    assert "cmd.exe" in names
    assert "python.exe" in names

    manager.stop()
    gone, alive = psutil.wait_procs(tree, timeout=5)
    assert alive == []
    assert manager.pid is None
    assert AMCPClient(port=manager.amcp_port, timeout=0.5).ping() is False


def test_start_is_a_no_op_when_already_running(manager):
    assert manager.start() is True
    pid = manager.pid
    assert manager.start() is True
    assert manager.pid == pid


def test_is_running_ignores_instances_it_does_not_own():
    with FakeAMCPServer() as s:
        m = CasparProcessManager(exe_path="casparcg.exe", amcp_port=s.port)
        assert m.is_running() is False


def test_stop_sends_bye_first():
    with FakeAMCPServer() as s:
        CasparProcessManager(exe_path="casparcg.exe", amcp_port=s.port).stop()
        assert s.commands == ["BYE"]

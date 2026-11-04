"""Injection, input validation and the removed self-updater (#3)."""

import xml.etree.ElementTree as ET

import psutil
import pytest
from conftest import TESTS_DIR, amcp_log, free_port, instance, windows_only
from fake_amcp import FakeAMCPServer

from elliotts_casper_controller import config_manager as cm, process_manager
from elliotts_casper_controller.amcp_client import AMCPClient
from elliotts_casper_controller.process_manager import CasparProcessManager

PACKAGE = TESTS_DIR.parent / "elliotts_casper_controller"
NASTY_NAME = 'A | B & C <D> -- "E"'

# ---------------------------------------------------------------- launching CasparCG


def test_caspar_launched_directly_without_a_shell(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        raise RuntimeError("not launching in this test")

    monkeypatch.setattr(process_manager.subprocess, "Popen", fake_popen)
    exe = str(tmp_path / "casparcg.exe")
    m = CasparProcessManager(
        exe_path=exe, amcp_port=free_port(), window_title=NASTY_NAME, config_filename="c.config"
    )
    m.start()
    # A list (no shell parses the name) starting with the full exe path.
    assert captured["args"] == [exe, "c.config"]


@windows_only
def test_odd_name_starts_where_current_dir_search_is_off(caspar_dir, monkeypatch):
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "1")
    port = free_port()
    cfg = {
        "caspar_exe_path": str(caspar_dir / "casparcg.cmd"),
        "video_mode": "1080p2500",
        "instances": [instance(1, NASTY_NAME, amcp_port=port)],
    }
    cm.regenerate_instance_config(cfg, cfg["instances"][0])
    m = CasparProcessManager(
        exe_path=cfg["caspar_exe_path"],
        amcp_port=port,
        window_title=NASTY_NAME,
        config_filename="casparcg_inst_1.config",
    )
    try:
        assert m.start() is True
        assert NASTY_NAME not in " ".join(psutil.Process(m.pid).cmdline())
    finally:
        m.stop()


# ---------------------------------------------------------------- casparcg.config


def test_generated_config_escapes_names(tmp_path):
    cfg = {
        "caspar_exe_path": str(tmp_path / "casparcg.exe"),
        "video_mode": "1080p2500",
        "media_path": "D:\\A&B <media>",
        "instances": [instance(1, NASTY_NAME, ndi_name="N&D <I> --")],
    }
    path = cm.regenerate_instance_config(cfg, cfg["instances"][0])
    root = ET.parse(path).getroot()  # valid XML despite <, &, -- and quotes
    assert root.find("./channels/channel/consumers/ndi/name").text == "N&D <I> --"
    assert root.find("./paths/media-path").text == "D:\\A&B <media>"
    assert NASTY_NAME not in open(path, encoding="utf-8").read()  # name isn't in a comment


# ---------------------------------------------------------------- AMCP


@pytest.fixture
def clip_player(write_config):
    with FakeAMCPServer() as s:
        write_config({"instances": [instance(1, "Clipplayer", "media", amcp_port=s.port)]})
        yield s


@pytest.mark.parametrize("clip", ['X" LOOP\r\nCLEAR 1', 'A"B', "A\nB", "A\x00B", "foo\\"])
def test_clip_triggers_reject_injection(client, clip_player, clip):
    by_name = client.get("/api/load", params={"instance": "Clipplayer", "clip": clip})
    by_id = client.get("/api/instance/1/load", params={"clip": clip})
    assert by_name.status_code == by_id.status_code == 400
    assert clip_player.commands == []


def test_raw_amcp_rejects_a_second_command(client, clip_player):
    r = client.post("/api/instance/1/amcp", json={"command": "CLEAR 1\r\nKILL"})
    assert r.status_code == 400
    assert clip_player.commands == []


def test_amcp_client_refuses_unsafe_commands():
    with FakeAMCPServer() as s:
        client = AMCPClient(port=s.port)
        assert client.send("VERSION\r\nKILL").startswith("ERROR:")
        assert client.play_html(1, 'https://x/" LOOP').startswith("ERROR:")
        assert s.commands == []


@windows_only
def test_unsafe_saved_startup_command_does_not_break_start(
    client, write_config, caspar_dir, recorded_sleeps
):
    # Configs saved before validation existed are never re-checked, so start must cope.
    port = free_port()
    inst = instance(1, "Clips", "media", startup_command="CLEAR 1\nKILL", amcp_port=port)
    write_config({"caspar_exe_path": str(caspar_dir / "casparcg.cmd"), "instances": [inst]})
    try:
        assert client.post("/api/server/start").status_code == 200
        assert "KILL" not in amcp_log(caspar_dir, port)
    finally:
        client.post("/api/server/stop")


# ---------------------------------------------------------------- config validation


def test_config_import_keeps_this_pcs_exe_when_path_missing_here(client, write_config, tmp_path):
    exe = tmp_path / "casparcg.exe"
    exe.write_bytes(b"")
    write_config({"caspar_exe_path": str(exe)})
    other_pc = {"caspar_exe_path": "Z:\\Nowhere\\casparcg.exe", "instances": [instance(1, "GFX1")]}
    assert client.post("/api/config/import", json=other_pc).status_code == 200
    assert client.get("/api/config").json()["caspar_exe_path"] == str(exe)


def test_config_import_rejects_arbitrary_exe(client):
    r = client.post(
        "/api/config/import",
        json={"caspar_exe_path": "C:\\Windows\\System32\\calc.exe", "instances": []},
    )
    assert r.status_code == 400


def test_exe_path_must_be_an_existing_casparcg(client, tmp_path):
    real = tmp_path / "casparcg.exe"
    real.write_bytes(b"")
    assert client.post("/api/config", json={"caspar_exe_path": str(real)}).status_code == 200
    assert client.post("/api/config", json={"caspar_exe_path": ""}).status_code == 200
    for bad in (str(tmp_path / "missing" / "casparcg.exe"), "C:\\Windows\\notepad.exe"):
        assert client.post("/api/config", json={"caspar_exe_path": bad}).status_code == 400


@pytest.mark.parametrize(
    "bad",
    [
        {"instances": [instance(1, "GFX1\r\n")]},
        {"instances": [instance(1, "")]},
        {"instances": [{**instance(1, "GFX1"), "id": "../x"}]},
        {"instances": [{**instance(1, "GFX1"), "id": True}]},
        {"instances": ["GFX1"]},
        {"instances": [instance(1, "GFX1", url='https://x/"y')]},
        {"instances": [instance(1, "GFX1", url="https://x/y\\")]},
        {"instances": [instance(1, "GFX1", startup_command="CLEAR 1\nKILL")]},
        {"instances": [{**instance(1, "GFX1"), "type": "shell"}]},
        {"instances": [instance(1, "GFX1", amcp_port=70000)]},
        {"remote_controllers": [{"url": "http://user:pw@host:5280"}]},
        {"remote_controllers": [{"url": "http://host:5280/api/x"}]},
        {"remote_controllers": [{"url": "file:///C:/"}]},
        {"remote_controllers": [{"url": "http://host:abc"}]},
        {"remote_controllers": [{"url": "http://host:99999"}]},
        {"server_name": "Studio\x07A"},
    ],
)
def test_unsafe_config_rejected_by_save_and_import(client, bad):
    assert client.post("/api/config", json=bad).status_code == 400
    assert client.post("/api/config/import", json=bad).status_code == 400


# Save's pydantic model already coerces or drops these; import takes raw JSON.
@pytest.mark.parametrize(
    "bad", [{"instances": None}, {"startup_delay": "60"}, {"autostart_caspar": "yes"}]
)
def test_malformed_import_rejected(client, bad):
    assert client.post("/api/config/import", json=bad).status_code == 400


@pytest.mark.parametrize(
    "url", ["http://u:p@host:5280", "http://host:5280/x", "ftp://host", "file:///C:/", "host:5280"]
)
def test_remote_test_only_accepts_plain_controller_urls(client, url):
    assert client.post("/api/remote/test", json={"url": url}).status_code == 400


@pytest.mark.parametrize("path", ["/api/config/import", "/api/key/rotate", "/api/password"])
def test_remote_proxy_only_allows_dashboard_actions(client, write_config, path):
    write_config({"remote_controllers": [{"url": f"http://127.0.0.1:{free_port()}"}]})
    r = client.post("/api/remote/0/proxy", json={"path": path})
    assert (r.status_code, r.json()["detail"]) == (400, "Invalid remote path")


def test_server_name_escaped_in_pages(client):
    assert client.post("/api/config", json={"server_name": "<b>Studio</b>"}).status_code == 200
    page = client.get("/").text
    assert "<b>Studio</b>" not in page
    assert "&lt;b&gt;Studio&lt;/b&gt;" in page


# ---------------------------------------------------------------- self-updater


def test_no_self_updater():
    sources = " ".join(p.read_text(encoding="utf-8") for p in PACKAGE.glob("*.py"))
    assert "api.github.com" not in sources
    assert "Check for Updates" not in sources

"""Known-bad behaviour, pinned as expected failures.

Each test asserts the *correct* behaviour and is marked ``xfail`` with the issue
that fixes it. xfail is strict (pyproject.toml), so when a fix lands the test
"unexpectedly passes" and fails the run: remove the marker in that PR.
"""

import itertools
import shutil
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from conftest import free_port, instance
from fake_amcp import FakeAMCPServer
from test_status import run_status_worker_once

from elliotts_casper_controller import config_manager as cm
from elliotts_casper_controller import core, ndi_tally, process_manager
from elliotts_casper_controller.process_manager import CasparProcessManager

FIXTURES = Path(__file__).parent / "fixtures"
PACKAGE = Path(__file__).parent.parent / "elliotts_casper_controller"


def issue(number, why):
    return pytest.mark.xfail(reason=f"#{number}: {why}")


@pytest.fixture(autouse=True)
def no_tally(monkeypatch):
    monkeypatch.setattr(core, "_refresh_tally_monitors", lambda: None)


# ---------------------------------------------------------------- #2 access control


@issue(2, "/api/* routes skip the password check")
def test_api_requires_auth_when_password_set(client, write_config):
    write_config({"web_password": "hunter2", "web_password_enabled": True})
    assert client.get("/api/config").status_code == 401


@issue(2, "GET /api/config returns the password in plain text")
def test_config_api_never_returns_password(client, write_config):
    write_config({"web_password": "hunter2", "web_password_enabled": True})
    client.post("/login", json={"password": "hunter2"})
    assert "web_password" not in client.get("/api/config").json()


@issue(2, "/static joins absolute paths, so any file on disk can be read")
def test_static_route_cannot_read_outside_static(client, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    assert client.get("/static/" + secret.as_posix()).status_code == 404


# ---------------------------------------------------------------- #3 injection, updater


@issue(3, "CasparCG is launched through a cmd /k string built from the instance name")
def test_caspar_launched_directly_without_a_shell(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        raise RuntimeError("not launching in this test")

    monkeypatch.setattr(process_manager.subprocess, "Popen", fake_popen)
    exe = str(tmp_path / "casparcg.exe")
    m = CasparProcessManager(
        exe_path=exe,
        amcp_port=free_port(),
        window_title="A | B & C <D>",
        config_filename="casparcg_inst_1.config",
    )
    m.start()
    # A list (no shell parsing of the name) whose first item is the full exe path, so
    # launching also works where NoDefaultCurrentDirectoryInExePath is set.
    assert captured["args"] == [exe, "casparcg_inst_1.config"]


@issue(3, "names are written into casparcg.config without XML escaping")
def test_generated_config_escapes_names(tmp_path):
    cfg = {
        "caspar_exe_path": str(tmp_path / "casparcg.exe"),
        "video_mode": "1080p2500",
        "instances": [instance(1, 'A&B <x> -- "y"', ndi_name="N&D <I>")],
    }
    path = cm.regenerate_instance_config(cfg, cfg["instances"][0])
    root = ET.parse(path).getroot()
    assert root.find("./channels/channel/consumers/ndi/name").text == "N&D <I>"


@issue(3, "clip names are not escaped, so quotes or CR/LF inject AMCP commands")
@pytest.mark.parametrize("clip", ['X" LOOP\r\nCLEAR 1', 'A"B'])
def test_clip_trigger_rejects_injection(client, write_config, clip):
    with FakeAMCPServer() as s:
        write_config({"instances": [instance(1, "Clipplayer", "media", amcp_port=s.port)]})
        r = client.get("/api/load", params={"instance": "Clipplayer", "clip": clip})
        assert r.status_code == 400
        assert s.commands == []


@issue(3, "config import saves any JSON, including an arbitrary exe path that Start then runs")
def test_config_import_is_validated(client):
    r = client.post(
        "/api/config/import",
        json={"caspar_exe_path": "C:\\Windows\\System32\\calc.exe", "instances": []},
    )
    assert r.status_code in (400, 422)


@issue(3, "the remote proxy appends any path to the remote URL, so '@host' redirects it")
def test_remote_proxy_rejects_host_change(client, write_config):
    write_config({"remote_controllers": [{"url": f"http://127.0.0.1:{free_port()}"}]})
    r = client.post("/api/remote/0/proxy", json={"path": f"@127.0.0.1:{free_port()}/x"})
    assert r.status_code == 400


@issue(3, "the app updates itself from the original author's personal GitHub")
def test_no_self_updater():
    assert "api.github.com" not in (PACKAGE / "gui_launcher.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- #5 process ownership, liveness


@issue(5, "Start All kills every casparcg.exe on the machine, managed or not")
def test_start_all_kills_only_owned_processes(
    client, write_config, recorded_sleeps, no_global_kills, monkeypatch
):
    monkeypatch.setattr(CasparProcessManager, "start", lambda self: True)
    write_config(
        {
            "caspar_exe_path": "casparcg.exe",
            "instances": [instance(1, "GFX1", amcp_port=free_port())],
        }
    )
    client.post("/api/server/start")
    assert no_global_kills == []


@issue(5, "liveness uses INFO 1-1, so a server that rejects INFO shows as stopped")
def test_liveness_uses_version(client, write_config):
    with FakeAMCPServer(replies={"INFO": "404 INFO FAILED"}) as s:
        write_config({"instances": [instance(1, "GFX1", amcp_port=s.port)]})
        run_status_worker_once()
        assert client.get("/api/status").json()["instances"][0]["status"] == "live"


@issue(
    5,
    "saving re-numbers instance IDs by list order, so running managers point at the wrong instance",
)
def test_instance_ids_survive_reorder(client, write_config):
    write_config({"instances": [instance(1, "A"), instance(2, "B")]})
    client.post("/api/config", json={"instances": [instance(2, "B"), instance(1, "A")]})
    assert [i["id"] for i in client.get("/api/config").json()["instances"]] == [2, 1]


# ---------------------------------------------------------------- #6 settings validation


@issue(6, "duplicate AMCP ports are accepted")
def test_duplicate_ports_rejected(client):
    r = client.post(
        "/api/config",
        json={"instances": [instance(1, "A", amcp_port=6000), instance(2, "B", amcp_port=6000)]},
    )
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------- #7 display


@issue(7, "the _<digits> suffix strip truncates real clip names")
def test_clip_names_with_numeric_suffix_kept():
    xml = (
        '<layer><foreground><producer type="ffmpeg-producer">'
        "<filename>PROMO_2024</filename></producer></foreground></layer>"
    )
    assert core._parse_layer_info("201 INFO OK\n" + xml)["clip"] == "PROMO_2024"


@issue(
    7,
    "INFO parsing expects a layout CasparCG 2.3 doesn't send (synthetic 2.3-style XML; replace with a capture)",
)
def test_caspar_23_info_layout_parsed():
    xml = (
        "<layer><background><producer>empty</producer></background><foreground>"
        "<file><clip>0 60</clip><name>AMB</name><path>media/AMB.mp4</path><time>12.5 60</time></file>"
        "<loop>false</loop><paused>false</paused><producer>ffmpeg</producer></foreground></layer>"
    )
    info = core._parse_layer_info("201 INFO OK\n" + xml)
    assert info["clip"] == "AMB"
    assert info["time"] == 12.5


@issue(7, "tally matches NDI names by substring, so GFX1 lights for GFX10")
def test_tally_matches_exact_ndi_name(monkeypatch):
    other = types.SimpleNamespace(ndi_name="PLAYOUT-PC (PCR3 GFX10)")
    fake_ndi = types.SimpleNamespace(
        FindCreate=types.SimpleNamespace,
        find_create_v2=lambda opts: object(),
        find_wait_for_sources=lambda finder, ms: None,
        find_get_current_sources=lambda finder: [other],
        find_destroy=lambda finder: None,
    )
    monkeypatch.setattr(ndi_tally, "ndi", fake_ndi)
    stop = types.SimpleNamespace(is_set=lambda: False)
    # One search pass, then the 15 s deadline has passed.
    clock = itertools.chain([0, 0], itertools.repeat(99))
    monkeypatch.setattr(ndi_tally, "time", types.SimpleNamespace(time=clock.__next__))
    assert ndi_tally._find_source("PCR3 GFX1", stop) is None


# ---------------------------------------------------------------- #12 v1.x migration


@issue(12, "v1 'channels' migration never runs: DEFAULT_CONFIG already has 'instances'")
def test_v1_channels_format_is_migrated(config_dir):
    shutil.copy(FIXTURES / "v1_channels_config.json", config_dir / "elliotts_caspar_config.json")
    cfg = cm.load()
    assert "channels" not in cfg
    assert [i["name"] for i in cfg["instances"]] == [
        "GFX1",
        "GFX2",
        "GFX3",
        "GFX4",
        "GFXPVW",
        "MEDIA1",
        "CH7",
    ]
    assert cfg["instances"][5] == {
        "id": 6,
        "name": "MEDIA1",
        "ndi_name": "PCR3 MEDIA1",
        "type": "media",
        "url": "",
        "startup_command": "PLAY 6-1 LOOP",
    }


@issue(12, "old 'amcp_port' is dropped instead of becoming 'amcp_base_port'")
def test_v1_amcp_port_is_migrated(write_config):
    write_config({"amcp_port": 6000})
    assert cm.load()["amcp_base_port"] == 6000

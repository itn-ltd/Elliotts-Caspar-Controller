"""Known-bad behaviour, pinned as expected failures.

Each test asserts the *correct* behaviour and is marked ``xfail`` with the issue
that fixes it. xfail is strict (pyproject.toml), so when a fix lands the test
"unexpectedly passes" and fails the run: remove the marker in that PR.
"""

import itertools
import shutil
import types

import pytest
from conftest import FIXTURES, free_port, instance, run_status_worker_once
from fake_amcp import FakeAMCPServer

from caspar_controller import config_manager as cm, core, ndi_tally
from caspar_controller.process_manager import CasparProcessManager


def issue(number: int, why: str, raises: type | tuple = AssertionError) -> pytest.MarkDecorator:
    # `raises` stops an unrelated error (e.g. from a partial fix) passing as "still broken".
    return pytest.mark.xfail(reason=f"#{number}: {why}", raises=raises)


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

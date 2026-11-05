"""/api/status, the background status worker and INFO parsing.

The INFO XML below is written in the shape today's parser expects. It has not
been captured from a real CasparCG yet: replace it with captures from the
galleries' build when they are available (see #7).
"""

import pytest
from conftest import free_port, instance, run_status_worker_once
from fake_amcp import EMPTY_LAYER_XML, FakeAMCPServer

from caspar_controller import log, tally
from caspar_controller.caspar.info import parse_layer_info

PLAYING_XML = (
    '<layer><foreground><producer type="ffmpeg-producer">'
    "<filename>D:\\media\\AMB.mp4</filename><time>12.5</time><duration>60</duration>"
    "<loop>1</loop></producer></foreground></layer>"
)
EMPTY = {"clip": None, "time": 0.0, "duration": 0.0, "loop": False, "producer": "empty"}


# ---------------------------------------------------------------- INFO parsing


def test_parse_playing_clip():
    assert parse_layer_info("201 INFO OK\r\n" + PLAYING_XML) == {
        "clip": "AMB.mp4",
        "time": 12.5,
        "duration": 60.0,
        "loop": True,
        "producer": "ffmpeg-producer",
    }


def test_parse_strips_numeric_suffix():
    xml = PLAYING_XML.replace("AMB.mp4", "AMB.mp4_0")
    assert parse_layer_info("201 INFO OK\n" + xml)["clip"] == "AMB.mp4"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ERROR: refused",
        "404 INFO FAILED\r\n",
        "201 INFO OK",
        "201 INFO OK\r\n",
        "201 INFO OK\r\n<not xml",
        "201 INFO OK\r\n<layer/>",
        "201 INFO OK\r\n" + EMPTY_LAYER_XML,
    ],
)
def test_parse_non_playing_replies_are_empty(raw):
    assert parse_layer_info(raw) == EMPTY


def test_parse_loop_false_values():
    for value in ("0", "false", "False"):
        xml = PLAYING_XML.replace("<loop>1</loop>", f"<loop>{value}</loop>")
        assert parse_layer_info("201 INFO OK\n" + xml)["loop"] is False


# ---------------------------------------------------------------- status


STATUS_KEYS = {"running", "version", "instances", "needs_setup", "server_name", "tally_available"}


def test_status_shape_before_first_poll(client, write_config):
    write_config(
        {
            "caspar_exe_path": "C:\\CasparCG\\casparcg.exe",
            "server_name": "Studio A",
            "instances": [instance(1, "GFX1", url="https://x", amcp_port=6100)],
        }
    )
    data = client.get("/api/status").json()

    assert set(data) == STATUS_KEYS
    assert data["running"] is False
    assert data["needs_setup"] is False
    assert data["server_name"] == "Studio A"
    assert data["tally_available"] is False  # NDI is stubbed out in conftest
    assert data["instances"][0] == {
        "id": 1,
        "name": "GFX1",
        "ndi_name": "PCR3 GFX1",
        "type": "html",
        "url": "https://x",
        "startup_command": "",
        "amcp_port": 6100,
        "status": "stopped",
        "layer": {},
        "on_program": False,
        "on_preview": False,
    }


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({"caspar_exe_path": "", "instances": [instance(1, "A")]}, True),
        ({"caspar_exe_path": "casparcg.exe", "instances": []}, True),
        ({"caspar_exe_path": "casparcg.exe", "instances": [instance(1, "A")]}, False),
    ],
)
def test_needs_setup(client, write_config, cfg, expected):
    write_config(cfg)
    assert client.get("/api/status").json()["needs_setup"] is expected


def test_worker_marks_live_instances_and_clip_info(client, write_config):
    with FakeAMCPServer(info_xml=PLAYING_XML) as live:
        write_config(
            {
                "caspar_exe_path": "casparcg.exe",
                "instances": [
                    instance(1, "MEDIA1", "media", amcp_port=live.port),
                    instance(2, "GFX2", amcp_port=free_port()),
                ],
            }
        )
        run_status_worker_once()

        assert live.commands[0] == "INFO 1-1"  # liveness comes from INFO, not VERSION
        data = client.get("/api/status").json()

    assert data["running"] is True
    one, two = data["instances"]
    assert one["status"] == "live"
    assert one["layer"]["clip"] == "AMB.mp4"
    assert two["status"] == "stopped"
    assert two["layer"] == EMPTY


def test_tally_is_merged_by_ndi_name(client, write_config, monkeypatch):
    write_config({"instances": [instance(1, "GFX1"), instance(2, "GFX2")]})
    monkeypatch.setattr(
        tally, "get_tally", lambda: {"PCR3 GFX2": {"program": True, "preview": False}}
    )
    a, b = client.get("/api/status").json()["instances"]
    assert (a["on_program"], b["on_program"], b["on_preview"]) == (False, True, False)


def test_log_endpoint_returns_event_log(client):
    log.log_event("hello")
    lines = client.get("/api/log").json()["log"]
    assert len(lines) == 1 and lines[0].endswith("] hello")


def test_log_keeps_last_200():
    for i in range(205):
        log.log_event(f"e{i}")
    assert len(log._log) == 200
    assert log._log[0].endswith("e5")

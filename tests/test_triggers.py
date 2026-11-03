"""HTTP clip triggers (Companion / panels) and the raw AMCP endpoint."""
import pytest
from conftest import free_port, instance
from fake_amcp import FakeAMCPServer

from elliotts_casper_controller import core


@pytest.fixture
def media_server(write_config):
    with FakeAMCPServer() as s:
        write_config({"instances": [
            instance(1, "GFX1", amcp_port=free_port()),
            instance(2, "Clipplayer", "media", amcp_port=s.port),
        ]})
        yield s


def test_load_by_name_loops_by_default(client, media_server):
    r = client.get("/api/load", params={"instance": "clipplayer", "clip": "MYCLIP"})
    assert r.status_code == 200
    assert r.json() == {
        "ok": True, "instance": "clipplayer", "clip": "MYCLIP",
        "command": 'PLAY 1-1 "MYCLIP" LOOP', "response": "202 PLAY OK",
    }
    assert media_server.commands == ['PLAY 1-1 "MYCLIP" LOOP']
    assert core._log[-1].endswith('HTTP load: clipplayer → PLAY 1-1 "MYCLIP" LOOP → 202 PLAY OK')


def test_load_once(client, media_server):
    client.get("/api/load", params={"instance": "Clipplayer", "clip": "FOLDER/CLIP 2", "loop": "false"})
    assert media_server.commands == ['PLAY 1-1 "FOLDER/CLIP 2"']


def test_load_unknown_instance_is_404(client, media_server):
    r = client.get("/api/load", params={"instance": "nope", "clip": "X"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Instance 'nope' not found"


def test_load_into_html_instance_is_400(client, media_server):
    r = client.get("/api/load", params={"instance": "GFX1", "clip": "X"})
    assert r.status_code == 400
    assert media_server.commands == []


def test_load_by_id(client, media_server):
    r = client.get("/api/instance/2/load", params={"clip": "AMB", "loop": "false"})
    assert r.json() == {"ok": True, "instance": "Clipplayer", "clip": "AMB",
                        "command": 'PLAY 1-1 "AMB"', "response": "202 PLAY OK"}
    assert client.get("/api/instance/1/load", params={"clip": "AMB"}).status_code == 400
    assert client.get("/api/instance/9/load", params={"clip": "AMB"}).status_code == 404


def test_load_requires_clip(client, media_server):
    assert client.get("/api/load", params={"instance": "Clipplayer"}).status_code == 422


def test_raw_amcp_command(client, media_server):
    r = client.post("/api/instance/2/amcp", json={"command": "  MIXER 1-1 OPACITY 0.5  "})
    assert r.json() == {"ok": True, "response": "202 MIXER OK"}
    assert media_server.commands == ["MIXER 1-1 OPACITY 0.5"]
    assert client.post("/api/instance/9/amcp", json={"command": "X"}).status_code == 404

"""Two controllers over real HTTP (#9), and a Triggers-page URL fired from another device."""

import threading
import time
from urllib.parse import quote

import pytest
import uvicorn
from conftest import API_KEY, free_port, instance
from fake_amcp import FakeAMCPServer

from caspar_controller import instances
from caspar_controller.api.app import app


@pytest.fixture
def agent_b():
    """This app served on a real port: the "other" controller. It shares this test's config."""
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def clips():
    with FakeAMCPServer() as s:
        yield s


def test_one_agent_controls_another(client, write_config, agent_b, clips, monkeypatch):
    write_config(
        {
            "instances": [instance(1, "Clips", "media", amcp_port=clips.port)],
            "remote_controllers": [{"url": agent_b, "api_key": API_KEY}],
        }
    )
    started = []
    monkeypatch.setattr(instances.manager, "start_all", lambda: started.append(True))

    remote = client.get("/api/remotes").json()["remotes"][0]
    assert (remote["online"], remote["status"]["instances"][0]["name"]) == (True, "Clips")
    assert client.post("/api/remote/0/server/start").json()["ok"] is True
    assert started == [True]
    r = client.post("/api/remote/0/instance/1/amcp", json={"command": "PLAY 1-1 AMB"})
    assert r.json()["response"] == "202 PLAY OK"
    assert clips.commands == ["PLAY 1-1 AMB"]
    assert client.post("/api/remote/0/test").json()["authorised"] is True


def test_a_copied_trigger_url_works_from_another_device(client, anon, write_config, clips):
    write_config({"instances": [instance(1, "Clipplayer", "media", amcp_port=clips.port)]})
    # What the Triggers page builds: lan_url + /api/load?instance=&clip=&api_key=
    base = client.get("/api/config").json()["lan_url"]
    key = client.get("/api/key").json()["api_key"]
    url = f"{base}/api/load?instance=Clipplayer&clip={quote('MY CLIP')}&api_key={quote(key)}"

    r = anon.get(url.removeprefix(base))  # anon: another PC, no session, only the URL
    assert r.status_code == 200
    assert clips.commands == ['PLAY 1-1 "MY CLIP" LOOP']

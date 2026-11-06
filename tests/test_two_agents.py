"""Two controllers over real HTTP (#9), and a Triggers-page URL fired from another device."""

import threading
import time
from urllib.parse import quote

import pytest
import requests
import uvicorn
from conftest import API_KEY, free_port, instance
from fake_amcp import FakeAMCPServer

from caspar_controller import instances, settings
from caspar_controller.api.app import app


@pytest.fixture
def serve():
    """Start this app on a real port; returns its URL. Agents share this test's config."""
    servers = []

    def start() -> str:
        port = free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        servers.append((server, thread))
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        return f"http://127.0.0.1:{port}"

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def clips():
    with FakeAMCPServer() as s:
        yield s


def test_two_agents_control_each_other(write_config, serve, clips, monkeypatch):
    a, b = serve(), serve()
    write_config(
        {
            "instances": [instance(1, "Clips", "media", amcp_port=clips.port)],
            "remote_controllers": [
                {"url": b, "api_key": API_KEY},  # remote 0: A's view of B
                {"url": a, "api_key": API_KEY},  # remote 1: B's view of A
                {"url": b, "api_key": "wrong"},  # remote 2: a bad key
            ],
        }
    )
    # A password, so the key is what lets each agent in, not first-run local trust.
    cfg = settings.load()
    settings.set_password(cfg, "pw")
    settings.save(cfg)
    started = []
    monkeypatch.setattr(instances.manager, "start_all", lambda: started.append(True))

    def call(agent: str, path: str, **kwargs) -> requests.Response:
        return requests.post(agent + path, headers={"X-API-Key": API_KEY}, timeout=10, **kwargs)

    assert call(a, "/api/remote/0/server/start").json()["ok"] is True  # A starts B's outputs
    r = call(b, "/api/remote/1/instance/1/amcp", json={"command": "PLAY 1-1 AMB"})  # B drives A
    assert r.json()["response"] == "202 PLAY OK"
    assert (started, clips.commands) == ([True], ["PLAY 1-1 AMB"])
    assert call(a, "/api/remote/0/test").json()["authorised"] is True
    assert call(a, "/api/remote/2/test").json()["authorised"] is False
    assert call(a, "/api/remote/2/server/start").status_code == 502  # the key really is checked
    assert started == [True]


def test_a_copied_trigger_url_works_from_another_device(client, anon, write_config, clips):
    write_config({"instances": [instance(1, "Clipplayer", "media", amcp_port=clips.port)]})
    # What the Triggers page builds: lan_url + /api/load?instance=&clip=&api_key=
    base = client.get("/api/config").json()["lan_url"]
    key = client.get("/api/key").json()["api_key"]
    url = f"{base}/api/load?instance=Clipplayer&clip={quote('MY CLIP')}&api_key={quote(key)}"

    r = anon.get(url.removeprefix(base))  # anon: another PC, no session, only the URL
    assert r.status_code == 200
    assert clips.commands == ['PLAY 1-1 "MY CLIP" LOOP']

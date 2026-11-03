"""Remote Controllers, against a fake remote controller HTTP server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import free_port

REMOTE_STATUS = {
    "running": True,
    "version": "1.1.1",
    "server_name": "Studio B",
    "needs_setup": False,
    "tally_available": False,
    "instances": [{"id": 1, "name": "GFX1", "type": "html", "status": "live"}],
}


class FakeRemote:
    """Answers like another controller's API and records POSTs."""

    def __init__(self):
        self.status = REMOTE_STATUS
        self.posts = []
        remote = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, body):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/status":
                    self._json(remote.status)
                elif self.path == "/api/config":
                    self._json({"server_name": "From Config"})
                elif self.path == "/api/media":
                    self._json({"clips": ["AMB", "TEST_RED"]})
                else:
                    self.send_error(404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                remote.posts.append((self.path, body))
                self._json({"ok": True, "message": "done"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def remote():
    r = FakeRemote()
    yield r
    r.close()


@pytest.fixture
def offline_url():
    return f"http://127.0.0.1:{free_port()}"


def test_remotes_online_and_offline(client, write_config, remote, offline_url):
    write_config({"remote_controllers": [{"url": remote.url + "/"}, {"url": offline_url}]})

    online, offline = client.get("/api/remotes").json()["remotes"]

    assert online == {
        "idx": 0,
        "url": remote.url,
        "display_name": "Studio B",
        "online": True,
        "status": REMOTE_STATUS,
    }
    assert offline == {
        "idx": 1,
        "url": offline_url,
        "display_name": offline_url,
        "online": False,
        "status": None,
    }


def test_remote_name_falls_back_to_config_then_cache(client, write_config, remote):
    remote.status = {**REMOTE_STATUS, "server_name": ""}
    write_config({"remote_controllers": [{"url": remote.url}]})
    assert client.get("/api/remotes").json()["remotes"][0]["display_name"] == "From Config"

    remote.close()  # now offline: the cached name is still shown
    assert client.get("/api/remotes").json()["remotes"][0] == {
        "idx": 0,
        "url": remote.url,
        "display_name": "From Config",
        "online": False,
        "status": None,
    }


def test_no_remotes(client, write_config):
    write_config({"remote_controllers": []})
    assert client.get("/api/remotes").json() == {"remotes": []}


def test_remote_media_proxy(client, write_config, remote, offline_url):
    write_config({"remote_controllers": [{"url": remote.url}, {"url": offline_url}]})
    assert client.get("/api/remote/0/media").json() == {"clips": ["AMB", "TEST_RED"]}
    assert client.get("/api/remote/1/media").json() == {
        "clips": [],
        "error": "Could not reach remote",
    }
    assert client.get("/api/remote/5/media").status_code == 404


def test_remote_action_proxy(client, write_config, remote, offline_url):
    write_config({"remote_controllers": [{"url": remote.url}, {"url": offline_url}]})

    r = client.post("/api/remote/0/proxy", json={"path": "/api/server/start"})
    assert r.json() == {"ok": True, "message": "done"}
    client.post(
        "/api/remote/0/proxy", json={"path": "/api/instance/1/amcp", "body": {"command": "CLEAR 1"}}
    )
    assert remote.posts == [
        ("/api/server/start", None),
        ("/api/instance/1/amcp", {"command": "CLEAR 1"}),
    ]

    assert client.post("/api/remote/1/proxy", json={"path": "/api/server/stop"}).status_code == 502
    assert client.post("/api/remote/9/proxy", json={"path": "/x"}).status_code == 404


def test_remote_connection_test(client, remote, offline_url):
    assert client.post("/api/remote/test", json={"url": remote.url}).json() == {
        "online": True,
        "server_name": "Studio B",
        "version": "1.1.1",
    }
    assert client.post("/api/remote/test", json={"url": offline_url}).json() == {"online": False}

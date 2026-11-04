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


REMOTE_KEY = "remote-key"


class FakeRemote:
    """Answers like another controller's API, checks its key and records POSTs."""

    def __init__(self):
        self.status = REMOTE_STATUS
        self.posts = []
        self.keys_seen = []
        self.redirect_to = None  # set to a URL to answer every GET with a redirect there
        remote = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, body, status=200):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorised(self):
                remote.keys_seen.append(self.headers.get("X-API-Key"))
                if remote.keys_seen[-1] == REMOTE_KEY:
                    return True
                self._json({"detail": "Invalid or missing API key"}, status=401)
                return False

            def do_GET(self):
                if not self._authorised():
                    return
                if remote.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", remote.redirect_to + self.path)
                    self.end_headers()
                elif self.path == "/api/status":
                    self._json(remote.status)
                elif self.path == "/api/config":
                    self._json({"server_name": "From Config"})
                elif self.path == "/api/media":
                    self._json({"clips": ["AMB", "TEST_RED"]})
                else:
                    self.send_error(404)

            def do_POST(self):
                if not self._authorised():
                    return
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


@pytest.fixture
def remotes_config(write_config, remote, offline_url):
    """Remote 0 is the fake (right key), remote 1 is unreachable."""
    return write_config(
        {
            "remote_controllers": [
                {"url": remote.url + "/", "api_key": REMOTE_KEY},
                {"url": offline_url, "api_key": REMOTE_KEY},
            ]
        }
    )


def test_remotes_online_and_offline(client, remotes_config, remote, offline_url):
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
    assert set(remote.keys_seen) == {REMOTE_KEY}


def test_remote_with_wrong_key(client, write_config, remote):
    write_config({"remote_controllers": [{"url": remote.url, "api_key": "wrong"}]})
    assert client.get("/api/remotes").json()["remotes"][0] == {
        "idx": 0,
        "url": remote.url,
        "display_name": remote.url,
        "online": True,
        "status": None,
        "auth_error": True,
    }


def test_remote_name_falls_back_to_config_then_cache(client, write_config, remote):
    remote.status = {**REMOTE_STATUS, "server_name": ""}
    write_config({"remote_controllers": [{"url": remote.url, "api_key": REMOTE_KEY}]})
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


def test_remote_media_proxy(client, remotes_config):
    assert client.get("/api/remote/0/media").json() == {"clips": ["AMB", "TEST_RED"]}
    assert client.get("/api/remote/1/media").json() == {
        "clips": [],
        "error": "Could not reach remote",
    }
    assert client.get("/api/remote/5/media").status_code == 404


def test_remote_action_proxy(client, remotes_config, remote):
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


@pytest.mark.parametrize("path", ["@evil.example/x", "//evil.example/x", "\\\\evil/x", "api/x"])
def test_remote_proxy_rejects_paths_that_change_host(client, remotes_config, remote, path):
    r = client.post("/api/remote/0/proxy", json={"path": path})
    assert (r.status_code, r.json()["detail"]) == (400, "Invalid remote path")
    assert remote.posts == []


def test_remote_proxy_reports_rejected_key_as_502(client, write_config, remote):
    write_config({"remote_controllers": [{"url": remote.url, "api_key": "wrong"}]})
    r = client.post("/api/remote/0/proxy", json={"path": "/api/server/start"})
    assert (r.status_code, r.json()["detail"]) == (502, "Remote rejected its API key")


def test_remote_key_not_forwarded_on_redirect(client, write_config, remote):
    elsewhere = FakeRemote()
    remote.redirect_to = elsewhere.url
    write_config({"remote_controllers": [{"url": remote.url, "api_key": REMOTE_KEY}]})
    try:
        assert client.get("/api/remotes").json()["remotes"][0]["online"] is False
        assert elsewhere.keys_seen == []
    finally:
        elsewhere.close()


def test_remote_connection_test(client, remote, offline_url):
    def test(url, key=REMOTE_KEY):
        return client.post("/api/remote/test", json={"url": url, "api_key": key}).json()

    assert test(remote.url) == {
        "online": True,
        "authorised": True,
        "server_name": "Studio B",
        "version": "1.1.1",
    }
    assert test(remote.url, "wrong") == {"online": True, "authorised": False}
    assert test(offline_url) == {"online": False}


def test_remote_connection_test_uses_saved_key_when_blank(client, write_config, remote):
    write_config({"remote_controllers": [{"url": remote.url + "/", "api_key": REMOTE_KEY}]})
    r = client.post("/api/remote/test", json={"url": remote.url, "api_key": ""})
    assert r.json()["authorised"] is True

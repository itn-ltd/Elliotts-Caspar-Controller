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
                if self.path == "/api/health":  # open, like the real one
                    return self._json({"ok": True, "version": "1.1.1", "server_name": "Studio B"})
                if not self._authorised():
                    return
                if remote.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", remote.redirect_to + self.path)
                    self.end_headers()
                elif self.path == "/api/status":
                    self._json(remote.status)
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


def test_remote_name_is_cached_for_when_it_goes_offline(client, write_config, remote):
    write_config({"remote_controllers": [{"url": remote.url, "api_key": REMOTE_KEY}]})
    assert client.get("/api/remotes").json()["remotes"][0]["display_name"] == "Studio B"

    remote.close()  # now offline: the cached name is still shown
    assert client.get("/api/remotes").json()["remotes"][0] == {
        "idx": 0,
        "url": remote.url,
        "display_name": "Studio B",
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


def test_remote_actions(client, remotes_config, remote):
    assert client.post("/api/remote/0/server/start").json() == {"ok": True, "message": "done"}
    client.post("/api/remote/0/server/stop")
    client.post("/api/remote/0/instance/1/restart")
    client.post("/api/remote/0/instance/1/stop")
    client.post("/api/remote/0/instance/1/amcp", json={"command": "CLEAR 1"})
    assert remote.posts == [
        ("/api/server/start", None),
        ("/api/server/stop", None),
        ("/api/instance/1/restart", None),
        ("/api/instance/1/stop", None),
        ("/api/instance/1/amcp", {"command": "CLEAR 1"}),
    ]
    assert client.post("/api/remote/1/server/stop").status_code == 502  # offline
    assert client.post("/api/remote/9/server/stop").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/remote/0/server/restart-pc",  # only start and stop
        "/api/remote/0/instance/1/delete",
        "/api/remote/0/instance/x/stop",
        "/api/remote/0/proxy",  # the old generic proxy is gone
        "/api/remote/test",  # and the arbitrary-URL test
    ],
)
def test_only_fixed_remote_actions_exist(client, remotes_config, remote, path):
    body = {"path": "/api/config", "url": "http://elsewhere"}
    assert client.post(path, json=body).status_code in (404, 422)
    assert remote.posts == []


def test_remote_amcp_command_is_checked_here(client, remotes_config, remote):
    r = client.post("/api/remote/0/instance/1/amcp", json={"command": "CLEAR 1\r\nKILL"})
    assert r.status_code == 400
    assert remote.posts == []


def test_remote_rejected_key_is_502(client, write_config, remote):
    write_config({"remote_controllers": [{"url": remote.url, "api_key": "wrong"}]})
    r = client.post("/api/remote/0/server/start")
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


def test_saved_remote_test_uses_health_and_its_key(client, write_config, remote, offline_url):
    write_config(
        {
            "remote_controllers": [
                {"url": remote.url, "api_key": REMOTE_KEY},
                {"url": remote.url, "api_key": "wrong"},
                {"url": offline_url, "api_key": REMOTE_KEY},
            ]
        }
    )
    assert client.post("/api/remote/0/test").json() == {
        "online": True,
        "authorised": True,
        "server_name": "Studio B",
        "version": "1.1.1",
    }
    assert client.post("/api/remote/1/test").json()["authorised"] is False
    assert client.post("/api/remote/2/test").json() == {"online": False}
    assert client.post("/api/remote/9/test").status_code == 404

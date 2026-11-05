"""The trigger-only key and the addresses allowed to use the keys (#25)."""

import pytest
from conftest import API_KEY, LOCAL, instance
from fake_amcp import FakeAMCPServer
from fastapi.testclient import TestClient

from caspar_controller import log, settings
from caspar_controller.api import auth as api_auth
from caspar_controller.api.app import app

AUTOMATOR = ("10.20.30.40", 50000)


@pytest.fixture(autouse=True)
def fresh_lookups(monkeypatch):
    monkeypatch.setattr(api_auth, "_resolved", {"entries": None, "at": 0.0, "networks": []})


@pytest.fixture
def clips(write_config):
    with FakeAMCPServer() as s:
        write_config({"instances": [instance(1, "Clipplayer", "media", amcp_port=s.port)]})
        yield s


@pytest.fixture
def trigger_key(clips):
    return settings.load()["trigger_key"]


def device(address=AUTOMATOR, key=None) -> TestClient:
    """Another machine on the network, sending a key if given."""
    return TestClient(app, client=address, headers={"X-API-Key": key} if key else {})


# ---------------------------------------------------------------- trigger key


def test_trigger_key_fires_triggers(clips, trigger_key):
    automator = device(key=trigger_key)
    assert (
        automator.get("/api/load", params={"instance": "Clipplayer", "clip": "AMB"}).status_code
        == 200
    )
    assert automator.get("/api/instance/1/load", params={"clip": "AMB"}).status_code == 200
    assert (
        automator.get(
            f"/api/load?instance=Clipplayer&clip=AMB&api_key={trigger_key}", headers={}
        ).status_code
        == 200
    )
    assert len(clips.commands) == 3


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/config"),
        ("POST", "/api/instance/1/amcp"),
        ("POST", "/api/server/stop"),
        ("GET", "/api/status"),
        ("GET", "/api/events"),
    ],
)
def test_trigger_key_can_do_nothing_else(clips, trigger_key, method, path):
    r = device(key=trigger_key).request(method, path, json={"command": "CLEAR 1"})
    assert (r.status_code, r.json()["detail"]) == (403, "The trigger key can only fire triggers")
    assert clips.commands == []


def test_trigger_key_never_reaches_account_routes(clips, trigger_key):
    assert device(key=trigger_key).get("/api/key").status_code == 401


def test_refusals_are_logged_without_the_key(clips, trigger_key):
    device(key=trigger_key).get("/api/config")
    assert log._log[-1].endswith(
        "Refused /api/config from 10.20.30.40: the trigger key can only fire triggers"
    )
    assert trigger_key not in "".join(log._log)


def test_trigger_key_rotates_on_its_own(client, clips, trigger_key):
    new = client.post("/api/key/rotate?key=trigger").json()["trigger_key"]
    assert new != trigger_key
    assert settings.load()["api_key"] == API_KEY  # the full key is untouched
    params = {"instance": "Clipplayer", "clip": "AMB"}
    assert device(key=trigger_key).get("/api/load", params=params).status_code == 401
    assert device(key=new).get("/api/load", params=params).status_code == 200


def test_trigger_key_is_a_secret(client, clips, trigger_key):
    assert trigger_key not in client.get("/api/config").text
    assert trigger_key not in client.get("/api/config/export").text
    client.post("/api/config/import", json={"trigger_key": "from-elsewhere"})
    assert settings.load()["trigger_key"] == trigger_key


# ---------------------------------------------------------------- allowed addresses


@pytest.mark.parametrize("entry", ["10.20.30.40", "10.20.30.0/24"])
def test_listed_address_may_use_the_keys(client, clips, trigger_key, entry):
    client.post("/api/config", json={"api_clients": [entry]})
    params = {"instance": "Clipplayer", "clip": "AMB"}
    assert device(key=trigger_key).get("/api/load", params=params).status_code == 200
    assert device(key=API_KEY).get("/api/config").status_code == 200


def test_unlisted_address_is_refused_with_either_key(client, clips, trigger_key):
    client.post("/api/config", json={"api_clients": ["10.99.0.1"]})
    for key in (trigger_key, API_KEY):
        r = device(key=key).get("/api/load", params={"instance": "Clipplayer", "clip": "AMB"})
        assert (r.status_code, r.json()["detail"]) == (
            403,
            "This address isn't allowed to use the API keys",
        )
    assert clips.commands == []


def test_signed_in_browser_is_not_affected(client, write_config):
    write_config({"api_clients": ["10.99.0.1"]})
    settings.set_password(cfg := settings.load(), "pw")
    settings.save(cfg)
    browser = device()
    assert browser.post("/login", json={"password": "pw"}).status_code == 200
    assert browser.get("/api/config").status_code == 200


def test_empty_list_lets_any_address_use_the_full_key(clips):
    assert device(key=API_KEY).get("/api/config").status_code == 200


def test_host_names_are_looked_up_at_most_every_5_minutes(client, clips, monkeypatch):
    lookups = []

    def getaddrinfo(host, port):
        lookups.append(host)
        return [(2, 1, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(api_auth.socket, "getaddrinfo", getaddrinfo)
    client.post("/api/config", json={"api_clients": ["automator.example"]})
    local_key = device(LOCAL, API_KEY)  # 127.0.0.1, but not a trusted localhost browser
    for _ in range(3):
        assert local_key.get("/api/status").status_code == 200
    assert lookups == ["automator.example"]
    assert device(key=API_KEY).get("/api/status").status_code == 403


@pytest.mark.parametrize("bad", ["not a host!", "10.0.0.0/99", "-x.example"])
def test_bad_entries_are_rejected(client, bad):
    assert client.post("/api/config", json={"api_clients": [bad]}).status_code == 400

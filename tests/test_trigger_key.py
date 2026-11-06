"""The trigger-only key and the addresses allowed to use the keys (#25)."""

import types

import pytest
from conftest import API_KEY, LOCAL, instance
from fake_amcp import FakeAMCPServer
from fastapi.testclient import TestClient

from caspar_controller import clients, log, settings
from caspar_controller.api.app import app

AUTOMATOR = ("10.20.30.40", 50000)


@pytest.fixture(autouse=True)
def fresh_lookups(monkeypatch):
    monkeypatch.setattr(clients, "_cache", {"entries": None, "at": 0.0, "names": {}})


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
        "Refused /api/config from 10.20.30.40: The trigger key can only fire triggers"
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


@pytest.fixture
def dns(monkeypatch):
    """Host-name lookups, recorded; set .answer to an address, or None to fail."""
    state = types.SimpleNamespace(lookups=[], answer="127.0.0.1")

    def getaddrinfo(host, port):
        state.lookups.append(host)
        if state.answer is None:
            raise OSError("no such host")
        return [(2, 1, 6, "", (state.answer, 0))]

    monkeypatch.setattr(clients.socket, "getaddrinfo", getaddrinfo)
    return state


def test_host_names_are_looked_up_on_save_not_per_request(client, clips, dns):
    client.post("/api/config", json={"api_clients": ["automator.example"]})
    assert dns.lookups == ["automator.example"]  # on save
    local_key = device(LOCAL, API_KEY)  # 127.0.0.1, but not a trusted localhost browser
    for _ in range(3):
        assert local_key.get("/api/status").status_code == 200
    assert dns.lookups == ["automator.example"]  # requests only read the cache
    assert device(key=API_KEY).get("/api/status").status_code == 403


def test_a_failed_lookup_keeps_the_last_good_address(client, clips, dns):
    client.post("/api/config", json={"api_clients": ["automator.example"]})
    dns.answer = None  # DNS down at the next refresh
    clients.resolve(["automator.example"])
    assert device(LOCAL, API_KEY).get("/api/status").status_code == 200
    assert "couldn't look up automator.example (keeping its last known address)" in log._log[-1]


def test_ipv4_mapped_addresses_match():
    assert clients.allowed("::ffff:10.20.30.40", ["10.20.30.0/24"])


def test_forwarded_for_header_is_not_trusted():
    import inspect

    from caspar_controller import tray

    assert "proxy_headers=False" in inspect.getsource(tray.run)


def test_wrong_keys_are_logged(clips):
    device(key="guess").get("/api/config")
    assert log._log[-1].endswith("Refused /api/config from 10.20.30.40: Invalid or missing API key")


def test_import_keeps_this_pcs_allowed_addresses(client, clips):
    client.post("/api/config", json={"api_clients": ["10.20.30.40"]})
    client.post("/api/config/import", json={"server_name": "Other gallery"})  # no api_clients
    assert settings.load()["api_clients"] == ["10.20.30.40"]
    client.post("/api/config/import", json={"api_clients": ["10.99.0.1"]})  # another gallery's
    assert settings.load()["api_clients"] == ["10.20.30.40"]


@pytest.mark.parametrize(
    "bad", ["not a host!", "10.0.0.0/99", "-x.example", "10.0.0.5/8", "a..b", "x-.y", "10.0.5"]
)
def test_bad_entries_are_rejected(client, bad):
    assert client.post("/api/config", json={"api_clients": [bad]}).status_code == 400


# ---------------------------------------------------------------- v1.x changeover (#12)


def changeover(client, addresses: list) -> None:
    assert (
        client.post("/api/config", json={"keyless_trigger_clients": addresses}).status_code == 200
    )


def test_listed_addresses_fire_triggers_with_no_key(client, clips):
    changeover(client, ["10.20.30.0/24"])
    assert (
        device().get("/api/load", params={"instance": "Clipplayer", "clip": "AMB"}).status_code
        == 200
    )
    assert device().get("/api/instance/1/load", params={"clip": "AMB"}).status_code == 200
    assert len(clips.commands) == 2
    warning = "Trigger from 10.20.30.40 with no key, allowed for the v1.x changeover."
    assert sum(line.endswith(warning) for line in log._log) == 2  # one per request


@pytest.mark.parametrize(
    ("address", "path"),
    [(AUTOMATOR, "/api/config"), (("10.99.0.1", 50000), "/api/load")],
    ids=["not a trigger", "not listed"],
)
def test_only_triggers_from_listed_addresses(client, clips, address, path):
    changeover(client, ["10.20.30.40"])
    assert (
        device(address).get(path, params={"instance": "Clipplayer", "clip": "AMB"}).status_code
        == 401
    )


def test_off_by_default(clips):
    assert settings.load()["keyless_trigger_clients"] == []
    assert (
        device().get("/api/load", params={"instance": "Clipplayer", "clip": "AMB"}).status_code
        == 401
    )


@pytest.mark.parametrize("bad", ["automator.example", "10.0.5", "10.0.0.5/8"])
def test_changeover_takes_addresses_only(client, bad):
    assert client.post("/api/config", json={"keyless_trigger_clients": [bad]}).status_code == 400


def test_import_keeps_this_pcs_changeover_list(client, clips):
    changeover(client, ["10.20.30.40"])
    client.post("/api/config/import", json={"keyless_trigger_clients": ["10.0.0.0/8"]})
    assert settings.load()["keyless_trigger_clients"] == ["10.20.30.40"]

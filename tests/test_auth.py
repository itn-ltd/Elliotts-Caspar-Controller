"""Access control: API key, password login, first-run setup and secrets (#2)."""

import json

import pytest
from conftest import API_KEY, LOCAL, LOCAL_URL, instance
from fake_amcp import FakeAMCPServer
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from caspar_controller import __version__, settings
from caspar_controller.api.app import ROUTERS, app
from caspar_controller.auth import hash_password, key_matches, verify_password

OPEN_ROUTES = {"/api/health"}
SESSION_ONLY_ROUTES = {"/api/password", "/api/key", "/api/key/rotate"}
API_ROUTES = [
    (method, route.path.replace("{inst_id}", "1").replace("{idx}", "0"))
    for route in [*app.routes, *(r for router in ROUTERS for r in router.routes)]
    if isinstance(route, APIRoute) and route.path.startswith("/api/")
    if route.path not in OPEN_ROUTES
    for method in route.methods
]


@pytest.fixture
def password_on(write_config):
    return write_config({"web_password_hash": hash_password("hunter2")})


def login(client) -> str:
    return client.post("/login", json={"password": "hunter2"}).cookies["session"]


# ---------------------------------------------------------------- API


def test_api_routes_found():
    assert len(API_ROUTES) > 20


@pytest.mark.parametrize(("method", "path"), API_ROUTES)
def test_every_api_route_needs_credentials(anon, password_on, method, path):
    r = anon.request(method, path)
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or missing API key"}


@pytest.mark.parametrize(("method", "path"), API_ROUTES)
def test_every_api_route_accepts_a_session(anon, password_on, inline_threads, method, path):
    login(anon)
    assert anon.request(method, path).status_code not in (401, 403)


@pytest.mark.parametrize(("method", "path"), API_ROUTES)
def test_api_key_works_except_on_account_routes(anon, password_on, inline_threads, method, path):
    r = anon.request(method, path, headers={"X-API-Key": API_KEY})
    assert (r.status_code == 401) == (path in SESSION_ONLY_ROUTES)


@pytest.mark.parametrize(
    "auth",
    [
        {"headers": {"X-API-Key": API_KEY}},
        {"params": {"api_key": API_KEY}},
    ],
)
def test_api_key_as_header_or_query(anon, password_on, auth):
    assert anon.get("/api/config", **auth).status_code == 200


def test_api_session_works(anon, password_on):
    login(anon)
    assert anon.get("/api/config").status_code == 200


@pytest.mark.parametrize("key", ["wrong", "", "ü-non-ascii"])
def test_bad_api_key_is_401(anon, password_on, key):
    # Query string: httpx won't send non-ASCII headers, and URLs are where odd keys arrive.
    assert anon.get("/api/config", params={"api_key": key}).status_code == 401


def test_health_is_open(anon, password_on):
    assert anon.get("/api/health").json() == {"ok": True, "version": __version__}
    assert anon.get("/api/healthz").status_code == 401  # exact match only


def test_trigger_url_with_key(anon, write_config):
    with FakeAMCPServer() as s:
        write_config(
            {
                "web_password_hash": hash_password("hunter2"),
                "instances": [instance(1, "Clipplayer", "media", amcp_port=s.port)],
            }
        )
        params = {"instance": "Clipplayer", "clip": "AMB"}
        assert anon.get("/api/load", params=params).status_code == 401
        assert anon.get("/api/load", params={**params, "api_key": API_KEY}).status_code == 200
        assert s.commands == ['PLAY 1-1 "AMB" LOOP']


def test_rotated_key_replaces_old(anon, password_on):
    login(anon)
    new = anon.post("/api/key/rotate").json()["api_key"]
    anon.cookies.clear()
    assert new != API_KEY
    assert anon.get("/api/config", headers={"X-API-Key": API_KEY}).status_code == 401
    assert anon.get("/api/config", headers={"X-API-Key": new}).status_code == 200


def test_key_endpoint(client, write_config):
    write_config({})
    assert client.get("/api/key").json() == {"api_key": API_KEY}


# ---------------------------------------------------------------- pages


def test_pages_redirect_to_login(anon, password_on):
    r = anon.get("/settings", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/login"


def test_this_pc_must_also_sign_in_once_password_set(local_anon, password_on):
    assert local_anon.get("/", follow_redirects=False).status_code == 307
    assert local_anon.get("/api/config").status_code == 401


def test_first_run_other_devices_see_setup_page(anon, write_config):
    write_config({})
    r = anon.get("/", follow_redirects=False)
    assert r.status_code == 403
    assert "No web UI password is set yet" in r.text
    assert anon.get("/api/config").status_code == 401


def test_first_run_this_pc_has_access(local_anon, write_config):
    write_config({})
    assert local_anon.get("/").status_code == 200
    assert local_anon.get("/api/config").status_code == 200
    assert local_anon.get("/", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


@pytest.mark.parametrize(
    ("base_url", "headers"),
    [
        ("http://evil.example:5280", {}),  # DNS rebinding: local socket, foreign Host
        (LOCAL_URL, {"Sec-Fetch-Site": "cross-site"}),  # another site's page (CSRF)
        (LOCAL_URL, {"Sec-Fetch-Site": "same-site"}),
    ],
)
def test_first_run_trust_needs_a_local_same_origin_request(write_config, base_url, headers):
    write_config({})
    browser = TestClient(app, base_url=base_url, client=LOCAL)
    assert browser.get("/api/key", headers=headers).status_code == 401
    assert browser.get("/", headers=headers, follow_redirects=False).status_code == 403


def test_login_and_static_are_open(anon, password_on):
    assert anon.get("/login").status_code == 200
    assert anon.get("/static/esc_icon.png").status_code == 200


def test_static_cannot_read_outside_static(anon, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    assert anon.get("/static/" + secret.as_posix()).status_code == 404
    assert anon.get("/static/%2e%2e/pyproject.toml").status_code == 404  # httpx collapses plain ../


# ---------------------------------------------------------------- login


def test_login_sets_strict_http_only_cookie(anon, password_on):
    r = anon.post("/login", json={"password": "hunter2"})
    assert r.json() == {"ok": True}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie


def test_wrong_password_is_401(anon, password_on):
    r = anon.post("/login", json={"password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect password"


def test_login_before_password_set_is_400(anon, write_config):
    write_config({})
    assert anon.post("/login", json={"password": "x"}).status_code == 400


def test_lone_surrogate_password_is_rejected_not_500(anon, password_on):
    r = anon.post(
        "/login", content='{"password": "\\ud800"}', headers={"content-type": "application/json"}
    )
    assert r.status_code == 401


def test_logout_revokes_session(anon, password_on):
    token = login(anon)
    assert anon.get("/", follow_redirects=False).status_code == 200
    anon.post("/logout")
    # Replay the old token: logout must revoke it on the server, not just clear the cookie.
    anon.cookies.set("session", token)
    assert anon.get("/", follow_redirects=False).status_code == 307


# ---------------------------------------------------------------- secrets


def test_password_saved_as_hash_only(local_anon, write_config, config_dir):
    write_config({})  # first run: this PC sets the first password
    assert local_anon.post("/api/password", json={"password": "s3cret!"}).json() == {"ok": True}
    raw = (config_dir / "elliotts_caspar_config.json").read_text()
    assert "s3cret!" not in raw
    assert verify_password("s3cret!", json.loads(raw)["web_password_hash"])


@pytest.mark.parametrize(
    ("password", "detail"),
    [("  ", "Password can't be empty"), ("hunter2", "Choose a different password")],
)
def test_bad_new_password_rejected(anon, password_on, password, detail):
    login(anon)
    r = anon.post("/api/password", json={"password": password, "current_password": "hunter2"})
    assert (r.status_code, r.json()["detail"]) == (400, detail)


@pytest.mark.parametrize("current", ["", "wrong"])
def test_changing_password_needs_the_current_one(anon, password_on, current):
    login(anon)
    r = anon.post("/api/password", json={"password": "new-pw", "current_password": current})
    assert (r.status_code, r.json()["detail"]) == (400, "Current password is incorrect")


def test_changing_password_signs_out_other_sessions(password_on):
    me, other = TestClient(app), TestClient(app)
    login(me)
    login(other)
    r = me.post("/api/password", json={"password": "new-pw", "current_password": "hunter2"})
    assert r.json() == {"ok": True}
    assert me.get("/", follow_redirects=False).status_code == 200
    assert other.get("/", follow_redirects=False).status_code == 307


def test_config_api_no_longer_sets_password(client, password_on, config_dir):
    client.post("/api/config", json={"web_password": "ignored"})
    raw = json.loads((config_dir / "elliotts_caspar_config.json").read_text())
    assert verify_password("hunter2", raw["web_password_hash"])


# ---------------------------------------------------------------- initial password (env var)


@pytest.fixture
def initial_password(monkeypatch):
    monkeypatch.setenv(settings.INITIAL_PASSWORD_ENV, "intune-default")


def test_initial_password_seeds_first_run(initial_password):
    cfg = settings.load()
    assert verify_password("intune-default", cfg["web_password_hash"])
    assert cfg["password_change_required"] is True


def test_initial_password_never_overwrites(initial_password, password_on):
    cfg = settings.load()
    assert verify_password("hunter2", cfg["web_password_hash"])
    assert cfg["password_change_required"] is False


def test_blank_initial_password_is_ignored(monkeypatch):
    monkeypatch.setenv(settings.INITIAL_PASSWORD_ENV, "  ")
    assert settings.load()["web_password_hash"] == ""


def test_must_change_initial_password_on_first_sign_in(anon, write_config, initial_password):
    write_config({})
    assert anon.post("/login", json={"password": "intune-default"}).json() == {"ok": True}

    # Everything except changing the password is blocked for this session.
    assert anon.get("/", follow_redirects=False).headers["location"] == "/change-password"
    assert anon.get("/api/config").status_code == 403
    assert anon.get("/change-password").status_code == 200

    r = anon.post("/api/password", json={"password": "intune-default"})
    assert r.json()["detail"] == "Choose a different password"
    assert anon.post("/api/password", json={"password": "my-own"}).json() == {"ok": True}

    assert anon.get("/", follow_redirects=False).status_code == 200
    assert anon.get("/api/config").json()["password_change_required"] is False


def test_api_key_unaffected_by_pending_change(anon, write_config, initial_password):
    write_config({})
    settings.load()  # seed the password
    assert anon.get("/api/config", headers={"X-API-Key": API_KEY}).status_code == 200


def test_import_cannot_clear_pending_change(client, write_config, initial_password, config_dir):
    write_config({})
    settings.load()
    client.post("/api/config/import", json={"password_change_required": False})
    assert settings.load()["password_change_required"] is True


def test_config_and_export_never_contain_secrets(client, write_config):
    write_config(
        {
            "web_password_hash": hash_password("hunter2"),
            "remote_controllers": [{"url": "http://r:5280", "api_key": "remote-secret"}],
        }
    )
    cfg = client.get("/api/config").json()
    exported = client.get("/api/config/export").json()
    for view in (cfg, exported):
        text = json.dumps(view)
        assert API_KEY not in text and "remote-secret" not in text
        assert "web_password_hash" not in view and "web_password" not in view
        assert view["remote_controllers"] == [{"url": "http://r:5280", "api_key_set": True}]
    assert cfg["password_set"] is True


def test_blank_remote_key_keeps_the_saved_one(client, write_config, config_dir):
    write_config({"remote_controllers": [{"url": "http://r:5280/", "api_key": "remote-secret"}]})
    client.post(
        "/api/config", json={"remote_controllers": [{"url": "http://r:5280", "api_key": ""}]}
    )
    client.post("/api/config/import", json={"remote_controllers": [{"url": "http://r:5280"}]})
    saved = json.loads((config_dir / "elliotts_caspar_config.json").read_text())
    assert saved["remote_controllers"] == [{"url": "http://r:5280", "api_key": "remote-secret"}]


def test_load_survives_a_read_only_config_dir(monkeypatch):
    def read_only(_config):
        raise PermissionError("read-only")

    monkeypatch.setattr(settings, "save", read_only)
    assert settings.load()["api_key"]  # generated, just not saved


@pytest.mark.parametrize(("enabled", "hashed"), [(True, True), (False, False)])
def test_v1_plaintext_password_is_hashed_on_load(write_config, config_dir, enabled, hashed):
    write_config({"web_password": "old-pw", "web_password_enabled": enabled})
    cfg = settings.load()
    raw = (config_dir / "elliotts_caspar_config.json").read_text()
    assert "old-pw" not in raw
    assert "web_password" not in cfg and "web_password_enabled" not in cfg
    assert verify_password("old-pw", cfg["web_password_hash"]) is hashed


def test_api_key_generated_once_and_kept():
    first = settings.load()["api_key"]
    assert first
    assert settings.load()["api_key"] == first


# ---------------------------------------------------------------- auth helpers


def test_hash_and_verify():
    stored = hash_password("pw")
    assert stored.startswith("scrypt$")
    assert stored != hash_password("pw")  # salted
    assert verify_password("pw", stored)
    assert not verify_password("PW", stored)


@pytest.mark.parametrize("stored", ["", "plain", "md5$aa$bb", "scrypt$zz$00"])
def test_verify_rejects_malformed_hashes(stored):
    assert verify_password("pw", stored) is False


@pytest.mark.parametrize(
    ("supplied", "expected", "ok"),
    [("k", "k", True), ("k", "x", False), (None, "k", False), ("", "", False), ("é", "k", False)],
)
def test_key_matches(supplied, expected, ok):
    assert key_matches(supplied, expected) is ok

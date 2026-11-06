"""Guessing is throttled and sessions expire on the server (#22)."""

import types

import pytest
from conftest import API_KEY, instance
from fastapi.testclient import TestClient

from caspar_controller import log, settings, throttle
from caspar_controller.api import auth as api_auth
from caspar_controller.api.app import app

ATTACKER = ("10.66.0.9", 50000)
OPERATOR = ("10.66.0.10", 50000)


@pytest.fixture
def clock(monkeypatch):
    """One fake clock for the throttle (monotonic) and sessions (wall time)."""
    now = [1000.0]
    fake = types.SimpleNamespace(monotonic=lambda: now[0], time=lambda: now[0])
    monkeypatch.setattr(throttle, "time", fake)
    monkeypatch.setattr(api_auth, "time", fake)
    return now


@pytest.fixture
def password(write_config):
    write_config({"instances": [instance(1, "GFX1")]})
    cfg = settings.load()
    settings.set_password(cfg, "right")
    settings.save(cfg)


def device(address, **kwargs) -> TestClient:
    return TestClient(app, client=address, **kwargs)


def sign_in(client: TestClient, pw: str) -> int:
    return client.post("/login", json={"password": pw}).status_code


# ---------------------------------------------------------------- sign-in


def test_wrong_passwords_are_throttled_per_address(password, clock):
    attacker = device(ATTACKER)
    assert [sign_in(attacker, "guess") for _ in range(5)] == [401] * 5
    r = attacker.post("/login", json={"password": "right"})  # even the right one, while blocked
    assert (r.status_code, r.headers["retry-after"]) == (429, "60")
    assert r.json()["detail"] == "Too many wrong passwords: try again in 60 s"
    assert sign_in(device(OPERATOR), "right") == 200  # other addresses aren't affected


def test_the_block_lifts_after_a_minute(password, clock):
    attacker = device(ATTACKER)
    for _ in range(5):
        sign_in(attacker, "guess")
    clock[0] += 61
    assert sign_in(attacker, "right") == 200


def test_failures_spread_out_over_time_are_not_throttled(password, clock):
    attacker = device(ATTACKER)
    for _ in range(8):
        assert sign_in(attacker, "guess") == 401
        clock[0] += 20  # 3 a minute: under the limit


def test_a_good_sign_in_resets_the_count(password, clock):
    operator = device(OPERATOR)
    for _ in range(4):
        sign_in(operator, "typo")
    assert sign_in(operator, "right") == 200
    assert sign_in(operator, "typo") == 401  # counting from zero again, not blocked


def test_a_block_is_logged_once(password, clock):
    attacker = device(ATTACKER)
    for _ in range(9):
        sign_in(attacker, "guess")
    blocks = [line for line in log._log if "Too many wrong passwords" in line]
    assert len(blocks) == 1 and blocks[0].endswith("from 10.66.0.9: refusing it for a minute.")


def test_wrong_current_password_on_change_is_throttled(password, clock):
    operator = device(OPERATOR)
    sign_in(operator, "right")
    codes = [
        operator.post(
            "/api/password", json={"password": "new", "current_password": "guess"}
        ).status_code
        for _ in range(6)
    ]
    assert codes == [400] * 5 + [429]


# ---------------------------------------------------------------- API keys


def test_wrong_api_keys_are_throttled_even_for_the_right_key(password, clock):
    attacker = device(ATTACKER)
    codes = [
        attacker.get("/api/status", headers={"X-API-Key": f"guess{n}"}).status_code
        for n in range(5)
    ]
    assert codes == [401] * 5
    r = attacker.get("/api/status", headers={"X-API-Key": API_KEY})
    assert (r.status_code, r.json()["detail"]) == (
        429,
        "Too many wrong API keys: try again in 60 s",
    )
    assert device(OPERATOR).get("/api/status", headers={"X-API-Key": API_KEY}).status_code == 200
    assert sum("Too many wrong API keys from 10.66.0.9" in line for line in log._log) == 1


def test_no_key_at_all_isnt_counted(password, clock):
    browser = device(ATTACKER)
    for _ in range(10):
        assert browser.get("/api/status").status_code == 401
    assert browser.get("/api/status", headers={"X-API-Key": API_KEY}).status_code == 200


def test_a_valid_key_on_an_account_route_isnt_counted(password, clock):
    panel = device(OPERATOR, headers={"X-API-Key": API_KEY})
    for _ in range(10):
        assert panel.get("/api/key").status_code == 401  # session-only, not a guess
    assert panel.get("/api/status").status_code == 200


# ---------------------------------------------------------------- sessions


def test_sessions_expire_after_30_days(password, clock):
    operator = device(OPERATOR)
    sign_in(operator, "right")
    clock[0] += 29 * 86400
    assert operator.get("/api/config").status_code == 200
    clock[0] += 2 * 86400  # 31 days old: a copied token stops working too
    assert operator.get("/api/config").status_code == 401
    assert api_auth._sessions == {}  # forgotten, not just refused


def test_session_cookie_matches_the_server_limit(password):
    r = device(OPERATOR).post("/login", json={"password": "right"})
    assert f"Max-Age={api_auth.SESSION_MAX}" in r.headers["set-cookie"]

"""Pages, the config API and the web-UI password, as they behave today."""

import json

import pytest
from conftest import instance
from fastapi.testclient import TestClient

from elliotts_casper_controller import core


@pytest.fixture(autouse=True)
def no_tally(monkeypatch):
    monkeypatch.setattr(core, "_refresh_tally_monitors", lambda: None)


# ---------------------------------------------------------------- pages


@pytest.mark.parametrize(
    ("path", "heading"),
    [
        ("/", "Dashboard"),
        ("/settings", "Settings"),
        ("/http-generator", "HTTP Generator"),
        ("/login", "Password"),
    ],
)
def test_pages_render(client, path, heading):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert heading in r.text


def test_server_name_shown_in_title(client, write_config):
    write_config({"server_name": "Studio A"})
    assert "<title>Dashboard — Studio A</title>" in client.get("/").text


def test_http_generator_uses_web_port(client, write_config):
    write_config({"web_port": 5999})
    assert ":5999/api/load?instance=" in client.get("/http-generator").text


def test_static_files(client):
    r = client.get("/static/esc_icon.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert client.get("/static/missing.png").status_code == 404


# ---------------------------------------------------------------- config API


def test_get_config(client, write_config):
    write_config({"server_name": "Studio A"})
    cfg = client.get("/api/config").json()
    assert cfg["server_name"] == "Studio A"
    assert cfg["web_port"] == 5280  # defaults filled in


def test_post_config_merges_saves_and_regenerates(client, write_config, config_dir, tmp_path):
    exe = tmp_path / "caspar" / "casparcg.exe"
    exe.parent.mkdir()
    write_config(
        {
            "caspar_exe_path": str(exe),
            "server_name": "Old",
            "web_port": 6000,
            "instances": [instance(1, "GFX1")],
        }
    )

    r = client.post("/api/config", json={"server_name": "New"})

    assert r.json() == {"ok": True}
    saved = json.loads((config_dir / "elliotts_caspar_config.json").read_text())
    assert saved["server_name"] == "New"
    assert saved["web_port"] == 6000  # untouched fields kept
    assert (exe.parent / "casparcg_inst_1.config").exists()
    assert core._log[-1].endswith("Config saved and instance configs regenerated.")


def test_post_config_rejects_wrong_types(client):
    assert client.post("/api/config", json={"web_port": "abc"}).status_code == 422


def test_export_config(client, write_config):
    write_config({"server_name": "Studio A"})
    r = client.get("/api/config/export")
    assert r.headers["content-disposition"] == "attachment; filename=elliotts_caspar_config.json"
    assert r.json()["server_name"] == "Studio A"


def test_import_config_replaces_everything(client, write_config, config_dir):
    write_config({"server_name": "Old", "web_port": 6000})
    r = client.post("/api/config/import", json={"server_name": "Imported", "instances": []})
    assert r.json() == {"ok": True}
    saved = json.loads((config_dir / "elliotts_caspar_config.json").read_text())
    assert saved == {"server_name": "Imported", "instances": []}


@pytest.mark.parametrize("body", ["not json", "[1, 2]"])
def test_import_rejects_non_objects(client, body):
    r = client.post(
        "/api/config/import", content=body, headers={"content-type": "application/json"}
    )
    assert r.status_code == 400


# ---------------------------------------------------------------- web password


@pytest.fixture
def password_on(write_config):
    return write_config({"web_password": "hunter2", "web_password_enabled": True})


def test_pages_open_when_password_disabled(client, write_config):
    write_config({"web_password": "hunter2", "web_password_enabled": False})
    assert client.get("/", follow_redirects=False).status_code == 200


def test_pages_redirect_to_login(client, password_on):
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/login"


def test_login_page_and_static_are_open(client, password_on):
    assert client.get("/login").status_code == 200
    assert client.get("/static/esc_icon.png").status_code == 200


def test_wrong_password_is_401(client, password_on):
    r = client.post("/login", json={"password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect password"


def test_login_then_logout(client, password_on):
    r = client.post("/login", json={"password": "hunter2"})
    assert r.json() == {"ok": True}
    assert "session" in r.cookies
    assert client.get("/", follow_redirects=False).status_code == 200

    client.post("/logout")
    client.cookies.clear()
    assert client.get("/", follow_redirects=False).status_code == 307


def test_localhost_bypasses_password(password_on):
    local = TestClient(core.app, client=("127.0.0.1", 50000))
    assert local.get("/", follow_redirects=False).status_code == 200

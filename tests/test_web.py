"""Pages, the config API and the web-UI password, as they behave today."""

import json

import pytest
from conftest import API_KEY, instance

from caspar_controller import log

# ---------------------------------------------------------------- pages


@pytest.mark.parametrize(
    ("path", "heading"),
    [
        ("/", "Dashboard"),
        ("/settings", "Settings"),
        ("/triggers", "Triggers"),
        ("/login", "Password"),
    ],
)
def test_pages_render(client, path, heading):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert heading in r.text


def test_pages_are_static_files_and_hold_no_config(client, write_config):
    # Since #7 the server name and URLs come from the API, filled in by the page's script.
    write_config({"server_name": "Studio A", "web_port": 5999})
    page = client.get("/")
    assert "Studio A" not in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert client.get("/web/app.js").status_code == 200
    assert client.get("/web/app.css").status_code == 200


def test_trigger_base_url_uses_web_port(client, write_config):
    write_config({"web_port": 5999})
    assert client.get("/api/config").json()["lan_url"].endswith(":5999")


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
    saved = json.loads((config_dir / "config.json").read_text())
    assert saved["server_name"] == "New"
    assert saved["web_port"] == 6000  # untouched fields kept
    assert (exe.parent / "casparcg_inst_1.config").exists()
    assert log._log[-1].endswith("Config saved and instance configs regenerated.")


def test_post_config_rejects_wrong_types(client):
    # 400 from the Settings model since #6 (was FastAPI's 422); "6000" isn't coerced either.
    for value in ("abc", "6000", True):
        assert client.post("/api/config", json={"web_port": value}).status_code == 400


def test_export_config(client, write_config):
    write_config({"server_name": "Studio A"})
    r = client.get("/api/config/export")
    assert r.headers["content-disposition"] == "attachment; filename=elliotts_caspar_config.json"
    assert r.json()["server_name"] == "Studio A"


def test_import_replaces_everything_except_secrets(client, write_config, config_dir):
    write_config({"server_name": "Old", "web_port": 6000, "web_password_hash": "scrypt$aa$bb"})
    trigger_key = client.get("/api/key").json()["trigger_key"]
    r = client.post(
        "/api/config/import",
        json={"server_name": "Imported", "instances": [], "api_key": "x", "web_password_hash": "y"},
    )
    assert r.json() == {"ok": True}
    saved = json.loads((config_dir / "config.json").read_text())
    assert saved == {
        "server_name": "Imported",
        "instances": [],
        "remote_controllers": [],
        "api_key": API_KEY,
        "trigger_key": trigger_key,  # this machine's, like the API key (#25)
        "web_password_hash": "scrypt$aa$bb",
        "password_change_required": False,
    }


def test_partial_import_uses_defaults_for_missing_fields(client, tmp_path):
    r = client.post("/api/config/import", json={"instances": [instance(1, "GFX1")]})
    assert r.status_code == 200
    assert "<video-mode>1080p2500</video-mode>" in (tmp_path / "casparcg_inst_1.config").read_text()


@pytest.mark.parametrize("body", ["not json", "[1, 2]"])
def test_import_rejects_non_objects(client, body):
    r = client.post(
        "/api/config/import", content=body, headers={"content-type": "application/json"}
    )
    assert r.status_code == 400

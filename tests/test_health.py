"""/api/health for monitoring: open, with versions and states but nothing sensitive (#15)."""

import json

from conftest import API_KEY, free_port, instance
from fake_amcp import FakeAMCPServer

from caspar_controller import __version__, instances, settings


def test_health_reports_versions_and_states(anon, write_config):
    with FakeAMCPServer(version="2.3.3 Stable") as live:
        write_config(
            {
                "server_name": "Gallery",
                "instances": [
                    instance(1, "GFX1", amcp_port=live.port),
                    instance(2, "GFX2", amcp_port=free_port()),
                ],
            }
        )
        instances.manager.poll_status()
        health = anon.get("/api/health").json()  # no key, no sign-in
    assert health == {
        "ok": True,
        "version": __version__,
        "server_name": "Gallery",
        "caspar_versions": ["2.3.3 Stable"],
        "instances": [{"id": 1, "state": "live"}, {"id": 2, "state": "stopped"}],
    }


def test_health_shows_auto_restart_at_work(anon, write_config, monkeypatch):
    write_config({"instances": [instance(1, "GFX1"), instance(2, "GFX2")]})
    states = {1: "crashed (1/5)", 2: "failed"}
    monkeypatch.setattr(instances.manager, "state_of", states.get)
    assert anon.get("/api/health").json()["instances"] == [
        {"id": 1, "state": "crashed (1/5)"},
        {"id": 2, "state": "failed"},
    ]


def test_health_exposes_nothing_sensitive(anon, write_config):
    write_config(
        {
            "server_name": "Gallery",
            "media_path": "D:\\SecretMedia",
            "instances": [
                instance(
                    1,
                    "PRIVATE-NAME",
                    url="https://example.com/private-graphics",
                    ndi_name="PRIVATE NDI",
                ),
            ],
            "remote_controllers": [{"url": "http://10.9.8.7:5280", "api_key": "remote-secret-key"}],
            "api_clients": ["10.1.2.3"],
        }
    )
    cfg = settings.load()
    settings.set_password(cfg, "pw")
    settings.save(cfg)
    cfg = settings.load()
    text = json.dumps(anon.get("/api/health").json())
    for sensitive in (
        "PRIVATE-NAME",
        "PRIVATE NDI",
        "private-graphics",
        "SecretMedia",
        "10.9.8.7",
        "10.1.2.3",
        "remote-secret-key",
        API_KEY,
        cfg["trigger_key"],
        cfg["web_password_hash"],
        "5250",
    ):
        assert sensitive not in text

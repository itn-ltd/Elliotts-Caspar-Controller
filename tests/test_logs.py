"""Log files that survive a restart, and the diagnostics download (#14)."""

import io
import json
import logging
import sys
import threading
import zipfile

import pytest
from conftest import API_KEY, instance

from caspar_controller import log, settings


@pytest.fixture
def log_files(config_dir, monkeypatch):
    """write_files() into the test's home; every handler and hook it adds is undone after."""
    root = logging.getLogger()
    for target, name in ((sys, "excepthook"), (threading, "excepthook")):
        monkeypatch.setattr(target, name, getattr(target, name))
    monkeypatch.setattr(root, "level", root.level)
    before = {id(h) for h in root.handlers + log._events.handlers}

    def start():
        log.write_files(settings.logs_dir())

    yield start
    for logger in (root, log._events):
        for handler in [h for h in logger.handlers if id(h) not in before]:
            handler.close()
            logger.removeHandler(handler)


def restart(monkeypatch, start) -> None:
    """What a quit and relaunch does to the logs: memory cleared, files reopened."""
    for handler in list(log._events.handlers):
        handler.close()
        log._events.removeHandler(handler)
    monkeypatch.setattr(log, "_log", [])
    start()


def test_events_survive_a_restart(log_files, monkeypatch):
    log_files()
    log.log_event("Started all instances.")
    restart(monkeypatch, log_files)
    log.log_event("Back again.")
    lines = log.entries()
    assert lines[0].endswith("] Started all instances.") and lines[0].startswith("[20")  # dated
    assert lines[-1].endswith("] Back again.")


def test_only_the_last_events_come_back(log_files, monkeypatch):
    log_files()
    for n in range(log.MAX_LOG + 50):
        log.log_event(f"event {n}")
    restart(monkeypatch, log_files)
    lines = log.entries()
    assert len(lines) == log.MAX_LOG and lines[-1].endswith(f"event {log.MAX_LOG + 49}")


def test_the_app_log_and_uncaught_errors_go_to_a_file(log_files, config_dir):
    log_files()
    logging.getLogger("caspar_controller.test").warning("Something odd")
    thread = threading.Thread(target=lambda: 1 / 0, name="worker")
    thread.start()
    thread.join()
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = (config_dir / "logs" / log.AGENT_FILE).read_text(encoding="utf-8")
    assert "WARNING caspar_controller.test: Something odd" in text
    assert "Uncaught error in worker" in text and "ZeroDivisionError" in text


def test_log_files_rotate(log_files, config_dir, monkeypatch):
    monkeypatch.setattr(log, "FILE_BYTES", 2000)
    log_files()
    for n in range(200):
        log.log_event(f"a long enough event line to fill the file quickly, number {n}")
    names = {p.name for p in (config_dir / "logs").iterdir()}
    assert {"events.log", "events.log.1"} <= names and "events.log.6" not in names


# ---------------------------------------------------------------- diagnostics


def unzip(response) -> dict:
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        return {name: z.read(name).decode("utf-8") for name in z.namelist()}


@pytest.fixture
def secret_config(write_config, caspar_dir):
    cfg = write_config(
        {
            "caspar_exe_path": str(caspar_dir / "casparcg.exe"),
            "instances": [instance(1, "GFX1")],
            "remote_controllers": [{"url": "http://10.0.0.2:5280", "api_key": "remote-secret-key"}],
        }
    )
    full = settings.load()
    settings.set_password(full, "pw")
    settings.save(full)
    return {**cfg, **settings.load()}


def test_diagnostics_hold_config_versions_and_logs(client, log_files, secret_config, caspar_dir):
    log_files()
    log.log_event("Started all instances.")
    inst_logs = caspar_dir / "log" / "inst_1"
    inst_logs.mkdir(parents=True)
    (inst_logs / "caspar_2026-11-06.log").write_text("[info] Initialized video modes.\n")
    r = client.get("/api/diagnostics")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "attachment; filename=caspar-controller-diagnostics-" in r.headers["content-disposition"]
    files = unzip(r)
    assert set(files) >= {"config.json", "about.json", "logs/events.log", "casparcg/inst_1.log"}
    about = json.loads(files["about.json"])
    assert about["instances"][0] | {"caspar_version": ""} == {
        "id": 1,
        "name": "GFX1",
        "status": "stopped",
        "caspar_version": "",
        "amcp_port": 5250,
    }
    assert "Started all instances." in files["logs/events.log"]
    assert "Initialized video modes." in files["casparcg/inst_1.log"]


def test_diagnostics_contain_no_secrets(client, log_files, secret_config):
    log_files()
    cfg = settings.load()
    # Even if something ever logged them, the zip leaves them out.
    log.log_event(f"oops {cfg['trigger_key']} {cfg['web_password_hash']} remote-secret-key")
    logging.getLogger("caspar_controller").error("key was %s", API_KEY)
    text = "\n".join(unzip(client.get("/api/diagnostics")).values())
    for secret in (API_KEY, cfg["trigger_key"], cfg["web_password_hash"], "remote-secret-key"):
        assert secret not in text
    assert "[redacted]" in text
    assert json.loads(unzip(client.get("/api/diagnostics"))["config.json"])[
        "remote_controllers"
    ] == [{"url": "http://10.0.0.2:5280", "api_key_set": True}]


def test_diagnostics_need_the_full_key(write_config, secret_config):
    from fastapi.testclient import TestClient

    from caspar_controller.api.app import app

    trigger = TestClient(
        app, client=("10.0.0.9", 50000), headers={"X-API-Key": settings.load()["trigger_key"]}
    )
    assert trigger.get("/api/diagnostics").status_code == 403

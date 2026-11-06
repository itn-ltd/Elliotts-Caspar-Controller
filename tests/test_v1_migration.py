"""Taking over a v1.x install on first start (#12), against a fake C:\\Users tree."""

import json
import os
import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES

from caspar_controller import log, settings
from caspar_controller.auth import verify_password


def v1_install(tmp_path, user: str = "operator", fixture: str = "v1_instances_config.json"):
    """A per-user v1.x install holding ``fixture`` and a casparcg.exe; returns the config path."""
    folder = tmp_path / "Users" / user / settings.V1_INSTALL
    folder.mkdir(parents=True)
    (folder / "casparcg.exe").write_bytes(b"")
    return shutil.copy(FIXTURES / fixture, folder / settings.LEGACY_NAMES[0])


def saved(config_dir) -> dict:
    """What the first start wrote."""
    settings.load()
    return json.loads((config_dir / "config.json").read_text())


# ---------------------------------------------------------------- formats


def test_v1_channels_format_is_migrated(config_dir):
    shutil.copy(FIXTURES / "v1_channels_config.json", config_dir / "config.json")
    cfg = settings.load()
    assert "channels" not in cfg
    assert [i["name"] for i in cfg["instances"]] == [
        "GFX1",
        "GFX2",
        "GFX3",
        "GFX4",
        "GFXPVW",
        "MEDIA1",
        "CH7",
    ]
    assert cfg["instances"][5] == {
        "id": 6,
        "name": "MEDIA1",
        "ndi_name": "PCR3 MEDIA1",
        "type": "media",
        "url": "",
        "startup_command": "PLAY 6-1 LOOP",
    }


def test_v1_amcp_port_is_migrated(write_config):
    write_config({"amcp_port": 6000})
    assert settings.load()["amcp_base_port"] == 6000


# ---------------------------------------------------------------- finding it


@pytest.mark.parametrize("where", ["user", "caspar", "beside"])
def test_found_in_each_place_v1_kept_it(config_dir, tmp_path, where):
    folder = {
        "user": tmp_path / "Users" / "operator" / settings.V1_INSTALL,
        "caspar": tmp_path / "CasparCG",
        "beside": tmp_path / "legacy",
    }[where]
    folder.mkdir(parents=True)
    (folder / settings.LEGACY_NAMES[1]).write_text(json.dumps({"server_name": "Old"}))
    assert settings.load()["server_name"] == "Old"


def test_the_newest_is_used_and_the_rest_logged(config_dir, tmp_path):
    old = v1_install(tmp_path, "first")
    new = v1_install(tmp_path, "second")
    os.utime(old, (1, 1))
    cfg = settings.load()
    assert cfg["v1_notice"]["source"] == str(new)
    assert any(line.endswith(f"Also found the older {old}: not imported.") for line in log._log)


def test_an_unreadable_one_is_skipped(config_dir, tmp_path):
    good = v1_install(tmp_path, "good")
    bad = v1_install(tmp_path, "bad")
    with open(bad, "w") as f:
        f.write("{not json")
    assert settings.load()["v1_notice"]["source"] == str(good)


@pytest.mark.parametrize(
    "bad",
    [{"instances": None}, {"channels": ["GFX1"]}, {"caspar_exe_path": 5}, {"instances": [7]}],
    ids=["null instances", "string channel", "number path", "number instance"],
)
def test_a_wrong_shape_is_skipped_for_the_next(config_dir, tmp_path, bad):
    good = v1_install(tmp_path, "good")
    broken = v1_install(tmp_path, "broken")
    Path(broken).write_text(json.dumps(bad))
    os.utime(good, (1, 1))  # the broken one is newest, so it's tried first
    assert settings.load()["v1_notice"]["source"] == str(good)


def test_a_file_vanishing_while_looking_isnt_fatal(config_dir, tmp_path, monkeypatch):
    v1_install(tmp_path)
    monkeypatch.setattr(
        os.path, "getmtime", lambda path: (_ for _ in ()).throw(FileNotFoundError(path))
    )
    assert "migrated_from" not in settings.load()


def test_an_existing_v2_config_wins(write_config, tmp_path):
    v1_install(tmp_path)
    write_config({"server_name": "New"})
    cfg = settings.load()
    assert cfg["server_name"] == "New" and "migrated_from" not in cfg


# ---------------------------------------------------------------- converting


def test_settings_media_and_outputs_carry_over(config_dir, tmp_path):
    source = v1_install(tmp_path)
    cfg = settings.load()
    assert cfg["media_path"] == "D:\\Media" and cfg["server_name"] == "Gallery Server"
    assert [(i["id"], i["name"]) for i in cfg["instances"]] == [(1, "GFX1"), (2, "MEDIA1")]
    # Same AMCP ports, so trigger URLs and Companion buttons keep working.
    assert [settings.instance_amcp_port(cfg, i) for i in cfg["instances"]] == [5250, 5300]
    assert cfg["caspar_exe_path"] == os.path.join(os.path.dirname(source), "casparcg.exe")
    assert settings.validate_config(cfg)  # a valid v2 config, not just a loaded one


def test_the_password_is_hashed_and_keys_made(config_dir, tmp_path):
    v1_install(tmp_path)
    cfg = saved(config_dir)
    assert "web_password" not in cfg and "web_password_enabled" not in cfg
    assert verify_password("old-password", cfg["web_password_hash"])
    assert cfg["api_key"] and cfg["trigger_key"]
    assert cfg["auto_restart"] is True and "startup_delay" not in cfg


def test_a_missing_caspar_exe_is_dropped(config_dir, tmp_path):
    os.remove(os.path.join(os.path.dirname(v1_install(tmp_path)), "casparcg.exe"))
    assert settings.load()["caspar_exe_path"] == ""


def test_backed_up_and_marked_and_the_original_kept(config_dir, tmp_path):
    source = v1_install(tmp_path, fixture="v1_channels_config.json")
    original = open(source, encoding="utf-8").read()
    cfg = saved(config_dir)
    assert json.loads((config_dir / settings.V1_BACKUP).read_text()) == json.loads(original)
    assert open(source, encoding="utf-8").read() == original  # v1.x until it's uninstalled
    assert cfg["migrated_from"] == "1.x"
    assert cfg["v1_notice"] == {"source": str(source), "outputs": 7, "password": False}
    assert len(cfg["instances"]) == 7 and "channels" not in cfg


def test_the_backup_has_no_plaintext_password(config_dir, tmp_path):
    v1_install(tmp_path)
    saved(config_dir)
    backup = json.loads((config_dir / settings.V1_BACKUP).read_text())
    assert "web_password" not in backup and backup["server_name"] == "Gallery Server"


def test_imported_even_if_the_backup_cant_be_saved(config_dir, tmp_path):
    (config_dir / settings.V1_BACKUP).mkdir()  # a folder in the way: writing it fails
    v1_install(tmp_path)
    assert settings.load()["migrated_from"] == "1.x"
    assert any(f"Couldn't save {config_dir / settings.V1_BACKUP}" in line for line in log._log)


def test_a_blank_password_isnt_reported_as_kept(config_dir, tmp_path):
    source = v1_install(tmp_path)
    cfg = json.loads(Path(source).read_text())
    Path(source).write_text(json.dumps({**cfg, "web_password": "  "}))
    loaded = settings.load()
    assert loaded["v1_notice"]["password"] is False and not loaded["web_password_hash"]


def test_exports_leave_out_the_migration_marks(local_anon, config_dir, tmp_path):
    v1_install(tmp_path, fixture="v1_channels_config.json")
    exported = local_anon.get("/api/config/export").json()
    assert not {"migrated_from", "v1_notice"} & exported.keys()


def test_imports_and_profiles_never_carry_the_migration_marks():
    marks = {"migrated_from": "1.x", "v1_notice": {"source": "elsewhere"}}
    assert not marks.keys() & settings.foreign_config({**marks, "server_name": "X"}, "").keys()


def test_the_dashboard_notice_shows_until_dismissed(local_anon, config_dir, tmp_path):
    v1_install(tmp_path, fixture="v1_channels_config.json")  # no password: this PC is trusted
    assert local_anon.get("/api/config").json()["v1_notice"]["outputs"] == 7
    assert local_anon.post("/api/v1-notice/dismiss").status_code == 200
    assert "v1_notice" not in local_anon.get("/api/config").json()
    assert saved(config_dir)["migrated_from"] == "1.x"  # still marked for the remediation script

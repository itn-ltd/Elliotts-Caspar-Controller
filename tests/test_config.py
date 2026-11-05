"""Config loading, migration, port assignment and casparcg.config generation."""

import json
import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES, TESTS_DIR, instance

from caspar_controller import settings
from caspar_controller.caspar import config_xml

GOLDEN = TESTS_DIR / "golden"


# ---------------------------------------------------------------- loading


def test_missing_file_gives_defaults_plus_api_key():
    cfg = settings.load()
    assert {**cfg, "api_key": ""} == settings.DEFAULT_CONFIG
    assert len(cfg["api_key"]) >= 40


def test_v1_channels_file_loads_other_settings(config_dir):
    # The channels/amcp_port migrations themselves never run: see test_known_issues.py.
    shutil.copy(FIXTURES / "v1_channels_config.json", config_dir / "elliotts_caspar_config.json")
    cfg = settings.load()
    assert cfg["autostart_caspar"] is True
    assert "startup_delay" not in cfg  # never used, dropped on load
    assert cfg["video_mode"] == "1080p2500"
    assert "amcp_port" not in cfg


def test_misspelled_filename_is_renamed(config_dir):
    (config_dir / "elliotts_casper_config.json").write_text(json.dumps({"server_name": "Old"}))
    assert settings.load()["server_name"] == "Old"
    assert (config_dir / "elliotts_caspar_config.json").exists()
    assert not (config_dir / "elliotts_casper_config.json").exists()


def test_instance_fields_are_backfilled(write_config):
    write_config({"instances": [{"id": 1, "name": "A", "ndi_name": "N"}]})
    inst = settings.load()["instances"][0]
    assert inst["type"] == "html"
    assert inst["url"] == ""
    assert inst["startup_command"] == ""


def test_stored_values_override_defaults(write_config):
    write_config({"web_port": 6000, "video_mode": "1080i5000"})
    cfg = settings.load()
    assert cfg["web_port"] == 6000
    assert cfg["video_mode"] == "1080i5000"
    assert cfg["amcp_base_port"] == 5250


def test_save_then_load_round_trips():
    cfg = settings.load()
    cfg["server_name"] = "Studio A"
    cfg["instances"] = [instance(1, "GFX1")]
    settings.save(cfg)
    assert settings.load() == cfg


# ---------------------------------------------------------------- ports


@pytest.mark.parametrize(
    ("override", "position", "expected"),
    [
        (0, 0, 5250),  # base + position
        (0, 2, 5252),
        (None, 1, 5251),  # missing key behaves like 0
        (6000, 1, 6000),  # explicit override wins
    ],
)
def test_instance_amcp_port(override, position, expected):
    insts = [instance(i + 1, f"I{i}") for i in range(3)]
    if override is None:
        del insts[position]["amcp_port"]
    else:
        insts[position]["amcp_port"] = override
    cfg = {"amcp_base_port": 5250, "instances": insts}
    assert settings.instance_amcp_port(cfg, insts[position]) == expected


def test_port_follows_id_not_list_position():
    # Changed in #5: by position, reordering moved a live instance onto another's port.
    a, b = instance(1, "A"), instance(2, "B")
    cfg = {"amcp_base_port": 5250, "instances": [b, a]}
    assert settings.instance_amcp_port(cfg, a) == 5250
    assert settings.instance_amcp_port(cfg, b) == 5251
    assert settings.instance_amcp_port({"amcp_base_port": 7000}, instance(9, "X")) == 7008


def test_instance_ids_survive_reorder(client, write_config):
    write_config({"instances": [instance(1, "A"), instance(2, "B")]})
    client.post("/api/config", json={"instances": [instance(2, "B"), instance(1, "A")]})
    assert [i["id"] for i in client.get("/api/config").json()["instances"]] == [2, 1]


def test_clashing_instance_ids_get_the_next_free_one(client, write_config):
    write_config({"instances": [instance(1, "A"), instance(3, "C")]})
    insts = [instance(1, "A"), instance(3, "C"), instance(3, "D"), instance(0, "E")]
    client.post("/api/config", json={"instances": insts})
    assert [i["id"] for i in client.get("/api/config").json()["instances"]] == [1, 3, 4, 5]


# ---------------------------------------------------------------- casparcg.config


def _gen_config(tmp_path, media_path=""):
    return {
        "caspar_exe_path": str(tmp_path / "caspar" / "casparcg.exe"),
        "amcp_base_port": 5250,
        "video_mode": "1080p5000",
        "media_path": media_path,
        "instances": [instance(1, "GFX1"), instance(2, "MEDIA1", "media")],
    }


def test_instance_config_matches_golden(tmp_path):
    (tmp_path / "caspar").mkdir()
    cfg = _gen_config(tmp_path)
    path = config_xml.regenerate_instance_config(cfg, cfg["instances"][1])

    assert Path(path) == tmp_path / "caspar" / "casparcg_inst_2.config"
    golden = (GOLDEN / "casparcg_inst_2.config").read_text(encoding="utf-8")
    assert Path(path).read_text(encoding="utf-8") == golden


def test_custom_media_path_is_written(tmp_path):
    (tmp_path / "caspar").mkdir()
    cfg = _gen_config(tmp_path, media_path="  D:\\Media\\  ")
    path = config_xml.regenerate_instance_config(cfg, cfg["instances"][0])
    assert "<media-path>D:\\Media\\</media-path>" in Path(path).read_text(encoding="utf-8")


def test_regenerate_creates_dirs_and_keeps_html_cache(tmp_path):
    # Changed in #5: saving settings used to wipe the cache of instances that were live.
    caspar = tmp_path / "caspar"
    cached = caspar / "html_cache" / "inst_1" / "cached.txt"
    cached.parent.mkdir(parents=True)
    cached.write_text("x")
    cfg = _gen_config(tmp_path)

    config_xml.regenerate_instance_config(cfg, cfg["instances"][0])

    assert (caspar / "log" / "inst_1").is_dir()
    assert (caspar / "data" / "inst_1").is_dir()
    assert cached.exists()

    config_xml.reset_html_cache(cfg, cfg["instances"][0])  # what a start does
    assert (caspar / "html_cache" / "inst_1").is_dir()
    assert not cached.exists()


def test_relative_exe_path_writes_config_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = {
        "caspar_exe_path": "casparcg.exe",
        "video_mode": "1080p2500",
        "instances": [instance(4, "X")],
    }
    path = config_xml.regenerate_instance_config(cfg, cfg["instances"][0])
    assert path == "casparcg_inst_4.config"
    assert (tmp_path / "casparcg_inst_4.config").exists()
    assert not (tmp_path / "html_cache").exists()


def test_regenerate_all_returns_one_path_per_instance(tmp_path):
    (tmp_path / "caspar").mkdir()
    cfg = _gen_config(tmp_path)
    paths = config_xml.regenerate_all_instance_configs(cfg)
    assert [Path(p).name for p in paths] == ["casparcg_inst_1.config", "casparcg_inst_2.config"]


# ---------------------------------------------------------------- test patterns


EXPECTED_PATTERNS = {
    "TEST_BARS",
    "TEST_BLACK",
    "TEST_BLUE",
    "TEST_GREEN",
    "TEST_GREY",
    "TEST_RED",
    "TEST_WHITE",
}


def test_test_patterns_written_next_to_exe(tmp_path):
    cfg = {"caspar_exe_path": str(tmp_path / "casparcg.exe"), "media_path": ""}
    config_xml.ensure_test_pattern_images(cfg)
    assert {p.stem for p in (tmp_path / "media").glob("*.png")} == EXPECTED_PATTERNS


def test_test_patterns_use_custom_media_path_and_keep_existing(tmp_path):
    media = tmp_path / "custom"
    media.mkdir()
    (media / "TEST_RED.png").write_bytes(b"keep me")
    config_xml.ensure_test_pattern_images({"caspar_exe_path": "", "media_path": str(media)})
    assert (media / "TEST_RED.png").read_bytes() == b"keep me"
    assert len(list(media.glob("TEST_*.png"))) == 7


def test_test_patterns_skipped_without_absolute_exe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_xml.ensure_test_pattern_images({"caspar_exe_path": "casparcg.exe", "media_path": ""})
    assert not (tmp_path / "media").exists()

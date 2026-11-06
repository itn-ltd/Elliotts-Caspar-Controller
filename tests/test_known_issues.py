"""Known-bad behaviour, pinned as expected failures.

Each test asserts the *correct* behaviour and is marked ``xfail`` with the issue
that fixes it. xfail is strict (pyproject.toml), so when a fix lands the test
"unexpectedly passes" and fails the run: remove the marker in that PR.
"""

import shutil

import pytest
from conftest import FIXTURES

from caspar_controller import settings


def issue(number: int, why: str, raises: type | tuple = AssertionError) -> pytest.MarkDecorator:
    # `raises` stops an unrelated error (e.g. from a partial fix) passing as "still broken".
    return pytest.mark.xfail(reason=f"#{number}: {why}", raises=raises)


# ---------------------------------------------------------------- #12 v1.x migration


@issue(12, "v1 'channels' migration never runs: DEFAULT_CONFIG already has 'instances'")
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


@issue(12, "old 'amcp_port' is dropped instead of becoming 'amcp_base_port'")
def test_v1_amcp_port_is_migrated(write_config):
    write_config({"amcp_port": 6000})
    assert settings.load()["amcp_base_port"] == 6000

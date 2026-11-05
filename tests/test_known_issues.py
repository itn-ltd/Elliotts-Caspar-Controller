"""Known-bad behaviour, pinned as expected failures.

Each test asserts the *correct* behaviour and is marked ``xfail`` with the issue
that fixes it. xfail is strict (pyproject.toml), so when a fix lands the test
"unexpectedly passes" and fails the run: remove the marker in that PR.
"""

import itertools
import shutil
import types

import pytest
from conftest import FIXTURES, instance

from caspar_controller import settings, tally
from caspar_controller.caspar.info import parse_layer_info


def issue(number: int, why: str, raises: type | tuple = AssertionError) -> pytest.MarkDecorator:
    # `raises` stops an unrelated error (e.g. from a partial fix) passing as "still broken".
    return pytest.mark.xfail(reason=f"#{number}: {why}", raises=raises)


# ---------------------------------------------------------------- #6 settings validation


@issue(6, "duplicate AMCP ports are accepted")
def test_duplicate_ports_rejected(client):
    r = client.post(
        "/api/config",
        json={"instances": [instance(1, "A", amcp_port=6000), instance(2, "B", amcp_port=6000)]},
    )
    assert r.status_code in (400, 422)


# ---------------------------------------------------------------- #7 display


@issue(7, "the _<digits> suffix strip truncates real clip names")
def test_clip_names_with_numeric_suffix_kept():
    xml = (
        '<layer><foreground><producer type="ffmpeg-producer">'
        "<filename>PROMO_2024</filename></producer></foreground></layer>"
    )
    assert parse_layer_info("201 INFO OK\n" + xml)["clip"] == "PROMO_2024"


@issue(
    7,
    "INFO parsing expects a layout CasparCG 2.3 doesn't send (synthetic 2.3-style XML; replace with a capture)",
)
def test_caspar_23_info_layout_parsed():
    xml = (
        "<layer><background><producer>empty</producer></background><foreground>"
        "<file><clip>0 60</clip><name>AMB</name><path>media/AMB.mp4</path><time>12.5 60</time></file>"
        "<loop>false</loop><paused>false</paused><producer>ffmpeg</producer></foreground></layer>"
    )
    info = parse_layer_info("201 INFO OK\n" + xml)
    assert info["clip"] == "AMB"
    assert info["time"] == 12.5


@issue(7, "tally matches NDI names by substring, so GFX1 lights for GFX10")
def test_tally_matches_exact_ndi_name(monkeypatch):
    other = types.SimpleNamespace(ndi_name="PLAYOUT-PC (PCR3 GFX10)")
    fake_ndi = types.SimpleNamespace(
        FindCreate=types.SimpleNamespace,
        find_create_v2=lambda opts: object(),
        find_wait_for_sources=lambda finder, ms: None,
        find_get_current_sources=lambda finder: [other],
        find_destroy=lambda finder: None,
    )
    monkeypatch.setattr(tally, "ndi", fake_ndi)
    stop = types.SimpleNamespace(is_set=lambda: False)
    # One search pass, then the 15 s deadline has passed.
    clock = itertools.chain([0, 0], itertools.repeat(99))
    monkeypatch.setattr(tally, "time", types.SimpleNamespace(time=clock.__next__))
    assert tally._find_source("PCR3 GFX1", stop) is None


# ---------------------------------------------------------------- #12 v1.x migration


@issue(12, "v1 'channels' migration never runs: DEFAULT_CONFIG already has 'instances'")
def test_v1_channels_format_is_migrated(config_dir):
    shutil.copy(FIXTURES / "v1_channels_config.json", config_dir / "elliotts_caspar_config.json")
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

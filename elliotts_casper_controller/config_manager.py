"""Read/write app config and regenerate per-instance casparcg.config files."""

import json
import logging
import os
import sys
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from elliotts_casper_controller.amcp_client import has_control_chars, is_amcp_safe
from elliotts_casper_controller.auth import hash_password, new_api_key, verify_password

log = logging.getLogger(__name__)

SECRET_KEYS = ("api_key", "web_password_hash")
# Belong to this machine: config imports never change them.
MACHINE_KEYS = (*SECRET_KEYS, "password_change_required")
# Seeds the first web UI password (e.g. set by Intune); users must change it on first sign-in.
INITIAL_PASSWORD_ENV = "CASPAR_CONTROLLER_INITIAL_PASSWORD"


def _config_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DEFAULT_CONFIG = {
    "caspar_exe_path": "",
    "amcp_base_port": 5250,
    "web_port": 5280,
    "startup_delay": 60,
    "video_mode": "1080p2500",
    "autostart_caspar": False,
    "media_path": "",
    "server_name": "",
    "web_password_hash": "",
    "password_change_required": False,
    "api_key": "",
    "instances": [],
    "remote_controllers": [],
}


def _config_file() -> str:
    d = _config_dir()
    new_path = os.path.join(d, "elliotts_caspar_config.json")
    old_path = os.path.join(d, "elliotts_casper_config.json")
    # Migrate old misspelled filename on first run
    if not os.path.exists(new_path) and os.path.exists(old_path):
        os.rename(old_path, new_path)
    return new_path


def instance_amcp_port(cfg: dict, inst: dict) -> int:
    """Return the AMCP port for an instance.

    Uses inst['amcp_port'] if explicitly set (non-zero), otherwise falls back
    to base_port + position in the instances list.
    """
    override = inst.get("amcp_port")
    if override:
        return int(override)
    base = cfg.get("amcp_base_port", 5250)
    for i, x in enumerate(cfg.get("instances", [])):
        if x["id"] == inst["id"]:
            return base + i
    return base


def load() -> dict:
    cfg_file = _config_file()
    if os.path.exists(cfg_file):
        with open(cfg_file, "r") as f:
            stored = json.load(f)
        config = dict(DEFAULT_CONFIG)
        config.update(stored)
    else:
        config = dict(DEFAULT_CONFIG)

    # --- Migrate old single-server channel config to multi-instance ---
    if "channels" in config and "instances" not in config:
        config["instances"] = [
            {
                "id": ch.get("number", i + 1),
                "name": ch.get("name", f"CH{i + 1}"),
                "ndi_name": ch.get("ndi_name", f"PCR3 CH{i + 1}"),
                "type": ch.get("type", "html"),
                "url": ch.get("url", ""),
                "startup_command": ch.get("startup_command", ""),
            }
            for i, ch in enumerate(config["channels"])
        ]
        del config["channels"]

    # Migrate amcp_port → amcp_base_port
    if "amcp_port" in config and "amcp_base_port" not in config:
        config["amcp_base_port"] = config["amcp_port"]
    config.pop("amcp_port", None)

    # Backfill top-level defaults
    config.setdefault("amcp_base_port", 5250)
    config.setdefault("autostart_caspar", False)
    config.setdefault("instances", [])
    config.setdefault("remote_controllers", [])
    if config.get("startup_delay", 60) <= 15:
        config["startup_delay"] = 60

    # Backfill per-instance fields
    for inst in config.get("instances", []):
        inst.setdefault("type", "html")
        inst.setdefault("startup_command", "")
        inst.setdefault("url", "")

    if _secure_secrets(config):
        try:
            save(config)
        except OSError as e:
            # Read-only config folder: run with what we have rather than refuse to start.
            log.warning("Couldn't save %s: %s", cfg_file, e)
    return config


def set_password(config: dict, password: str, must_change: bool = False) -> None:
    """Store a new web UI password hash. Raises ValueError if it's blank or unchanged."""
    if not password.strip():
        raise ValueError("Password can't be empty")
    current = config["web_password_hash"]
    if current and verify_password(password, current):
        raise ValueError("Choose a different password")
    config["web_password_hash"] = hash_password(password)
    config["password_change_required"] = must_change


def _secure_secrets(config: dict) -> bool:
    """Hash v1 passwords, apply the initial password and create the API key. True if changed."""
    changed = False
    plaintext = config.pop("web_password", None)
    enabled = config.pop("web_password_enabled", None)
    if plaintext is not None or enabled is not None:
        changed = True
        if enabled and plaintext and plaintext.strip() and not config["web_password_hash"]:
            config["web_password_hash"] = hash_password(plaintext)
    initial = os.environ.get(INITIAL_PASSWORD_ENV, "")
    if initial.strip() and not config["web_password_hash"]:
        set_password(config, initial, must_change=True)
        changed = True
    if not config["api_key"]:
        config["api_key"] = new_api_key()
        changed = True
    return changed


def without_secrets(config: dict) -> dict:
    """Config minus the password hash and API keys, including each remote's key."""
    view = {k: v for k, v in config.items() if k not in SECRET_KEYS}
    if "remote_controllers" in config:
        view["remote_controllers"] = [
            {
                **{k: v for k, v in r.items() if k != "api_key"},
                "api_key_set": bool(r.get("api_key")),
            }
            for r in config["remote_controllers"]
        ]
    return view


def validate_remote_url(url: str) -> None:
    """Raise ValueError unless ``url`` is a plain ``http(s)://host[:port]``."""
    parts = urlsplit(url)
    try:
        port_ok = parts.port != 0
    except ValueError:  # non-numeric or out of range
        port_ok = False
    if (
        not port_ok
        or parts.scheme not in ("http", "https")
        or not parts.hostname
        or "@" in parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(f"Remote URL must look like http://host:port, not {url!r}")


def _check_text(field: str, value, required: bool = False) -> None:
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{field} must be text" + (" and can't be empty" if required else ""))
    if has_control_chars(value):
        raise ValueError(f"{field} contains characters that aren't allowed")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_list(field: str, data: dict) -> list:
    value = data.get(field, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ValueError(f"{field} must be a list of objects")
    return value


def _check_port(field: str, value, allow_zero: bool = False) -> None:
    if not _is_int(value) or not ((allow_zero and value == 0) or 1 <= value <= 65535):
        raise ValueError(f"{field} must be a port number")


def validate_config(data: dict) -> None:
    """Raise ValueError if any field present in ``data`` is unsafe or malformed."""
    exe = data.get("caspar_exe_path")
    if exe is not None:
        _check_text("CasparCG path", exe)
        # The path is executed on start, so only accept a real casparcg.exe.
        if exe and (os.path.basename(exe).lower() != "casparcg.exe" or not os.path.isfile(exe)):
            raise ValueError("CasparCG path must point to an existing casparcg.exe")
    for field in ("media_path", "server_name", "video_mode"):
        if field in data:
            _check_text(field, data[field])
    for field in ("web_port", "amcp_base_port"):
        if field in data:
            _check_port(field, data[field])
    if not _is_int(data.get("startup_delay", 60)):
        raise ValueError("startup_delay must be a whole number of seconds")
    if not isinstance(data.get("autostart_caspar", False), bool):
        raise ValueError("autostart_caspar must be true or false")
    for inst in _check_list("instances", data):
        if not _is_int(inst.get("id")):
            raise ValueError("Each instance needs a numeric id")
        _check_text("Instance name", inst.get("name"), required=True)
        _check_text("NDI name", inst.get("ndi_name"), required=True)
        if inst.get("type", "html") not in ("html", "media"):
            raise ValueError("Instance type must be html or media")
        _check_text("Instance URL", inst.get("url", ""))
        if not is_amcp_safe(inst.get("url", "")):
            raise ValueError("Instance URL can't contain quotes or backslashes")
        _check_text("Startup command", inst.get("startup_command", ""))
        _check_port("Instance AMCP port", inst.get("amcp_port", 0), allow_zero=True)
    for remote in _check_list("remote_controllers", data):
        _check_text("Remote URL", remote.get("url"), required=True)
        validate_remote_url(remote["url"])
        _check_text("Remote API key", remote.get("api_key", ""))


def keep_remote_keys(remotes: list, previous: list) -> list:
    """Fill blank remote API keys from the saved remote with the same URL."""

    def url(r: dict) -> str:
        return r.get("url", "").rstrip("/")

    saved = {url(r): r.get("api_key", "") for r in previous}
    return [
        {
            **{k: v for k, v in r.items() if k != "api_key_set"},
            "api_key": r.get("api_key") or saved.get(url(r), ""),
        }
        for r in remotes
    ]


def save(config: dict) -> None:
    with open(_config_file(), "w") as f:
        json.dump(config, f, indent=2)


def caspar_instance_config_path(config: dict, inst: dict) -> str:
    """Return the full path for a per-instance casparcg config file."""
    filename = f"casparcg_inst_{inst['id']}.config"
    exe = config.get("caspar_exe_path", "")
    if exe and os.path.isabs(exe):
        return os.path.join(os.path.dirname(exe), filename)
    return filename


def regenerate_instance_config(config: dict, inst: dict) -> str:
    """Write a single-channel casparcg.config for one instance. Returns the path written.

    Each instance gets its own log/inst_N and data/inst_N subdirectory to prevent file-locking
    conflicts when multiple CasparCG processes run from the same folder simultaneously.
    """
    port = instance_amcp_port(config, inst)
    inst_id = int(inst["id"])
    media_path_xml = escape(config.get("media_path", "").strip() or "media\\")
    video_mode = escape(config["video_mode"])
    ndi_name = escape(inst["ndi_name"])

    exe = config.get("caspar_exe_path", "")
    if exe and os.path.isabs(exe):
        import shutil

        exe_dir = os.path.dirname(exe)
        for subdir in (f"log\\inst_{inst_id}", f"data\\inst_{inst_id}"):
            os.makedirs(os.path.join(exe_dir, subdir), exist_ok=True)
        # Wipe the CEF session cache before each start so Chromium never shows
        # a "Restore pages?" dialog or navigates to the config file on startup.
        html_cache = os.path.join(exe_dir, f"html_cache\\inst_{inst_id}")
        shutil.rmtree(html_cache, ignore_errors=True)
        os.makedirs(html_cache, exist_ok=True)

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <log-level>info</log-level>

  <channels>
    <!-- Instance {inst_id} -->
    <channel>
      <video-mode>{video_mode}</video-mode>
      <consumers>
        <ndi>
          <name>{ndi_name}</name>
          <allow-fields>false</allow-fields>
        </ndi>
      </consumers>
    </channel>
  </channels>

  <paths>
    <media-path>{media_path_xml}</media-path>
    <log-path>log\\inst_{inst_id}\\</log-path>
    <data-path>data\\inst_{inst_id}\\</data-path>
    <template-path>template\\</template-path>
  </paths>

  <controllers>
    <tcp>
      <port>{port}</port>
      <protocol>AMCP</protocol>
    </tcp>
  </controllers>

  <html>
    <remote-debugging-port>0</remote-debugging-port>
    <enable-gpu>false</enable-gpu>
    <cache-path>html_cache\\inst_{inst_id}\\</cache-path>
  </html>

</configuration>
"""
    out_path = caspar_instance_config_path(config, inst)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)
    return out_path


def regenerate_all_instance_configs(config: dict) -> list:
    """Regenerate config files for every instance. Returns list of paths written."""
    return [regenerate_instance_config(config, inst) for inst in config.get("instances", [])]


_TEST_COLOURS = {
    "TEST_BLACK": (0, 0, 0),
    "TEST_WHITE": (255, 255, 255),
    "TEST_RED": (255, 0, 0),
    "TEST_GREEN": (0, 255, 0),
    "TEST_BLUE": (0, 0, 255),
    "TEST_GREY": (128, 128, 128),
}

# 7 standard EBU/SMPTE colour bar stripes (100%)
_SMPTE_BARS = [
    (255, 255, 255),  # White
    (255, 255, 0),  # Yellow
    (0, 255, 255),  # Cyan
    (0, 255, 0),  # Green
    (255, 0, 255),  # Magenta
    (255, 0, 0),  # Red
    (0, 0, 255),  # Blue
]


def _make_bars_image():
    from PIL import Image, ImageDraw

    W, H = 1920, 1080
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    bar_w = W // len(_SMPTE_BARS)
    for i, colour in enumerate(_SMPTE_BARS):
        x0 = i * bar_w
        x1 = x0 + bar_w if i < len(_SMPTE_BARS) - 1 else W
        draw.rectangle([x0, 0, x1, H], fill=colour)
    return img


def ensure_test_pattern_images(config: dict) -> None:
    """Write solid-colour and bars PNG test patterns into the CasparCG media folder.

    Called on every server start so test clips are always available.
    Skips if exe path is not set or media folder doesn't exist yet.
    """
    custom_media = config.get("media_path", "").strip()
    if custom_media:
        media_dir = custom_media
    else:
        exe = config.get("caspar_exe_path", "")
        if not exe or not os.path.isabs(exe):
            return
        media_dir = os.path.join(os.path.dirname(exe), "media")
    os.makedirs(media_dir, exist_ok=True)
    try:
        from PIL import Image

        for name, rgb in _TEST_COLOURS.items():
            path = os.path.join(media_dir, f"{name}.png")
            if not os.path.exists(path):
                Image.new("RGB", (1920, 1080), rgb).save(path)
        bars_path = os.path.join(media_dir, "TEST_BARS.png")
        if not os.path.exists(bars_path):
            _make_bars_image().save(bars_path)
    except Exception:
        pass

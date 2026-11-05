"""Read, validate and save the app config."""

import json
import logging
import os
import sys
from urllib.parse import urlsplit

from caspar_controller.auth import hash_password, new_api_key, verify_password
from caspar_controller.caspar.amcp import has_control_chars, is_amcp_safe

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
    """inst['amcp_port'] if set, otherwise base port + id - 1.

    By id, not list position, so reordering never moves a live instance's port.
    v1.x renumbered ids by position on every save, so existing ports don't change.
    """
    return int(inst.get("amcp_port") or cfg.get("amcp_base_port", 5250) + inst["id"] - 1)


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
    config.pop("startup_delay", None)  # v1.x setting that was never used

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

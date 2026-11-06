"""Read, validate and save the app config."""

import copy
import ipaddress
import json
import logging
import os
import re
import sys
import threading
import time
from typing import Annotated, Literal, Optional
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from caspar_controller.auth import hash_password, new_api_key, verify_password
from caspar_controller.caspar.amcp import has_control_chars, is_amcp_safe
from caspar_controller.log import log_event

log = logging.getLogger(__name__)

SECRET_KEYS = ("api_key", "trigger_key", "web_password_hash")
# Belong to this machine: config imports and profiles never change them.
MACHINE_KEYS = (*SECRET_KEYS, "password_change_required")
V1_PASSWORD_KEYS = ("web_password", "web_password_enabled")
# Seeds the first web UI password (e.g. set by Intune); users must change it on first sign-in.
INITIAL_PASSWORD_ENV = "CASPAR_CONTROLLER_INITIAL_PASSWORD"
HOME_ENV = "CASPAR_CONTROLLER_HOME"  # overrides %ProgramData%\CasparController, for development
# v1.x kept its config next to the exe (or the repo, from source), in either spelling.
LEGACY_DIR = os.path.dirname(
    sys.executable if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
)
LEGACY_NAMES = ("elliotts_caspar_config.json", "elliotts_casper_config.json")


def home_dir() -> str:
    default = os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"), "CasparController")
    return os.environ.get(HOME_ENV) or default


def _config_file() -> str:
    return os.path.join(home_dir(), "config.json")


# ---------------------------------------------------------------- validation


def _client_address(value: str) -> str:
    """An IP address, a subnet (10.0.0.0/24) or a host name."""
    value = value.strip()
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?", value):
            raise ValueError(f"{value!r} isn't an IP address, subnet or host name") from None
    return value


def _text(value: str) -> str:
    if has_control_chars(value):
        raise ValueError("contains characters that aren't allowed")
    return value


def _name(value: str) -> str:
    # No + & #: names go unencoded into trigger URLs (/api/load?instance=...).
    if not re.fullmatch(r"[\w.()'-]([\w .()'-]{0,62}[\w.()'-])?", value):
        raise ValueError(
            "use up to 64 letters, numbers, spaces and - _ . ( ) ', not starting or ending with a space"
        )
    return value


def _amcp_safe(value: str) -> str:
    if not is_amcp_safe(value):
        raise ValueError("can't contain quotes, backslashes or control characters")
    return value


def _casparcg_exe(path: str) -> str:
    # The path is executed on start, so only accept a real casparcg.exe.
    if path and (os.path.basename(path).lower() != "casparcg.exe" or not os.path.isfile(path)):
        raise ValueError("must point to an existing casparcg.exe")
    return _text(path)


def validate_remote_url(url: str) -> str:
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
    return url


Text = Annotated[str, AfterValidator(_text)]
Name = Annotated[str, AfterValidator(_name)]
Port = Annotated[int, Field(ge=1, le=65535)]


class _Model(BaseModel):
    # Strict: "1" isn't a port and "yes" isn't true. Unknown keys pass through untouched.
    model_config = ConfigDict(strict=True, extra="allow")


class Instance(_Model):
    id: int  # Settings makes it positive and unique
    name: Name
    ndi_name: Name
    type: Literal["html", "media"] = "html"
    url: Annotated[str, AfterValidator(_amcp_safe)] = ""
    startup_command: Text = ""
    amcp_port: int = Field(0, ge=0, le=65535)  # 0: base port + id - 1
    auto_restart: Literal["on", "off", "inherit"] = "inherit"  # inherit: the global setting


class Remote(_Model):
    url: Annotated[str, AfterValidator(validate_remote_url)]
    api_key: Text = ""


class Settings(_Model):
    """The settings an operator can change (Settings page, import, profile)."""

    caspar_exe_path: Annotated[str, AfterValidator(_casparcg_exe)] = ""
    amcp_base_port: Port = 5250
    web_port: Port = 5280
    video_mode: Text = "1080p2500"
    autostart_caspar: bool = False
    auto_restart: bool = True  # restart crashed or hung instances
    hang_timeout: int = Field(30, ge=5, le=600)  # seconds without an AMCP reply = hung
    media_path: Text = ""
    server_name: Text = ""
    instances: list[Instance] = []
    remote_controllers: list[Remote] = []
    # Addresses allowed to use either API key; empty: any. Signed-in browsers aren't affected.
    api_clients: list[Annotated[str, AfterValidator(_client_address)]] = []

    @field_validator("instances")
    @classmethod
    def _keep_ids_unique(cls, insts: list) -> list:
        """Keep each instance's id, so reordering never retargets a running process.

        A clashing or non-positive id gets the next free number.
        """
        next_id = max((i.id for i in insts), default=0) + 1
        seen = set()
        for inst in insts:
            if inst.id < 1 or inst.id in seen:
                inst.id, next_id = next_id, next_id + 1
            seen.add(inst.id)
        return insts

    @model_validator(mode="after")
    def _unique(self):
        for field, label in (("name", "name"), ("ndi_name", "NDI name")):
            _no_duplicates(label, [getattr(i, field).lower() for i in self.instances])
        ports = [i.amcp_port or self.amcp_base_port + i.id - 1 for i in self.instances]
        _no_duplicates("AMCP port", ports)
        if any(p > 65535 for p in ports):
            raise ValueError("AMCP ports must be 65535 or lower: lower the base port")
        return self


def _no_duplicates(label: str, values: list) -> None:
    dupes = sorted({str(v) for v in values if values.count(v) > 1})
    if dupes:
        raise ValueError(f"Each instance needs its own {label}: {', '.join(dupes)} used twice")


def validate_config(data: dict) -> dict:
    """The fields of ``data`` the Settings model knows, validated, with instance ids made unique.

    Raises ValueError with a readable message.
    """
    try:
        settings = Settings.model_validate(data)
    except ValidationError as e:
        raise ValueError(
            "; ".join(
                f"{'.'.join(map(str, err['loc'])) or 'config'}: {err['msg'].removeprefix('Value error, ')}"
                for err in e.errors()
            )
        ) from None
    return settings.model_dump(include=set(data) & set(Settings.model_fields))


def foreign_config(data: dict, this_exe: str) -> dict:
    """Validate a config from elsewhere (an import or a profile) for use on this PC."""
    if not isinstance(data, dict):
        raise ValueError("Config must be a JSON object")
    # v1.x password keys too: load() would turn them into this machine's password.
    data = {k: v for k, v in data.items() if k not in (*MACHINE_KEYS, *V1_PASSWORD_KEYS)}
    # Another PC's CasparCG path is meaningless here: keep this PC's.
    if "caspar_exe_path" in data and not os.path.isfile(data["caspar_exe_path"] or ""):
        data["caspar_exe_path"] = this_exe
    return {**data, **validate_config(data)}


DEFAULT_CONFIG = {
    **Settings().model_dump(),
    "web_password_hash": "",
    "password_change_required": False,
    "api_key": "",
    "trigger_key": "",  # fires triggers only: for Automator and panels
}


def auto_restart_on(cfg: dict, inst: dict) -> bool:
    """The instance's own on/off, or the global setting if it inherits."""
    choice = inst.get("auto_restart", "inherit")
    return choice == "on" or (choice == "inherit" and cfg["auto_restart"])


def instance_amcp_port(cfg: dict, inst: dict) -> int:
    """inst['amcp_port'] if set, otherwise base port + id - 1.

    By id, not list position, so reordering never moves a live instance's port.
    v1.x renumbered ids by position on every save, so existing ports don't change.
    """
    return int(inst.get("amcp_port") or cfg.get("amcp_base_port", 5250) + inst["id"] - 1)


# ---------------------------------------------------------------- storage

_lock = threading.RLock()
_cache: Optional[dict] = None  # read once; save() clears it


def load() -> dict:
    """The config (a copy: change it, then save())."""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _read()
        return copy.deepcopy(_cache)


def _first_run_config() -> dict:
    """A v1.x config copied from next to the exe, else a profile.json seed, else defaults."""
    # A bad seed file is logged and skipped: it must never stop the app starting.
    for name in LEGACY_NAMES:
        legacy = os.path.join(LEGACY_DIR, name)
        if os.path.isfile(legacy):
            try:
                stored = _read_object(legacy)
                log_event(f"Copied the config from {legacy}.")  # the original stays for v1.x
                return stored
            except (OSError, ValueError) as e:
                log_event(f"Ignored {legacy}: {e}")
    profile = os.path.join(home_dir(), "profile.json")
    if os.path.isfile(profile):
        try:
            seed = foreign_config(_read_object(profile), "")
            log_event(f"New install: settings taken from {profile}.")
            return seed
        except (OSError, ValueError) as e:
            log_event(f"Ignored {profile}: {e}")
    return {}


def _read_object(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)  # JSONDecodeError is a ValueError
    if not isinstance(data, dict):
        raise ValueError("must be a JSON object")
    return data


def _read() -> dict:
    cfg_file = _config_file()
    if os.path.exists(cfg_file):
        stored = _read_object(cfg_file)
        first_run = False
    else:
        stored = _first_run_config()
        first_run = True
    config = {**DEFAULT_CONFIG, **stored}

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
    config.pop("startup_delay", None)  # v1.x setting that was never used

    # Backfill per-instance fields
    for inst in config.get("instances", []):
        inst.setdefault("type", "html")
        inst.setdefault("startup_command", "")
        inst.setdefault("url", "")

    try:
        validate_config(config)
    except ValueError as e:
        # Saved before validation existed: run with it; saving in Settings shows what to fix.
        log.warning("The saved config has problems: %s", e)

    if _secure_secrets(config) or first_run:
        try:
            save(config)
        except OSError as e:
            # Read-only config folder: run with what we have rather than refuse to start.
            log.warning("Couldn't save %s: %s", cfg_file, e)
    return config


def save(config: dict) -> None:
    """Write the config atomically: a reader sees the old file or the new one, never half."""
    global _cache
    with _lock:
        path = _config_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        for _ in range(19):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # Windows: someone has the file open for a moment
                time.sleep(0.05)
        else:
            os.replace(tmp, path)  # last try: let the error out
        _cache = None


# ---------------------------------------------------------------- secrets


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
    for key in ("api_key", "trigger_key"):
        if not config[key]:
            config[key] = new_api_key()
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

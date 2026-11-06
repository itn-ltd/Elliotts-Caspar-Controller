"""Download diagnostics: one zip for the service desk, with every secret taken out."""

import glob
import io
import json
import os
import platform
import sys
import time
import zipfile

from fastapi import APIRouter
from fastapi.responses import Response

from caspar_controller import __version__
from caspar_controller.api.instances import status_payload
from caspar_controller.caspar.config_xml import instance_log_dir
from caspar_controller.settings import SECRET_KEYS, load as load_config, logs_dir, without_secrets

router = APIRouter()
CASPAR_LOG_BYTES = 2_000_000  # the end of each instance's newest CasparCG log


def _secrets(cfg: dict) -> list:
    keys = [cfg.get(k) for k in SECRET_KEYS] + [r.get("api_key") for r in cfg["remote_controllers"]]
    return sorted({k for k in keys if k}, key=len, reverse=True)


def _redact(text: str, secrets: list) -> str:
    # Belt and braces: nothing should log a key, but if a line ever does, it stops here.
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    return text


def _tail(path: str, size: int) -> str:
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - size))
        return f.read().decode("utf-8", "replace")


def build(cfg: dict) -> bytes:
    """The zip: config without secrets, versions and states, the app's logs and CasparCG's."""
    secrets = _secrets(cfg)
    status = status_payload()
    about = {
        "app_version": __version__,
        "python": sys.version.split()[0],
        "windows": platform.platform(),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "instances": [
            {k: i[k] for k in ("id", "name", "status", "caspar_version", "amcp_port")}
            for i in status["instances"]
        ],
    }
    files = {
        "config.json": json.dumps(without_secrets(cfg), indent=2),
        "about.json": json.dumps(about, indent=2),
    }
    for path in sorted(glob.glob(os.path.join(glob.escape(logs_dir()), "*.log*"))):
        files[f"logs/{os.path.basename(path)}"] = _tail(path, os.path.getsize(path))
    for inst in cfg["instances"]:
        logs = glob.glob(os.path.join(glob.escape(instance_log_dir(cfg, inst)), "*.log"))
        if logs:
            files[f"casparcg/inst_{inst['id']}.log"] = _tail(
                max(logs, key=os.path.getmtime), CASPAR_LOG_BYTES
            )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, _redact(text, secrets))
    return buf.getvalue()


@router.get("/api/diagnostics")
def api_diagnostics():
    """A zip for the service desk: config without secrets, versions and logs."""
    name = f"caspar-controller-diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=build(load_config()),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )

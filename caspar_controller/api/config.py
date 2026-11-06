"""Settings: read, save, export and import the config."""

import json
import socket

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from caspar_controller import clients, tally
from caspar_controller.api import or_400
from caspar_controller.caspar.config_xml import regenerate_all_instance_configs
from caspar_controller.log import log_event
from caspar_controller.settings import (
    MACHINE_KEYS,
    foreign_config,
    keep_remote_keys,
    load as load_config,
    save as save_config,
    validate_config,
    without_secrets,
)

router = APIRouter()


def _lan_ip() -> str:
    """This PC's address on the network (no packet is sent: UDP connect only picks a route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


@router.get("/api/config")
def api_config_get():
    cfg = load_config()
    return {
        **without_secrets(cfg),
        "password_set": bool(cfg["web_password_hash"]),
        "lan_url": f"http://{_lan_ip()}:{cfg['web_port']}",  # for trigger URLs other PCs use
    }


@router.post("/api/config")
def api_config_post(update: dict):
    cfg = load_config()
    # Only the Settings fields come back, so secrets can't be set here.
    data = {k: v for k, v in update.items() if v is not None}
    if {"amcp_base_port", "instances"} & data.keys():  # ports depend on both: check together
        data = {"amcp_base_port": cfg["amcp_base_port"], "instances": cfg["instances"], **data}
    data = or_400(validate_config, data)
    if "remote_controllers" in data:
        data["remote_controllers"] = keep_remote_keys(
            data["remote_controllers"], cfg["remote_controllers"]
        )
    cfg.update(data)
    save_config(cfg)
    if "api_clients" in data:
        clients.resolve(data["api_clients"])  # look host names up now, not in a request
    regenerate_all_instance_configs(cfg)
    tally.refresh_monitors()
    log_event("Config saved and instance configs regenerated.")
    return {"ok": True}


@router.get("/api/config/export")
def api_config_export():
    content = json.dumps(without_secrets(load_config()), indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=caspar_controller_config.json"},
    )


@router.post("/api/config/import")
async def api_config_import(request: Request):

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Config must be a JSON object")
    current = load_config()
    # Imports never change this machine's password, API key or forced-change flag.
    body = or_400(foreign_config, body, current["caspar_exe_path"])
    body["remote_controllers"] = keep_remote_keys(
        body.get("remote_controllers", []), current["remote_controllers"]
    )
    # This PC's allowed addresses too: another gallery's would lock out this one's Automator.
    body.update({k: current[k] for k in (*MACHINE_KEYS, "api_clients")})
    save_config(body)
    regenerate_all_instance_configs(load_config())  # fills defaults the import left out
    log_event("Config imported via web UI.")
    return {"ok": True}

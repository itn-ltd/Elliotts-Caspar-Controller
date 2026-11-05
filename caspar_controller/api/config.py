"""Settings: read, save, export and import the config."""

import json
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from caspar_controller import tally
from caspar_controller.api import or_400
from caspar_controller.caspar.config_xml import regenerate_all_instance_configs
from caspar_controller.log import log_event
from caspar_controller.settings import (
    MACHINE_KEYS,
    keep_remote_keys,
    load as load_config,
    save as save_config,
    validate_config,
    without_secrets,
)

router = APIRouter()


class ConfigUpdate(BaseModel):
    caspar_exe_path: Optional[str] = None
    amcp_base_port: Optional[int] = None
    web_port: Optional[int] = None
    video_mode: Optional[str] = None
    autostart_caspar: Optional[bool] = None
    media_path: Optional[str] = None
    server_name: Optional[str] = None
    instances: Optional[list] = None
    remote_controllers: Optional[list] = None


@router.get("/api/config")
def api_config_get():
    cfg = load_config()
    return {**without_secrets(cfg), "password_set": bool(cfg["web_password_hash"])}


@router.post("/api/config")
def api_config_post(update: ConfigUpdate):
    cfg = load_config()
    data = update.model_dump(exclude_none=True)
    or_400(validate_config, data)
    if "remote_controllers" in data:
        data["remote_controllers"] = keep_remote_keys(
            data["remote_controllers"], cfg["remote_controllers"]
        )
    # Renumber instances by array order so the web UI can reorder freely
    if "instances" in data:
        for i, inst in enumerate(data["instances"], start=1):
            inst["id"] = i
    cfg.update(data)
    save_config(cfg)
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
        headers={"Content-Disposition": "attachment; filename=elliotts_caspar_config.json"},
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
    # Another PC's CasparCG path is meaningless here: keep this PC's.
    if "caspar_exe_path" in body and not os.path.isfile(body["caspar_exe_path"] or ""):
        body["caspar_exe_path"] = current["caspar_exe_path"]
    or_400(validate_config, body)
    # Imports never change this machine's password, API key or forced-change flag.
    body = {k: v for k, v in body.items() if k not in MACHINE_KEYS}
    body["remote_controllers"] = keep_remote_keys(
        body.get("remote_controllers", []), current["remote_controllers"]
    )
    body.update({k: current[k] for k in MACHINE_KEYS})
    save_config(body)
    regenerate_all_instance_configs(load_config())  # fills defaults the import left out
    log_event("Config imported via web UI.")
    return {"ok": True}

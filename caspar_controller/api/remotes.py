"""Remote Controllers: dashboard status, media and actions on other controllers."""

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from caspar_controller.api import or_400
from caspar_controller.remotes import call_remote, remote_status
from caspar_controller.settings import keep_remote_keys, load as load_config, validate_remote_url

router = APIRouter()


def _get_remote(idx: int) -> dict:
    remotes = load_config().get("remote_controllers", [])
    if idx < 0 or idx >= len(remotes):
        raise HTTPException(status_code=404, detail=f"Remote {idx} not found")
    return remotes[idx]


@router.get("/api/remotes")
def api_remotes():
    cfg = load_config()
    remotes = cfg.get("remote_controllers", [])
    if not remotes:
        return {"remotes": []}
    with ThreadPoolExecutor(max_workers=len(remotes)) as ex:
        return {"remotes": list(ex.map(remote_status, range(len(remotes)), remotes))}


@router.get("/api/remote/{idx}/media")
def api_remote_media_proxy(idx: int):
    remote = _get_remote(idx)
    try:
        return call_remote(remote, "GET", "/api/media", timeout=5).json()
    except Exception:
        return {"clips": [], "error": "Could not reach remote"}


class _RemoteTestRequest(BaseModel):
    url: str
    api_key: str = ""


@router.post("/api/remote/test")
def api_remote_test(req: _RemoteTestRequest):
    # A blank key means "use the saved one" (Settings never shows saved remote keys).
    or_400(validate_remote_url, req.url)
    remote = keep_remote_keys([req.model_dump()], load_config()["remote_controllers"])[0]
    try:
        r = call_remote(remote, "GET", "/api/status", timeout=3)
        if r.status_code == 401:
            return {"online": True, "authorised": False}
        data = r.json()
        return {
            "online": True,
            "authorised": True,
            "server_name": data.get("server_name", ""),
            "version": data.get("version", ""),
        }
    except Exception:
        return {"online": False}


class _RemoteProxyRequest(BaseModel):
    path: str
    body: Optional[dict] = None


# The only remote actions the dashboard sends (Start/Stop All, per-instance start/stop/AMCP).
_REMOTE_ACTIONS = re.compile(r"/api/(server/(start|stop)|instance/(\d+|all)/(restart|stop|amcp))")


@router.post("/api/remote/{idx}/proxy")
def api_remote_proxy(idx: int, req: _RemoteProxyRequest):
    if not _REMOTE_ACTIONS.fullmatch(req.path):
        raise HTTPException(status_code=400, detail="Invalid remote path")
    remote = _get_remote(idx)
    try:
        r = call_remote(remote, "POST", req.path, timeout=5, json=req.body)
        if r.status_code == 401:
            # 502, not 401: a 401 here would sign this browser out.
            raise HTTPException(status_code=502, detail="Remote rejected its API key")
        return r.json()
    except (requests.RequestException, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"Remote unreachable: {e}") from e

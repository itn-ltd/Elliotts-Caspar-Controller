"""Remote Controllers: dashboard status, media and explicit actions on other controllers."""

from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, HTTPException

from caspar_controller import remotes
from caspar_controller.api.instances import CommandRequest, command_or_400
from caspar_controller.settings import load as load_config

router = APIRouter()


def _get_remote(idx: int) -> dict:
    saved = load_config().get("remote_controllers", [])
    if idx < 0 or idx >= len(saved):
        raise HTTPException(status_code=404, detail=f"Remote {idx} not found")
    return saved[idx]


def _relay(call, *args):
    """Run a remote call, turning its failures into a 502 (a 401 here would sign us out)."""
    try:
        return call(*args)
    except remotes.RemoteKeyRejected:
        raise HTTPException(status_code=502, detail="Remote rejected its API key") from None
    except remotes.ERRORS as e:
        raise HTTPException(status_code=502, detail=f"Remote failed: {e}") from None


@router.get("/api/remotes")
def api_remotes():
    """Each Remote Controller's status, without its key."""
    saved = load_config().get("remote_controllers", [])
    if not saved:
        return {"remotes": []}
    with ThreadPoolExecutor(max_workers=len(saved)) as ex:
        return {"remotes": list(ex.map(remotes.remote_status, range(len(saved)), saved))}


@router.get("/api/remote/{idx}/media")
def api_remote_media(idx: int):
    """A Remote Controller's clips."""
    remote = _get_remote(idx)
    try:
        return remotes.media(remote)
    except remotes.ERRORS:
        return {"clips": [], "error": "Could not reach remote"}


@router.post("/api/remote/{idx}/test")
def api_remote_test(idx: int):
    """Settings' Test button, for a saved remote: reachable, and is its key accepted?"""
    remote = _get_remote(idx)
    try:
        info = remotes.health(remote)
        remotes.media(remote)  # a keyed call: health alone needs no key
        authorised = True
    except remotes.RemoteKeyRejected:  # from either: e.g. a proxy that wants a key for health
        info, authorised = {}, False
    except remotes.ERRORS:
        return {"online": False}
    return {
        "online": True,
        "authorised": authorised,
        "server_name": info.get("server_name", ""),
        "version": info.get("version", ""),
    }


@router.post("/api/remote/{idx}/server/{action}")
def api_remote_server(idx: int, action: Literal["start", "stop"]):
    """Start All or Stop All on a Remote Controller."""
    return _relay(remotes.server_action, _get_remote(idx), action)


# Before /{action}, which would otherwise capture "amcp".
@router.post("/api/remote/{idx}/instance/{inst_id}/amcp")
def api_remote_amcp(idx: int, inst_id: int, req: CommandRequest):
    """Send a raw AMCP command to an output on a Remote Controller."""
    return _relay(remotes.amcp, _get_remote(idx), inst_id, command_or_400(req))


@router.post("/api/remote/{idx}/instance/{inst_id}/{action}")
def api_remote_instance(idx: int, inst_id: int, action: Literal["restart", "stop"]):
    """Restart or stop one output on a Remote Controller."""
    return _relay(remotes.instance_action, _get_remote(idx), inst_id, action)

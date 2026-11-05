"""Instance control: status, start, stop, restart and raw AMCP."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from caspar_controller import __version__, instances, tally
from caspar_controller.api import instance_or_404
from caspar_controller.caspar.amcp import has_control_chars
from caspar_controller.log import log_event
from caspar_controller.settings import auto_restart_on, load as load_config, save as save_config

router = APIRouter()


@router.get("/api/status")
def api_status():
    return status_payload()


def status_payload() -> dict:
    """Every instance's state, from the background poll (no AMCP calls here)."""
    cfg = load_config()
    insts = cfg.get("instances", [])
    probes = instances.manager.cached_status()
    tally_state = tally.get_tally()

    instances_out = []
    for inst in insts:
        on_air = tally_state.get(inst["ndi_name"], {})
        probe = probes.get(inst["id"])
        instances_out.append(
            {
                "id": inst["id"],
                "name": inst["name"],
                "ndi_name": inst["ndi_name"],
                "type": inst.get("type", "html"),
                "url": inst.get("url", ""),
                "startup_command": inst.get("startup_command", ""),
                "amcp_port": instances.manager.port(cfg, inst),
                "status": "live"
                if probe and probe.live
                else instances.manager.state_of(inst["id"]) or "stopped",
                "layer": probe.layer if probe else {},
                "caspar_version": probe.version if probe else "",
                "auto_restart": auto_restart_on(cfg, inst),
                "console_lines": instances.manager.console(inst["id"]).count,
                "on_program": on_air.get("program", False),
                "on_preview": on_air.get("preview", False),
            }
        )

    any_running = any(i["status"] == "live" for i in instances_out)
    needs_setup = not cfg.get("caspar_exe_path") or not insts
    return {
        "running": any_running,
        "version": __version__,
        "instances": instances_out,
        "needs_setup": needs_setup,
        "server_name": cfg.get("server_name", ""),
        "tally_available": tally.is_available(),
    }


# Start, stop and restart are jobs: they return 202 now, and progress goes to the event log.


@router.post("/api/server/start", status_code=202)
def api_server_start():
    if not load_config().get("instances", []):
        raise HTTPException(status_code=400, detail="No instances configured")
    instances.manager.start_all()
    return {"ok": True, "message": "Starting instances..."}


@router.post("/api/server/stop", status_code=202)
def api_server_stop():
    instances.manager.stop_all()
    return {"ok": True, "message": "Stopping all instances..."}


@router.post("/api/instance/all/restart", status_code=202)
def api_instance_restart_all():  # before {inst_id}, which would capture "all"
    log_event("Restarting all instances...")
    instances.manager.restart_all()
    return {"ok": True, "message": "Restarting all instances..."}


@router.post("/api/instance/{inst_id}/restart", status_code=202)
def api_instance_restart(inst_id: int):
    name = instance_or_404(load_config(), inst_id)["name"]
    log_event(f"Restarting inst {inst_id} ({name})...")
    instances.manager.restart(inst_id)
    return {"ok": True, "message": f"Restarting {name}..."}


@router.post("/api/instance/{inst_id}/stop", status_code=202)
def api_instance_stop(inst_id: int):
    name = instance_or_404(load_config(), inst_id)["name"]
    instances.manager.stop(inst_id)
    return {"ok": True, "message": f"Stopping {name}..."}


class CommandRequest(BaseModel):
    command: str


def _command(req: CommandRequest) -> str:
    command = req.command.strip()
    if not command or has_control_chars(command):  # CR/LF would smuggle in a second command
        raise HTTPException(status_code=400, detail="Enter one command, with no control characters")
    return command


@router.post("/api/instance/{inst_id}/amcp")
def api_instance_amcp(inst_id: int, req: CommandRequest):
    cfg = load_config()
    inst = instance_or_404(cfg, inst_id)
    command = _command(req)
    res = instances.manager.client(cfg, inst).send(command)
    log_event(f"Inst {inst_id} AMCP: {command[:60]} → {res[:60]}")
    return {"ok": True, "response": res}


@router.post("/api/instance/{inst_id}/console")
def api_instance_console(inst_id: int, req: CommandRequest):
    """Type into CasparCG's own console. Signed-in operators only (see auth.py)."""
    instance_or_404(load_config(), inst_id)
    command = _command(req)
    try:
        instances.manager.send_console(inst_id, command)
    except OSError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    log_event(f"Inst {inst_id} console: {command[:60]}")
    return {"ok": True}


class AutoRestartRequest(BaseModel):
    auto_restart: Literal["on", "off", "inherit"]


@router.post("/api/instance/{inst_id}/auto-restart")
def api_instance_auto_restart(inst_id: int, req: AutoRestartRequest):
    """The card's quick toggle: this instance's override of the global setting."""
    cfg = load_config()
    instance_or_404(cfg, inst_id)["auto_restart"] = req.auto_restart
    save_config(cfg)
    log_event(f"Inst {inst_id} auto-restart: {req.auto_restart}")
    return {"ok": True, "auto_restart": auto_restart_on(cfg, instance_or_404(cfg, inst_id))}

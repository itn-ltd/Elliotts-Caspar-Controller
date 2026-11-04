"""Instance control: status, start, stop, restart and raw AMCP."""

import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from caspar_controller import __version__, instances, tally
from caspar_controller.caspar.amcp import AMCPClient, has_control_chars
from caspar_controller.log import log_event
from caspar_controller.settings import instance_amcp_port, load as load_config

router = APIRouter()


@router.get("/api/status")
def api_status():
    cfg = load_config()
    insts = cfg.get("instances", [])

    # Read from background cache — returns in <1ms rather than blocking on AMCP pings
    live, layer_info = instances.cached_status()

    instances_out = []
    for inst in insts:
        port = instance_amcp_port(cfg, inst)
        running = live.get(inst["id"], False)
        li = layer_info.get(inst["id"], {})
        instances_out.append(
            {
                "id": inst["id"],
                "name": inst["name"],
                "ndi_name": inst["ndi_name"],
                "type": inst.get("type", "html"),
                "url": inst.get("url", ""),
                "startup_command": inst.get("startup_command", ""),
                "amcp_port": port,
                "status": "live" if running else "stopped",
                "layer": li,
            }
        )
    # Merge tally state into each instance
    tally_state = tally.get_tally()
    for inst in instances_out:
        t = tally_state.get(inst["ndi_name"], {})
        inst["on_program"] = t.get("program", False)
        inst["on_preview"] = t.get("preview", False)

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


@router.post("/api/server/start")
def api_server_start():
    cfg = load_config()
    if not cfg.get("instances", []):
        raise HTTPException(status_code=400, detail="No instances configured")
    started, errors = instances.start_all(cfg)
    if not started:
        raise HTTPException(status_code=500, detail="All instances failed to start")
    msg = f"Started {len(started)}/{len(cfg['instances'])} instances"
    if errors:
        msg += f" (failed: {errors})"
    log_event(msg)
    return {"ok": True, "message": msg}


@router.post("/api/server/stop")
def api_server_stop():
    instances.stop_all()
    return {"ok": True}


@router.post("/api/instance/{inst_id}/restart")
def api_instance_restart(inst_id: int):
    cfg = load_config()
    inst_map = {i["id"]: i for i in cfg.get("instances", [])}
    if inst_id not in inst_map:
        raise HTTPException(status_code=404, detail=f"Instance {inst_id} not found")
    name = inst_map[inst_id]["name"]
    log_event(f"Restarting inst {inst_id} ({name})...")
    threading.Thread(target=instances.restart, args=(inst_id,), daemon=True).start()
    return {"ok": True, "message": f"Restarting {name}..."}


@router.post("/api/instance/all/restart")
def api_instance_restart_all():
    cfg = load_config()
    log_event("Restarting all instances...")
    threading.Thread(target=instances.restart_all, args=(cfg,), daemon=True).start()
    return {"ok": True, "message": "Restarting all instances..."}


@router.post("/api/instance/{inst_id}/stop")
def api_instance_stop(inst_id: int):
    cfg = load_config()
    inst_map = {i["id"]: i for i in cfg.get("instances", [])}
    if inst_id not in inst_map:
        raise HTTPException(status_code=404, detail=f"Instance {inst_id} not found")
    inst = inst_map[inst_id]
    port = instance_amcp_port(cfg, inst)
    threading.Thread(target=instances.stop, args=(inst, port), daemon=True).start()
    return {"ok": True, "message": f"Stopping {inst['name']}..."}


class AMCPCommandRequest(BaseModel):
    command: str


@router.post("/api/instance/{inst_id}/amcp")
def api_instance_amcp(inst_id: int, req: AMCPCommandRequest):
    cfg = load_config()
    inst_map = {i["id"]: i for i in cfg.get("instances", [])}
    if inst_id not in inst_map:
        raise HTTPException(status_code=404, detail=f"Instance {inst_id} not found")
    port = instance_amcp_port(cfg, inst_map[inst_id])
    if has_control_chars(req.command.strip()):
        raise HTTPException(
            status_code=400, detail="AMCP commands can't contain control characters"
        )
    res = AMCPClient(port=port).send(req.command.strip())
    log_event(f"Inst {inst_id} AMCP: {req.command[:60]} → {res[:60]}")
    return {"ok": True, "response": res}

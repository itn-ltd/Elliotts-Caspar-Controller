"""Media clips: listing them and playing them from HTTP triggers."""

import os

from fastapi import APIRouter, HTTPException

from caspar_controller import instances
from caspar_controller.api import instance_or_404
from caspar_controller.caspar.amcp import is_amcp_safe
from caspar_controller.log import log_event
from caspar_controller.settings import load as load_config

router = APIRouter()


def _play_clip(cfg: dict, inst: dict, clip: str, loop: bool) -> tuple[str, str]:
    """Play a clip on a media instance. Returns (AMCP command, reply)."""
    if not is_amcp_safe(clip):
        raise HTTPException(
            status_code=400,
            detail="Clip names can't contain quotes, backslashes or control characters",
        )
    cmd = f'PLAY 1-1 "{clip}"' + (" LOOP" if loop else "")
    return cmd, instances.manager.client(cfg, inst).send(cmd)


@router.get("/api/load")
def api_load_clip(instance: str, clip: str, loop: bool = True):
    """Load a clip into a named media instance via HTTP GET.

    Usage: GET /api/load?instance=Clipplayer&clip=MYCLIP
    Optional: &loop=false to play once without looping.

    Designed for external triggers (hardware controllers, automation systems).
    """
    cfg = load_config()
    inst = next(
        (i for i in cfg.get("instances", []) if i["name"].lower() == instance.lower()),
        None,
    )
    if not inst:
        raise HTTPException(status_code=404, detail=f"Instance '{instance}' not found")
    if inst.get("type", "html") != "media":
        raise HTTPException(
            status_code=400,
            detail=f"Instance '{instance}' is not a media type — only media instances support clip loading",
        )
    cmd, res = _play_clip(cfg, inst, clip, loop)
    log_event(f"HTTP load: {instance} → {cmd} → {res[:60]}")
    return {"ok": True, "instance": instance, "clip": clip, "command": cmd, "response": res}


@router.get("/api/instance/{inst_id}/load")
def api_instance_load_clip(inst_id: int, clip: str, loop: bool = True):
    """Load a clip into a media instance by ID via HTTP GET.

    Usage: GET /api/instance/6/load?clip=MYCLIP
    """
    cfg = load_config()
    inst = instance_or_404(cfg, inst_id)
    if inst.get("type", "html") != "media":
        raise HTTPException(status_code=400, detail=f"Instance {inst_id} is not a media type")
    cmd, res = _play_clip(cfg, inst, clip, loop)
    log_event(f"HTTP load: Inst {inst_id} ({inst['name']}) → {cmd} → {res[:60]}")
    return {"ok": True, "instance": inst["name"], "clip": clip, "command": cmd, "response": res}


@router.get("/api/media")
def api_media():
    """The clips in the media folder."""
    cfg = load_config()
    custom_media = cfg.get("media_path", "").strip()
    if custom_media and os.path.isdir(custom_media):
        media_dir = custom_media
    else:
        exe = cfg.get("caspar_exe_path", "")
        if not exe or not os.path.isfile(exe):
            return {"clips": [], "error": "CasparCG exe path not set"}
        media_dir = os.path.join(os.path.dirname(exe), "media")
    if not os.path.isdir(media_dir):
        return {"clips": [], "error": f"Media folder not found: {media_dir}"}
    MEDIA_EXTS = {
        ".mp4",
        ".mov",
        ".avi",
        ".mxf",
        ".mkv",
        ".wmv",
        ".flv",
        ".png",
        ".jpg",
        ".jpeg",
        ".tga",
        ".bmp",
        ".gif",
    }
    clips = []
    for root, _, files in os.walk(media_dir):
        for fname in sorted(files):
            ext = os.path.splitext(fname)[1].lower()
            if ext in MEDIA_EXTS:
                rel = os.path.relpath(os.path.join(root, fname), media_dir)
                clip_name = os.path.splitext(rel)[0].replace(os.sep, "/").upper()
                clips.append(clip_name)
    return {"clips": sorted(clips), "media_dir": media_dir}

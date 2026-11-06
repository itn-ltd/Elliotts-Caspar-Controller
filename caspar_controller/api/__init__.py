"""The web server: HTTP API and pages."""

from fastapi import HTTPException


def or_400(check, *args):
    """Run a validator and return its result, turning its ValueError into a 400."""
    try:
        return check(*args)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


def instance_or_404(cfg: dict, inst_id: int) -> dict:
    inst = next((i for i in cfg.get("instances", []) if i["id"] == inst_id), None)
    if not inst:
        raise HTTPException(status_code=404, detail=f"Instance {inst_id} not found")
    return inst

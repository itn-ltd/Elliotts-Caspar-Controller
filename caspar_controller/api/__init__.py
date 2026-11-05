"""The web server: HTTP API and pages."""

from fastapi import HTTPException


def or_400(check, *args) -> None:
    """Run a validator, turning its ValueError into a 400."""
    try:
        check(*args)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


def instance_or_404(cfg: dict, inst_id: int) -> dict:
    inst = next((i for i in cfg.get("instances", []) if i["id"] == inst_id), None)
    if not inst:
        raise HTTPException(status_code=404, detail=f"Instance {inst_id} not found")
    return inst

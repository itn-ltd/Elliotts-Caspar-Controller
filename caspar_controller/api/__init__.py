"""The web server: HTTP API and pages."""

from fastapi import HTTPException


def or_400(check, *args) -> None:
    """Run a validator, turning its ValueError into a 400."""
    try:
        check(*args)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

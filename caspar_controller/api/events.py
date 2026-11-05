"""Dashboard feeds: the event log and NDI tally."""

from fastapi import APIRouter

from caspar_controller import log, tally

router = APIRouter()


@router.get("/api/log")
def api_log():
    return {"log": log.entries()}


@router.get("/api/tally")
def api_tally():
    return {
        "available": tally.is_available(),
        "tally": tally.get_tally(),
    }

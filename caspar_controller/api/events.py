"""Dashboard feeds: the event log, NDI tally and the live /api/events stream."""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from caspar_controller import log, tally
from caspar_controller.api.instances import status_payload

router = APIRouter()

TICK = 0.25  # seconds between checks for something new to send
LIFETIME = 300  # then end the stream; EventSource reconnects by itself


@router.get("/api/log")
def api_log():
    return {"log": log.entries()}


@router.get("/api/tally")
def api_tally():
    return {
        "available": tally.is_available(),
        "tally": tally.get_tally(),
    }


async def event_stream(request: Request):
    """Server-sent events: "status" and "log", each sent when it changes."""
    last: dict = {}
    for _ in range(int(LIFETIME / TICK)):
        if await request.is_disconnected():
            return
        for name, data in (("status", status_payload()), ("log", {"log": log.entries()})):
            if last.get(name) != data:
                last[name] = data
                yield f"event: {name}\ndata: {json.dumps(data)}\n\n"
        await asyncio.sleep(TICK)


@router.get("/api/events")
def api_events(request: Request):
    return StreamingResponse(
        event_stream(request), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )

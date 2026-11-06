"""Dashboard feeds: the event log, NDI tally and the live /api/events stream."""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from caspar_controller import log, tally
from caspar_controller.api.auth import still_allowed
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
    yield "retry: 500\n\n"  # after each planned end, reconnect in 0.5 s, not the browser's 3-5 s
    for tick in range(int(LIFETIME / TICK)):
        if await request.is_disconnected():
            return
        # Auth ran when the stream opened: stop if that sign-in or key has since gone.
        if tick % 4 == 0 and not still_allowed(request):
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

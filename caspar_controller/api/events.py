"""Dashboard feeds: the event log, NDI tally and the live /api/events stream."""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from caspar_controller import instances, log, tally
from caspar_controller.api.auth import still_allowed
from caspar_controller.api.instances import status_payload
from caspar_controller.log import Console
from caspar_controller.settings import load as load_config

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


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


async def event_stream(request: Request, console: Optional[Console] = None):
    """Server-sent events: "status" and "log" when they change, plus "console" lines.

    The first "console" event holds every line still kept, later ones only new lines.
    """
    last: dict = {}
    seen = 0
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
                yield _event(name, data)
        if console:
            lines, seen = console.since(seen)
            if lines:
                yield _event("console", {"lines": lines})
        await asyncio.sleep(TICK)


@router.get("/api/events")
def api_events(request: Request, console: Optional[int] = None):
    """?console=<instance id> adds that instance's CasparCG log lines.

    An id that isn't configured (say, a deleted instance a browser remembers) is ignored, not
    refused: a refused stream makes the page reload, and it would remember the same id.
    """
    known = any(i["id"] == console for i in load_config().get("instances", []))
    output = instances.manager.console(console) if known else None
    return StreamingResponse(
        event_stream(request, output),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )

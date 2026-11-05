"""The FastAPI app: routers, auth middleware and static files."""

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from caspar_controller import __version__, instances, tally
from caspar_controller.api import (
    auth,
    config,
    events,
    instances as instances_api,
    media,
    pages,
    remotes,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tally.refresh_monitors()
    instances.start_status_worker()
    yield
    instances.stop_status_worker()
    tally.stop_all()


app = FastAPI(title="Elliott's Caspar Controller", version=__version__, lifespan=lifespan)
app.add_middleware(auth.AuthMiddleware)
ROUTERS = tuple(m.router for m in (auth, config, events, instances_api, media, remotes, pages))
for router in ROUTERS:
    app.include_router(router)


def _app_root() -> str:
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


STATIC_DIR = os.path.join(_app_root(), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
def api_health():
    return {"ok": True, "version": __version__}

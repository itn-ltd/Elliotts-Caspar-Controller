"""The FastAPI app: routers, auth middleware and static files."""

import os
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
from caspar_controller.settings import load as load_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    tally.refresh_monitors()
    instances.manager.start_status_worker()
    yield
    instances.manager.stop_status_worker()
    tally.stop_all()


app = FastAPI(title="Elliott's Caspar Controller", version=__version__, lifespan=lifespan)
app.add_middleware(auth.AuthMiddleware)
ROUTERS = tuple(m.router for m in (auth, config, events, instances_api, media, remotes, pages))
for router in ROUTERS:
    app.include_router(router)


STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/web", StaticFiles(directory=pages.WEB_DIR), name="web")


@app.get("/api/health")
def api_health():
    # Open without sign-in: the login page shows the server name too.
    return {"ok": True, "version": __version__, "server_name": load_config()["server_name"]}

"""The FastAPI app: routers, auth middleware and static files."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from caspar_controller import __version__, clients, instances, tally
from caspar_controller.api import (
    auth,
    config,
    diagnostics,
    events,
    instances as instances_api,
    media,
    pages,
    remotes,
)
from caspar_controller.api.instances import status_payload
from caspar_controller.settings import load as load_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    tally.refresh_monitors()
    clients.refresh_in_background(load_config()["api_clients"])  # DNS can't delay start-up
    instances.manager.start_status_worker()
    yield
    instances.manager.stop_status_worker()
    tally.stop_all()


app = FastAPI(title="Caspar Controller", version=__version__, lifespan=lifespan)
app.add_middleware(auth.AuthMiddleware)
ROUTERS = tuple(
    m.router for m in (auth, config, diagnostics, events, instances_api, media, remotes, pages)
)
for router in ROUTERS:
    app.include_router(router)


STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/web", StaticFiles(directory=pages.WEB_DIR), name="web")


@app.get("/api/health")
def api_health():
    """Open health check for monitoring: versions and each output's state by id.

    The sign-in page and Remote Controllers use it too. It's open, so nothing sensitive: the
    server name is shown before sign-in anyway, and outputs are by id, with no names, URLs,
    ports or settings.
    """
    insts = status_payload()["instances"]
    return {
        "ok": True,
        "version": __version__,
        "server_name": load_config()["server_name"],
        "caspar_versions": sorted({i["caspar_version"] for i in insts if i["caspar_version"]}),
        "instances": [{"id": i["id"], "state": i["status"]} for i in insts],
    }

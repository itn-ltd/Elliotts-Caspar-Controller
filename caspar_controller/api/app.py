"""The FastAPI app: routers, auth middleware, static files and the server thread."""

import os
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
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


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

_server_thread: Optional[threading.Thread] = None
_uvicorn_server: Optional[uvicorn.Server] = None


def start_server(port: int = 5280, open_browser: bool = True) -> None:
    global _server_thread, _uvicorn_server
    cfg_obj = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning", access_log=False)
    _uvicorn_server = uvicorn.Server(cfg_obj)

    def run():
        _uvicorn_server.run()

    _server_thread = threading.Thread(target=run, daemon=True)
    _server_thread.start()

    if open_browser:
        time.sleep(1.5)
        webbrowser.open(f"http://127.0.0.1:{port}")


def stop_server() -> None:
    if _uvicorn_server:
        _uvicorn_server.should_exit = True

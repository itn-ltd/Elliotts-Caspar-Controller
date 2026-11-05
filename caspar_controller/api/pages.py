"""The web UI pages: static files in web/, behind the sign-in middleware."""

import os

from fastapi import APIRouter
from fastapi.responses import FileResponse

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
PAGES = {
    "/": "index",
    "/settings": "settings",
    "/http-generator": "http-generator",
    "/login": "login",
    "/change-password": "change-password",
}

router = APIRouter()


def web_page(name: str, status_code: int = 200) -> FileResponse:
    # no-cache: after an upgrade, browsers check for the new page instead of reusing the old one.
    path = os.path.join(WEB_DIR, f"{name}.html")
    return FileResponse(path, status_code=status_code, headers={"Cache-Control": "no-cache"})


for _path, _name in PAGES.items():
    router.add_api_route(
        _path, lambda name=_name: web_page(name), methods=["GET"], include_in_schema=False
    )

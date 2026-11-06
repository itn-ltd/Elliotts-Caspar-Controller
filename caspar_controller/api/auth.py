"""Sign-in, sessions, the web UI password and the API key."""

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from caspar_controller.api import or_400
from caspar_controller.api.pages import web_page
from caspar_controller.auth import key_matches, new_api_key, verify_password
from caspar_controller.log import log_event
from caspar_controller.settings import load as load_config, save as save_config, set_password

router = APIRouter()

_sessions: set = set()  # active session tokens for web UI auth


def _host_name(request: Request) -> str:
    host = request.headers.get("host", "")
    return host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]


def _trusted_local(request: Request) -> bool:
    """First-run trust: a browser on this PC, addressing it as localhost, on this site."""
    return (
        bool(request.client)
        and request.client.host in ("127.0.0.1", "::1")
        # Host stops DNS rebinding; Sec-Fetch-Site stops other sites' pages (CSRF).
        and _host_name(request) in ("127.0.0.1", "localhost", "[::1]")
        and request.headers.get("sec-fetch-site", "none") in ("none", "same-origin")
    )


_CHANGE_PASSWORD_PATHS = ("/change-password", "/api/password", "/logout")
# Account control and CasparCG's own console: never with the API key, which lives in
# panel configs and URLs.
_SESSION_ONLY_PATHS = ("/api/password", "/api/key", "/api/key/rotate")


def _session_only(path: str) -> bool:
    return path in _SESSION_ONLY_PATHS or (
        path.startswith("/api/instance/") and path.endswith("/console")
    )


def _supplied_key(request: Request) -> str | None:
    return request.headers.get("x-api-key") or request.query_params.get("api_key")


def still_allowed(request: Request) -> bool:
    """For a long-lived stream: is the sign-in or key it opened with still good?"""
    token = request.cookies.get("session")
    if token:
        return token in _sessions  # sign-out and password changes end sessions
    cfg = load_config()
    supplied = _supplied_key(request)
    if supplied:
        return key_matches(supplied, cfg["api_key"])
    return not cfg["web_password_hash"] and _trusted_local(request)


class AuthMiddleware(BaseHTTPMiddleware):
    """API needs a key or session; pages need a session (or this PC, until a password is set)."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/login", "/api/health") or path.startswith(("/static/", "/web/")):
            return await call_next(request)
        cfg = load_config()
        first_run = not cfg["web_password_hash"]
        if request.cookies.get("session", "") in _sessions:
            if cfg["password_change_required"] and path not in _CHANGE_PASSWORD_PATHS:
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "Password change required"}, status_code=403)
                return RedirectResponse(url="/change-password")
            return await call_next(request)
        if first_run and _trusted_local(request):
            return await call_next(request)
        if path.startswith("/api/"):
            supplied = _supplied_key(request)
            if not _session_only(path) and key_matches(supplied, cfg["api_key"]):
                return await call_next(request)
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
        if first_run:
            return web_page("setup", status_code=403)
        return RedirectResponse(url="/login")


class _PasswordRequest(BaseModel):
    password: str


class _PasswordChange(BaseModel):
    password: str
    current_password: str = ""


@router.get("/api/key")
def api_key_get():
    return {"api_key": load_config()["api_key"]}


@router.post("/api/key/rotate")
def api_key_rotate():
    cfg = load_config()
    cfg["api_key"] = new_api_key()
    save_config(cfg)
    log_event("API key rotated.")
    return {"api_key": cfg["api_key"]}


@router.post("/api/password")
def api_password(req: _PasswordChange, request: Request):
    cfg = load_config()
    # The current password is skipped only on first run and the forced first change.
    stored = cfg["web_password_hash"]
    if stored and not cfg["password_change_required"]:
        if not verify_password(req.current_password, stored):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
    or_400(set_password, cfg, req.password)
    save_config(cfg)
    # Sign out every other session: the old password may have leaked.
    _sessions.intersection_update({request.cookies.get("session", "")})
    log_event("Web UI password changed.")
    return {"ok": True}


@router.post("/login")
def api_login(req: _PasswordRequest, response: Response):
    stored = load_config()["web_password_hash"]
    if not stored:
        raise HTTPException(status_code=400, detail="No password is set yet")
    if not verify_password(req.password, stored):
        raise HTTPException(status_code=401, detail="Incorrect password")
    token = secrets.token_urlsafe(32)
    _sessions.add(token)
    response.set_cookie("session", token, httponly=True, samesite="strict", max_age=86400 * 30)
    return {"ok": True}


@router.post("/logout")
def api_logout(request: Request, response: Response):
    _sessions.discard(request.cookies.get("session", ""))
    response.delete_cookie("session")
    return {"ok": True}

"""Sign-in, sessions, the web UI password, the API keys and who may use them."""

import re
import secrets
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from caspar_controller import clients
from caspar_controller.api import or_400
from caspar_controller.api.pages import web_page
from caspar_controller.auth import key_matches, new_api_key, verify_password
from caspar_controller.log import log_event
from caspar_controller.settings import load as load_config, save as save_config, set_password
from caspar_controller.throttle import Throttle

router = APIRouter()

SESSION_MAX = 30 * 86400  # seconds a sign-in lasts, on the server as in the cookie
_sessions: dict = {}  # session token -> when it was created
# Wrong passwords from one address: 5 in a minute, then that address is refused for a minute.
_sign_in_failures = Throttle()
# Wrong API keys only limit logging. Keys are 256-bit random, so guessing isn't the risk; a
# stale saved key is (a Remote Controller still has a rotated key), and blocking its address
# would lock out every other device there, Companion with the right key included.
_wrong_key_log = Throttle()


def _session_ok(token: str) -> bool:
    """A known session that hasn't outlived SESSION_MAX (a copied token doesn't last forever)."""
    created = _sessions.get(token)
    if created is None:
        return False
    if time.monotonic() - created > SESSION_MAX:  # monotonic: a clock change can't expire them
        _sessions.pop(token, None)
        return False
    return True


def _host(request: Request) -> str:
    return request.client.host if request.client else ""


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
# Account control: never with the API key, which lives in panel configs and URLs.
_SESSION_ONLY_PATHS = ("/api/password", "/api/key", "/api/key/rotate")


def _supplied_key(request: Request) -> str | None:
    return request.headers.get("x-api-key") or request.query_params.get("api_key")


def still_allowed(request: Request) -> bool:
    """For a long-lived stream: is the sign-in or key it opened with still good?"""
    token = request.cookies.get("session")
    if token:
        return _session_ok(token)  # sign-out, password changes and age end sessions
    cfg = load_config()
    supplied = _supplied_key(request)
    if supplied:
        return key_matches(supplied, cfg["api_key"])
    return not cfg["web_password_hash"] and _trusted_local(request)


# All the trigger key may do: fire clips, as Automator and panels do. The template routes
# join this list in #26.
_TRIGGER_PATH = re.compile(r"/api/(load|instance/\d+/load)")


def _refuse(request: Request, reason: str, status: int = 403) -> JSONResponse:
    log_event(f"Refused {request.url.path} from {_host(request) or '?'}: {reason}")  # never the key
    return JSONResponse({"detail": reason}, status_code=status)


class AuthMiddleware(BaseHTTPMiddleware):
    """API needs a key or session; pages need a session (or this PC, until a password is set)."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/login", "/api/health") or path.startswith(("/static/", "/web/")):
            return await call_next(request)
        cfg = load_config()
        first_run = not cfg["web_password_hash"]
        if _session_ok(request.cookies.get("session", "")):
            if cfg["password_change_required"] and path not in _CHANGE_PASSWORD_PATHS:
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "Password change required"}, status_code=403)
                return RedirectResponse(url="/change-password")
            return await call_next(request)
        if first_run and _trusted_local(request):
            return await call_next(request)
        if path.startswith("/api/"):
            supplied = _supplied_key(request)
            host = _host(request)
            full = key_matches(supplied, cfg["api_key"])
            if (
                not supplied
                and _TRIGGER_PATH.fullmatch(path)
                and clients.listed(host, cfg["keyless_trigger_clients"])
            ):
                log_event(f"Trigger from {host} with no key, allowed for the v1.x changeover.")
                return await call_next(request)
            if path in _SESSION_ONLY_PATHS or not supplied:
                return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
            if not (full or key_matches(supplied, cfg["trigger_key"])):
                if not _wrong_key_log.blocked(host):  # quiet for a minute after 5
                    if _wrong_key_log.failed(host):
                        log_event(
                            f"Repeated wrong API keys from {host}: not logging more for a minute."
                        )
                    else:
                        return _refuse(request, "Invalid or missing API key", 401)
                return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
            if not clients.allowed(host, cfg["api_clients"]):
                return _refuse(request, "This address isn't allowed to use the API keys")
            if not full and not _TRIGGER_PATH.fullmatch(path):
                return _refuse(request, "The trigger key can only fire triggers")
            return await call_next(request)
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
    cfg = load_config()
    return {"api_key": cfg["api_key"], "trigger_key": cfg["trigger_key"]}


@router.post("/api/key/rotate")
def api_key_rotate(key: Literal["api", "trigger"] = "api"):
    """?key=trigger rotates the trigger key; otherwise the full API key."""
    name = f"{key}_key"
    cfg = load_config()
    cfg[name] = new_api_key()
    save_config(cfg)
    log_event(f"{name} rotated.")
    return {name: cfg[name]}


@router.post("/api/password")
def api_password(req: _PasswordChange, request: Request):
    cfg = load_config()
    # The current password is skipped only on first run and the forced first change.
    stored = cfg["web_password_hash"]
    if stored and not cfg["password_change_required"]:
        _check_password(request, req.current_password, stored, "Current password is incorrect", 400)
    or_400(set_password, cfg, req.password)
    save_config(cfg)
    # Sign out every other session: the old password may have leaked.
    token = request.cookies.get("session", "")
    for other in [
        t for t in _sessions if t != token
    ]:  # in place: this browser's never goes missing
        del _sessions[other]
    log_event("Web UI password changed.")
    return {"ok": True}


def _check_password(request: Request, password: str, stored: str, wrong: str, status: int) -> None:
    """Raise unless ``password`` matches; repeated wrong ones from one address are throttled."""
    host = _host(request)
    wait = _sign_in_failures.blocked(host)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many wrong passwords: try again in {wait} s",
            headers={"Retry-After": str(wait)},
        )
    if not verify_password(password, stored):
        if _sign_in_failures.failed(host):
            log_event(f"Too many wrong passwords from {host}: refusing it for a minute.")
        raise HTTPException(status_code=status, detail=wrong)
    _sign_in_failures.succeeded(host)


@router.post("/login")
def api_login(req: _PasswordRequest, request: Request, response: Response):
    stored = load_config()["web_password_hash"]
    if not stored:
        raise HTTPException(status_code=400, detail="No password is set yet")
    _check_password(request, req.password, stored, "Incorrect password", 401)
    for old in [t for t, created in _sessions.items() if time.monotonic() - created > SESSION_MAX]:
        del _sessions[old]  # browsers that never came back
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.monotonic()
    response.set_cookie("session", token, httponly=True, samesite="strict", max_age=SESSION_MAX)
    return {"ok": True}


@router.post("/logout")
def api_logout(request: Request, response: Response):
    _sessions.pop(request.cookies.get("session", ""), None)
    response.delete_cookie("session")
    return {"ok": True}

"""Talking to other Caspar Controllers (Remote Controllers), one explicit call per action.

Every path is fixed here, never taken from a request, so the remote's key can only reach
that remote's own API.
"""

from typing import Literal

import requests

_remote_name_cache: dict = {}  # url -> last known server_name for offline display


class RemoteKeyRejected(Exception):
    """The remote answered 401: its saved API key is wrong."""


# Everything a remote call can raise: key rejected, unreachable or an error status, or a
# reply that isn't a controller's JSON object.
ERRORS = (RemoteKeyRejected, requests.RequestException, ValueError)


def _call(remote: dict, method: str, path: str, timeout: float = 5, **kwargs) -> dict:
    """Request another controller's API with that controller's key; its JSON reply."""
    url = remote.get("url", "").rstrip("/")
    # No redirects: requests would forward the key header to wherever it's sent.
    r = requests.request(
        method,
        f"{url}{path}",
        headers={"X-API-Key": remote.get("api_key", "")},
        timeout=timeout,
        allow_redirects=False,
        **kwargs,
    )
    if r.status_code == 401:
        raise RemoteKeyRejected
    r.raise_for_status()  # a 404 or 500 from the remote is a failure, not "OK"
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("Not a Caspar Controller reply")
    return data


def health(remote: dict) -> dict:
    return _call(remote, "GET", "/api/health", timeout=3)


def media(remote: dict) -> dict:
    return _call(remote, "GET", "/api/media")


def server_action(remote: dict, action: Literal["start", "stop"]) -> dict:
    return _call(remote, "POST", f"/api/server/{action}")


def instance_action(remote: dict, inst_id: int, action: Literal["restart", "stop"]) -> dict:
    return _call(remote, "POST", f"/api/instance/{inst_id}/{action}")


def amcp(remote: dict, inst_id: int, command: str) -> dict:
    return _call(remote, "POST", f"/api/instance/{inst_id}/amcp", json={"command": command})


def remote_status(idx: int, remote: dict) -> dict:
    """One remote's dashboard entry: its status, or offline."""
    url = remote.get("url", "").rstrip("/")
    offline = {
        "idx": idx,
        "url": url,
        "display_name": _remote_name_cache.get(url) or url,
        "online": False,
        "status": None,
    }
    try:
        data = _call(remote, "GET", "/api/status", timeout=6)
    except RemoteKeyRejected:
        return {**offline, "online": True, "auth_error": True}
    except ERRORS:
        return offline
    server_name = data.get("server_name", "").strip()
    if server_name:
        _remote_name_cache[url] = server_name
    return {
        **offline,
        "display_name": server_name or _remote_name_cache.get(url) or url,
        "online": True,
        "status": data,
    }

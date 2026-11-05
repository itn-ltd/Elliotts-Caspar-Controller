"""Talking to other Caspar Controllers (Remote Controllers)."""

import requests

_remote_name_cache: dict = {}  # url -> last known server_name for offline display


def call_remote(
    remote: dict, method: str, path: str, timeout: float, **kwargs
) -> requests.Response:
    """Request another controller's API with that controller's API key."""
    # Only "/..." paths: "@host" or "//host" would send the key to a different host.
    if not path.startswith("/") or path.startswith("//") or any(c in path for c in "@\\"):
        raise ValueError("Invalid remote path")
    url = remote.get("url", "").rstrip("/")
    headers = {"X-API-Key": remote.get("api_key", "")}
    # No redirects: requests would forward the key header to wherever it's sent.
    return requests.request(
        method, f"{url}{path}", headers=headers, timeout=timeout, allow_redirects=False, **kwargs
    )


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
        # 6s timeout: remote /api/status pings up to 6 AMCP ports in parallel
        # (each join has a 3s timeout) so 5s gives a comfortable margin
        r = call_remote(remote, "GET", "/api/status", timeout=6)
        if r.status_code == 401:
            return {**offline, "online": True, "auth_error": True}
        data = r.json()
        server_name = data.get("server_name", "").strip()
        if not server_name and url not in _remote_name_cache:
            # Only fetch config the first time — afterwards use the cache
            try:
                cfg_r = call_remote(remote, "GET", "/api/config", timeout=4)
                server_name = cfg_r.json().get("server_name", "").strip()
            except Exception:
                pass
        if server_name:
            _remote_name_cache[url] = server_name
        display_name = server_name or _remote_name_cache.get(url) or url
        return {**offline, "display_name": display_name, "online": True, "status": data}
    except Exception:
        return offline

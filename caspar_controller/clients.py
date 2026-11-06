"""Which addresses may use the API keys (the api_clients setting).

Requests only ever read what's cached here, so a slow or failing DNS lookup can never stall
the server. Host names are looked up when the list is saved, at start-up, and in the
background every 5 minutes; a name that fails to resolve keeps its last good addresses.
"""

import ipaddress
import socket
import threading
import time

from caspar_controller.log import log_event

RESOLVE_EVERY = 300  # seconds between background lookups

_cache: dict = {"entries": None, "at": 0.0, "names": {}}  # names: host -> [networks]
_refreshing = threading.Lock()


def _literal(entry: str):
    """An IP address or subnet as a network, or None for a host name."""
    try:
        return ipaddress.ip_network(entry)
    except ValueError:
        return None


def resolve(entries: list) -> None:
    """Look up the host names in ``entries`` now. Never call this from a request."""
    names = dict(_cache["names"])
    for entry in entries:
        if _literal(entry):
            continue
        try:
            infos = socket.getaddrinfo(entry, None)
            names[entry] = [ipaddress.ip_network(i[4][0].split("%")[0]) for i in infos]
        except OSError as e:
            kept = " (keeping its last known address)" if entry in names else ""
            log_event(f"Allowed addresses: couldn't look up {entry}{kept}: {e}")
    _cache.update(entries=list(entries), at=time.monotonic(), names=names)


def refresh_in_background(entries: list) -> None:
    if not _refreshing.acquire(blocking=False):
        return  # one lookup at a time

    def run():
        try:
            resolve(entries)
        finally:
            _refreshing.release()

    threading.Thread(target=run, daemon=True, name="api-clients").start()


def allowed(host: str, entries: list) -> bool:
    """True if ``host`` (the caller's address) may use a key. An empty list allows any."""
    if not entries:
        return True
    if _cache["entries"] != entries or time.monotonic() - _cache["at"] > RESOLVE_EVERY:
        refresh_in_background(entries)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:  # ::ffff:10.0.0.5 is 10.0.0.5
        ip = ip.ipv4_mapped
    for entry in entries:
        networks = [_literal(entry)] if _literal(entry) else _cache["names"].get(entry, [])
        if any(ip in network for network in networks):
            return True
    return False

"""The CasparCG instances this PC runs: start, stop, restart and live status."""

import threading
import time

from caspar_controller.caspar.amcp import AMCPClient
from caspar_controller.caspar.config_xml import (
    ensure_test_pattern_images,
    regenerate_all_instance_configs,
    regenerate_instance_config,
)
from caspar_controller.caspar.info import EMPTY_LAYER, parse_layer_info
from caspar_controller.caspar.process import CasparProcessManager
from caspar_controller.log import log_event
from caspar_controller.settings import instance_amcp_port, load as load_config

_managers: dict = {}  # inst_id -> CasparProcessManager

# Background status cache — refreshed every 2s so /api/status returns instantly
_status_cache: dict = {"live": {}, "ts": 0.0}
_status_lock = threading.Lock()
_status_refresh_stop = threading.Event()


def _make_manager(inst: dict, cfg: dict) -> CasparProcessManager:
    port = instance_amcp_port(cfg, inst)
    return CasparProcessManager(
        exe_path=cfg["caspar_exe_path"],
        amcp_port=port,
        config_filename=f"casparcg_inst_{inst['id']}.config",
    )


def _load_instance(inst: dict, client: AMCPClient) -> str:
    """Send the startup command for an instance (always channel 1 in each CasparCG process)."""
    if inst.get("type", "html") == "html":
        url = inst.get("url", "").strip()
        return client.play_html(1, url) if url else client.send("CLEAR 1")
    cmd = inst.get("startup_command", "").strip()
    return client.send(cmd) if cmd else client.send("CLEAR 1")


def _background_status_worker():
    """Refresh AMCP state in background so /api/status returns from cache."""
    while not _status_refresh_stop.is_set():
        try:
            cfg = load_config()
            instances = cfg.get("instances", [])
            live: dict = {}
            layer_info: dict = {}

            # Known race (#5): threads still running after join() write into the
            # *next* poll's dicts, because these names are rebound each loop.
            def _fetch(inst):
                client = AMCPClient(port=instance_amcp_port(cfg, inst), timeout=1.0)  # noqa: B023
                raw = client.info_layer()
                is_live = raw.startswith("201")
                live[inst["id"]] = is_live  # noqa: B023
                layer_info[inst["id"]] = parse_layer_info(raw) if is_live else dict(EMPTY_LAYER)  # noqa: B023

            threads = [
                threading.Thread(target=_fetch, args=(inst,), daemon=True) for inst in instances
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=1.5)

            with _status_lock:
                _status_cache["live"] = live
                _status_cache["layer_info"] = layer_info
                _status_cache["ts"] = time.time()
        except Exception:
            pass
        _status_refresh_stop.wait(2)


def start_status_worker() -> None:
    _status_refresh_stop.clear()
    threading.Thread(target=_background_status_worker, daemon=True, name="status-refresh").start()


def stop_status_worker() -> None:
    _status_refresh_stop.set()


def cached_status() -> tuple[dict, dict]:
    """(live, layer_info) by instance id, from the background cache."""
    with _status_lock:
        return dict(_status_cache["live"]), dict(_status_cache.get("layer_info", {}))


def _start_sequence(cfg: dict, insts: list) -> tuple[list, list]:
    """Start insts one at a time. Returns (started ids, failed ids)."""
    started, errors = [], []
    # One at a time, 5s apart: receivers lock onto the wrong NDI source if outputs start together.
    for i, inst in enumerate(insts):
        m = _make_manager(inst, cfg)
        _managers[inst["id"]] = m
        if m.start():
            res = _load_instance(inst, AMCPClient(port=instance_amcp_port(cfg, inst)))
            log_event(f"Inst {inst['id']} ({inst['name']}) started → {res[:60]}")
            started.append(inst["id"])
            if i < len(insts) - 1:
                time.sleep(5)
        else:
            log_event(f"Inst {inst['id']} ({inst['name']}) FAILED to start")
            errors.append(inst["id"])
    return started, errors


def start_all(cfg: dict) -> tuple[list, list]:
    """Kill every CasparCG, then start every instance in order. Returns (started, failed)."""
    regenerate_all_instance_configs(cfg)
    ensure_test_pattern_images(cfg)
    # Kill by name, not by AMCP reply: stale ports would otherwise pile up processes.
    CasparProcessManager._kill_all_caspar_instances()
    return _start_sequence(cfg, cfg.get("instances", []))


def start_missing(cfg: dict) -> tuple[list, list]:
    """Start only the instances this app isn't already running, killing nothing."""
    missing = [i for i in cfg.get("instances", []) if i["id"] not in _managers]
    ensure_test_pattern_images(cfg)
    for inst in missing:
        regenerate_instance_config(cfg, inst)
    return _start_sequence(cfg, missing)


def stop_all() -> None:
    global _managers
    threads = [threading.Thread(target=m.stop, daemon=True) for m in list(_managers.values())]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)
    _managers = {}
    # Also catches CasparCG this app doesn't manage (stale ports, v1.x installs).
    CasparProcessManager._kill_all_caspar_instances()
    log_event("All CasparCG instances stopped.")


def restart_all(cfg: dict) -> None:
    """Stop everything, then start each instance in order. Runs in a thread."""
    stop_all()
    start_all(cfg)


def _stop_one(inst_id: int, port: int) -> None:
    # Unmanaged (e.g. adoption failed): kill whatever listens on its port.
    if inst_id in _managers:
        _managers.pop(inst_id).stop()
    else:
        CasparProcessManager._kill_caspar_on_port(port)


def restart(inst_id: int) -> None:
    """Stop the CasparCG process for one instance and relaunch it. Runs in a thread."""
    cfg = load_config()
    inst = next((i for i in cfg.get("instances", []) if i["id"] == inst_id), None)
    if not inst:
        return
    port = instance_amcp_port(cfg, inst)
    _stop_one(inst_id, port)
    regenerate_instance_config(cfg, inst)
    m = _make_manager(inst, cfg)
    _managers[inst_id] = m
    if m.start():
        res = _load_instance(inst, AMCPClient(port=port))
        log_event(f"Inst {inst_id} ({inst['name']}) restarted → {res[:60]}")
    else:
        log_event(f"Inst {inst_id} ({inst['name']}) failed to restart")
        _managers.pop(inst_id, None)


def adopt_running() -> int:
    """Take over instances already answering AMCP (e.g. after the app restarted). Returns count."""
    cfg = load_config()
    adopted = 0
    for inst in cfg.get("instances", []):
        if inst["id"] in _managers:
            continue
        m = _make_manager(inst, cfg)
        if m.adopt_existing():
            _managers[inst["id"]] = m
            adopted += 1
    return adopted


def stop(inst: dict, port: int) -> None:
    """Stop one instance. Runs in a thread."""
    _stop_one(inst["id"], port)
    log_event(f"Inst {inst['id']} ({inst['name']}) stopped.")

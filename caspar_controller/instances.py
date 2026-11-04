"""The CasparCG instances this PC runs: start, stop, restart and live status."""

import threading
import time

from caspar_controller.caspar.amcp import AMCPClient
from caspar_controller.caspar.config_xml import (
    ensure_test_pattern_images,
    regenerate_all_instance_configs,
    regenerate_instance_config,
)
from caspar_controller.caspar.info import parse_layer_info
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
        startup_delay=cfg["startup_delay"],
        window_title=f"PCR3 CasparCG — {inst['name']}",
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
                layer_info[inst["id"]] = (  # noqa: B023
                    parse_layer_info(raw)
                    if is_live
                    else {
                        "clip": None,
                        "time": 0.0,
                        "duration": 0.0,
                        "loop": False,
                        "producer": "empty",
                    }
                )

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


def start_all(cfg: dict) -> tuple[list, list]:
    """Start every instance in order. Returns (started ids, failed ids)."""
    instances = cfg.get("instances", [])

    # Write all config files and test pattern images before launching
    regenerate_all_instance_configs(cfg)
    ensure_test_pattern_images(cfg)

    # Always kill every running CasparCG before launching fresh.
    # Conditional kill (only when AMCP responds) misses instances on stale ports
    # and causes processes to accumulate across multiple Start clicks.
    CasparProcessManager._kill_all_caspar_instances()

    started = []
    errors = []

    # Sequential startup with 5s gap — ensures NDI sources appear one at a time
    # in config order so Tricaster/receivers lock onto the correct source.
    for i, inst in enumerate(instances):
        m = _make_manager(inst, cfg)
        _managers[inst["id"]] = m
        ok = m.start()
        if ok:
            res = _load_instance(inst, AMCPClient(port=instance_amcp_port(cfg, inst)))
            log_event(f"Inst {inst['id']} ({inst['name']}) started → {res[:60]}")
            started.append(inst["id"])
            if i < len(instances) - 1:
                time.sleep(5)  # let NDI source settle before announcing the next one
        else:
            log_event(f"Inst {inst['id']} ({inst['name']}) FAILED to start")
            errors.append(inst["id"])
    return started, errors


def stop_all() -> None:
    global _managers
    # Stop any managed instances
    threads = [threading.Thread(target=m.stop, daemon=True) for m in list(_managers.values())]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)
    _managers = {}
    # Kill any remaining CasparCG processes (e.g. started via the desktop GUI)
    CasparProcessManager._kill_all_caspar_instances()
    log_event("All CasparCG instances stopped.")


def restart(inst_id: int) -> None:
    """Stop the CasparCG process for one instance and relaunch it. Runs in a thread."""
    cfg = load_config()
    inst_map = {i["id"]: i for i in cfg.get("instances", [])}
    inst = inst_map.get(inst_id)
    if not inst:
        return
    port = instance_amcp_port(cfg, inst)
    # Kill whatever is on this port — works whether started from GUI or web
    if inst_id in _managers:
        _managers[inst_id].stop()
        _managers.pop(inst_id, None)
    else:
        CasparProcessManager._kill_caspar_on_port(port)
    regenerate_instance_config(cfg, inst)
    m = _make_manager(inst, cfg)
    _managers[inst_id] = m
    ok = m.start()
    if ok:
        res = _load_instance(inst, AMCPClient(port=port))
        log_event(f"Inst {inst_id} ({inst['name']}) restarted → {res[:60]}")
    else:
        log_event(f"Inst {inst_id} ({inst['name']}) failed to restart")
        _managers.pop(inst_id, None)


def restart_all(cfg: dict) -> None:
    """Stop everything, then start each instance in order. Runs in a thread."""
    global _managers
    instances = cfg.get("instances", [])
    # Stop all managed instances in parallel
    threads = [threading.Thread(target=m.stop, daemon=True) for m in list(_managers.values())]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)
    # Kill any remaining (e.g. started from GUI)
    CasparProcessManager._kill_all_caspar_instances()
    _managers = {}
    regenerate_all_instance_configs(cfg)
    # Start sequentially with 5s gap for NDI ordering
    for i, inst in enumerate(instances):
        m = _make_manager(inst, cfg)
        _managers[inst["id"]] = m
        ok = m.start()
        if ok:
            res = _load_instance(inst, AMCPClient(port=instance_amcp_port(cfg, inst)))
            log_event(f"Inst {inst['id']} ({inst['name']}) restarted → {res[:60]}")
            if i < len(instances) - 1:
                time.sleep(5)
        else:
            log_event(f"Inst {inst['id']} ({inst['name']}) failed to restart")
            _managers.pop(inst["id"], None)


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
    inst_id = inst["id"]
    if inst_id in _managers:
        _managers[inst_id].stop()
        _managers.pop(inst_id, None)
    else:
        CasparProcessManager._kill_caspar_on_port(port)
    log_event(f"Inst {inst_id} ({inst['name']}) stopped.")

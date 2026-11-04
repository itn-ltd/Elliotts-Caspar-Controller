"""Tray icon: runs the web server; the web UI does everything else."""

import os
import threading
import time
import webbrowser

import pystray
import uvicorn
from PIL import Image

from caspar_controller import instances
from caspar_controller.api.app import STATIC_DIR, app
from caspar_controller.log import log_event
from caspar_controller.settings import load as load_config

TITLE = "Elliott's Caspar Controller"


def _status_text(_item=None) -> str:
    live, _ = instances.cached_status()
    return f"{sum(live.values())}/{len(load_config()['instances'])} instances live"


def _restart_all() -> None:
    log_event("Restarting all instances (tray)...")
    threading.Thread(target=instances.restart_all, args=(load_config(),), daemon=True).start()


def start_caspar() -> None:
    """Reconnect to CasparCG left running, then auto-start the rest if that's switched on."""
    adopted = instances.adopt_running()
    if adopted:
        log_event(f"Reconnected to {adopted} running CasparCG instance(s).")
    cfg = load_config()
    if adopted or not cfg["autostart_caspar"] or not cfg["instances"]:
        return
    if not os.path.isfile(cfg["caspar_exe_path"]):
        log_event("Auto-start skipped: set the CasparCG path in Settings.")
        return
    started, _ = instances.start_all(cfg)
    log_event(f"Auto-started {len(started)}/{len(cfg['instances'])} instances")


def run() -> None:
    port = load_config()["web_port"]
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=port, access_log=False, log_config=None)
    )
    # uvicorn exits its thread with SystemExit when it can't bind the port.
    server_thread = threading.Thread(target=server.run, daemon=True)

    def quit_app(icon: pystray.Icon) -> None:
        server.should_exit = True
        icon.stop()

    def on_ready(icon: pystray.Icon) -> None:
        icon.visible = True
        server_thread.start()
        while not server.started and server_thread.is_alive():
            time.sleep(0.1)
        if not server.started:
            icon.notify(f"The web server couldn't start on port {port}. Is it in use?", TITLE)
            return
        webbrowser.open(url)
        start_caspar()

    menu = pystray.Menu(
        pystray.MenuItem("Open Caspar Controller", lambda: webbrowser.open(url), default=True),
        pystray.MenuItem(_status_text, None, enabled=False),
        pystray.MenuItem("Restart all instances", _restart_all),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    )
    icon_image = Image.open(os.path.join(STATIC_DIR, "esc_icon.png"))
    pystray.Icon("CasparController", icon_image, TITLE, menu).run(setup=on_ready)
    if server_thread.ident:
        server_thread.join(timeout=5)
    # os._exit skips PyInstaller's temp-folder cleanup, which fails if AV still holds a file.
    os._exit(0)

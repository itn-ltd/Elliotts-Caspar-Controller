"""Tray icon: runs the web server; the web UI does everything else."""

import logging
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
    live = sum(p.live for p in instances.manager.cached_status().values())
    return f"{live}/{len(load_config()['instances'])} instances live"


def _restart_all() -> None:
    log_event("Restarting all instances (tray)...")
    instances.manager.restart_all()


def start_caspar() -> None:
    """Reconnect to CasparCG left running, then auto-start the rest if that's switched on."""
    instances.manager.adopt_running()
    cfg = load_config()
    if not cfg["autostart_caspar"]:
        return
    if not os.path.isfile(cfg["caspar_exe_path"]):
        log_event("Auto-start skipped: set the CasparCG path in Settings.")
        return
    instances.manager.start_all()  # skips instances already running, so live outputs stay up


class _LastError(logging.Handler):
    """Keeps uvicorn's last error so the tray can say why the server didn't start."""

    message = ""

    def emit(self, record: logging.LogRecord) -> None:
        self.message = record.getMessage()


def _refresh_menu(icon: pystray.Icon) -> None:
    # pystray builds the Windows menu once, so rebuild it to update the live count.
    while icon.visible:
        time.sleep(3)
        icon.update_menu()


def run() -> None:
    port = load_config()["web_port"]
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            access_log=False,
            log_config=None,
            timeout_graceful_shutdown=1,  # open dashboard streams don't hold up Quit
            proxy_headers=False,  # no proxy in front: X-Forwarded-For would fake an allowed address
        )
    )
    # uvicorn ends its thread (SystemExit) if it can't bind or start up; this records why.
    server_thread = threading.Thread(target=server.run, daemon=True)
    last_error = _LastError(logging.ERROR)
    logging.getLogger("uvicorn.error").addHandler(last_error)

    def quit_app(icon: pystray.Icon) -> None:
        server.should_exit = True
        icon.stop()

    def on_ready(icon: pystray.Icon) -> None:
        icon.visible = True
        server_thread.start()
        while not server.started and server_thread.is_alive():
            time.sleep(0.1)
        if not server.started:
            icon.notify(
                f"The web server didn't start on port {port}. {last_error.message}"[:250], TITLE
            )
            return
        webbrowser.open(url)
        threading.Thread(target=_refresh_menu, args=(icon,), daemon=True).start()
        instances.manager.notify = lambda message: icon.notify(message[:250], TITLE)
        try:
            start_caspar()
        except Exception as e:  # a thread with nobody to report to
            log_event(f"Auto-start failed: {e}")
            icon.notify(f"CasparCG didn't start: {e}"[:250], TITLE)

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

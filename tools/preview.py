"""Run Caspar Controller against fake CasparCG servers, for working on the app without one.

    python tools/preview.py            # http://127.0.0.1:5390, opens your browser

Uses a throwaway config in .home/preview (git-ignored), never ProgramData or a real CasparCG:
two live HTML outputs, a media output playing a clip, one stopped output, and sample
console lines. Start/Stop can't launch anything, as there's no casparcg.exe behind it.
"""

import json
import socket
import sys
import threading
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
from fake_amcp import FakeAMCPServer  # noqa: E402

PORT = 5390
PLAYING = (
    "<layer><foreground><file><clip>0 95</clip><name>PROMO_2024</name>"
    "<path>media/PROMO_2024.mp4</path><time>37.5 95</time></file>"
    "<loop>false</loop><paused>false</paused><producer>ffmpeg</producer></foreground></layer>"
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    root = REPO / ".home" / "preview"
    home, caspar = root / "home", root / "caspar"
    for d in (home, caspar / "media", caspar / "log" / "inst_1", caspar / "log" / "inst_3"):
        d.mkdir(parents=True, exist_ok=True)
    for clip in ("PROMO_2024.mp4", "AMB_LOOP.mov", "STING.mov"):
        (caspar / "media" / clip).write_bytes(b"")
    (caspar / "log" / "inst_1" / "caspar_preview.log").write_text(
        "[info] Initialized video modes.\n[info] [html] Loaded the lower third\n", encoding="utf-8"
    )
    (caspar / "log" / "inst_3" / "caspar_preview.log").write_text(
        "[info] [ffmpeg] PROMO_2024.mp4 opened\n", encoding="utf-8"
    )

    servers = [FakeAMCPServer(), FakeAMCPServer(), FakeAMCPServer(info_xml=PLAYING)]
    instances = [
        {
            "id": 1,
            "name": "GFX1",
            "ndi_name": "PREVIEW GFX1",
            "type": "html",
            "url": "",
            "amcp_port": servers[0].port,
        },
        {
            "id": 2,
            "name": "GFX2",
            "ndi_name": "PREVIEW GFX2",
            "type": "html",
            "url": "",
            "amcp_port": servers[1].port,
        },
        {
            "id": 3,
            "name": "MEDIA1",
            "ndi_name": "PREVIEW MEDIA1",
            "type": "media",
            "amcp_port": servers[2].port,
        },
        {
            "id": 4,
            "name": "MEDIA2",
            "ndi_name": "PREVIEW MEDIA2",
            "type": "media",
            "amcp_port": free_port(),
        },
    ]
    # Fresh outputs each run (the fake servers get new ports); keep a password or keys set before.
    config = home / "config.json"
    kept = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    kept.update(
        server_name=kept.get("server_name", "Preview"),
        web_port=PORT,
        caspar_exe_path=str(caspar / "casparcg.exe"),
        instances=instances,
    )
    config.write_text(json.dumps(kept, indent=2), encoding="utf-8")

    import os

    os.environ["CASPAR_CONTROLLER_HOME"] = str(home)
    os.environ.pop("CASPAR_CONTROLLER_INITIAL_PASSWORD", None)
    import uvicorn

    from caspar_controller.api.app import app

    threading.Timer(1.5, webbrowser.open, args=(f"http://127.0.0.1:{PORT}",)).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()

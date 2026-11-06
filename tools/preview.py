"""Run Caspar Controller against fake CasparCG servers, for working on the app without one.

    python tools/preview.py            # http://127.0.0.1:5390, opens your browser
    python tools/preview.py --v1       # also shows the "Imported from v1.x" notice

Uses a throwaway config in .home/preview (git-ignored), never ProgramData or a real CasparCG:
two live HTML outputs, a media output playing a clip, one stopped output, and sample
console lines. The fake servers run inside this process, so Start, Stop and Restart are
disarmed here: they can't reconnect to, launch or kill any process.
"""

import json
import os
import sys
import threading
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
from conftest import free_port  # noqa: E402
from fake_amcp import FakeAMCPServer  # noqa: E402

from caspar_controller.caspar.process import CasparProcessManager  # noqa: E402

PORT = 5390
PLAYING = (
    "<layer><foreground><file><clip>0 95</clip><name>PROMO_2024</name>"
    "<path>media/PROMO_2024.mp4</path><time>37.5 95</time></file>"
    "<loop>false</loop><paused>false</paused><producer>ffmpeg</producer></foreground></layer>"
)


def disarm() -> None:
    """Stop would otherwise find this process on a fake's port, 'adopt' it and kill it."""
    CasparProcessManager.adopt_existing = lambda self: False
    CasparProcessManager.start = lambda self: False
    CasparProcessManager._kill_tree = staticmethod(lambda pid: None)


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

    fakes = [FakeAMCPServer(), FakeAMCPServer(), FakeAMCPServer(info_xml=PLAYING), None]
    outputs = [("GFX1", "html"), ("GFX2", "html"), ("MEDIA1", "media"), ("MEDIA2", "media")]
    instances = [
        {
            "id": n,
            "name": name,
            "ndi_name": f"PREVIEW {name}",
            "type": kind,
            "url": "",
            "amcp_port": fake.port if fake else free_port(),  # MEDIA2: nothing listening
        }
        for n, ((name, kind), fake) in enumerate(zip(outputs, fakes, strict=True), start=1)
    ]

    # Fresh outputs each run (the fakes get new ports); keep a password or keys set before, but
    # nothing that reaches other machines.
    config = home / "config.json"
    kept = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    for key in ("remote_controllers", "api_clients"):
        kept.pop(key, None)
    kept.update(
        server_name=kept.get("server_name", "Preview"),
        web_port=PORT,
        amcp_base_port=free_port(),  # outputs added here stay off 5250 and any real CasparCG
        caspar_exe_path=str(caspar / "casparcg.exe"),
        instances=instances,
    )
    if "--v1" in sys.argv:
        kept["v1_notice"] = {
            "source": str(root / "elliotts_caspar_config.json"),
            "outputs": 4,
            "password": True,
        }
    config.write_text(json.dumps(kept, indent=2), encoding="utf-8")

    os.environ["CASPAR_CONTROLLER_HOME"] = str(home)
    os.environ.pop("CASPAR_CONTROLLER_INITIAL_PASSWORD", None)
    disarm()
    import uvicorn

    from caspar_controller.api.app import app

    threading.Timer(1.5, webbrowser.open, args=(f"http://127.0.0.1:{PORT}",)).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()

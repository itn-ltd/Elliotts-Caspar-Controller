"""Stand-in for casparcg.exe, launched by the real process manager in tests.

Usage (from the instance's working directory, as the app launches it):
    python fake_caspar.py casparcg_inst_N.config

Reads the AMCP port from the generated config, serves fake AMCP on it, prints
CasparCG-like log lines, logs received commands to amcp_<port>.log, and exits on BYE.
"""

import os
import sys
import threading
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_amcp import FakeAMCPServer  # noqa: E402


def echo_console():
    """CasparCG reads commands typed into its console; this one just echoes them."""
    for line in sys.stdin:
        print(f"[fake casparcg] console: {line.strip()}", flush=True)


def main():
    config_path = sys.argv[1]
    root = ET.parse(config_path).getroot()
    port = int(root.find("./controllers/tcp/port").text)
    print(f"[fake casparcg] config={config_path} port={port}", flush=True)
    server = FakeAMCPServer(port=port, log_path=os.path.abspath(f"amcp_{port}.log"))
    # Coloured, as CasparCG's console is: the app strips the ANSI codes.
    print("\x1b[32m[fake casparcg] AMCP listening\x1b[0m", flush=True)
    threading.Thread(target=echo_console, daemon=True).start()
    server.bye.wait()
    server.close()


if __name__ == "__main__":
    main()

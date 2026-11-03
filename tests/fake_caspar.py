"""Stand-in for casparcg.exe, launched by the real process manager in tests.

Usage (from the instance's working directory, as the app launches it):
    python fake_caspar.py casparcg_inst_N.config

Reads the AMCP port from the generated config, serves fake AMCP on it, prints
CasparCG-like log lines, logs received commands to amcp_<port>.log, and exits on BYE.
"""
import os
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_amcp import FakeAMCPServer  # noqa: E402


def main():
    config_path = sys.argv[1]
    root = ET.parse(config_path).getroot()
    port = int(root.find("./controllers/tcp/port").text)
    print(f"[fake casparcg] config={config_path} port={port}", flush=True)
    server = FakeAMCPServer(port=port, log_path=os.path.abspath(f"amcp_{port}.log"))
    print("[fake casparcg] AMCP listening", flush=True)
    while not server.bye.wait(0.2):
        pass
    time.sleep(0.1)
    server.close()


if __name__ == "__main__":
    main()

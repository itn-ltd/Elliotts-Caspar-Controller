"""Stand-in for casparcg.exe, launched by the real process manager in tests.

Usage (from the instance's working directory, as the app launches it):
    python fake_caspar.py casparcg_inst_N.config

Reads the AMCP port and log path from the generated config, serves fake AMCP on it, writes
CasparCG-like lines to its log file, logs received commands to amcp_<port>.log, and exits
on BYE.
"""

import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_amcp import FakeAMCPServer  # noqa: E402


def main():
    config_path = sys.argv[1]
    root = ET.parse(config_path).getroot()
    port = int(root.find("./controllers/tcp/port").text)
    log_dir = root.find("./paths/log-path").text.replace("\\", os.sep)
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "caspar_fake.log"), "a", encoding="utf-8") as log:

        def say(line: str) -> None:
            log.write(line + "\n")
            log.flush()

        say(f"[fake casparcg] config={config_path} port={port}")
        server = FakeAMCPServer(port=port, log_path=os.path.abspath(f"amcp_{port}.log"))
        # Coloured, as some CasparCG builds write it: the app strips the ANSI codes.
        say("\x1b[32m[fake casparcg] AMCP listening\x1b[0m")
        server.bye.wait()
        say("[fake casparcg] BYE")
        server.close()


if __name__ == "__main__":
    main()

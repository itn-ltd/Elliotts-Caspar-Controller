"""Launch, monitor, and stop the CasparCG server process."""

import os
import subprocess
import time
from typing import Optional

import psutil

from caspar_controller.caspar.amcp import AMCPClient


class _AdoptedProcess:
    """Minimal Popen-compatible wrapper around an existing process found via psutil."""

    def __init__(self, pid: int):
        self._pid = pid
        try:
            self._psutil = psutil.Process(pid)
        except psutil.NoSuchProcess:
            self._psutil = None

    def poll(self):
        if self._psutil is None:
            return 0
        try:
            return None if self._psutil.is_running() else 0
        except psutil.NoSuchProcess:
            return 0

    @property
    def pid(self) -> int:
        return self._pid


class CasparProcessManager:
    def __init__(
        self,
        exe_path: str,
        amcp_port: int = 5250,
        config_filename: str = "casparcg.config",
    ):
        self.exe_path = exe_path
        self.amcp_port = amcp_port
        self.config_filename = config_filename
        self._process: Optional[subprocess.Popen] = None
        self._client = AMCPClient(port=amcp_port)

    def start(self) -> bool:
        """Launch CasparCG and wait for AMCP. Caller writes the config; raises OSError if the exe can't run."""
        if self.is_running():
            return True
        # No shell: names never reach a command line. Full path: works even where
        # NoDefaultCurrentDirectoryInExePath stops the current folder being searched.
        # No pipes either: CasparCG keeps running when this app quits (the console reads its
        # log files instead, see logtail.py).
        self._process = subprocess.Popen(
            [os.path.abspath(self.exe_path), self.config_filename],
            cwd=self._exe_dir(),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 90 s covers slow cold starts; galleries take under 60 s.
        deadline = time.time() + 90
        while time.time() < deadline:
            time.sleep(1)
            if self._process.poll() is not None:
                return False
            if self._client.ping():
                return True
        return False

    def adopt_existing(self) -> bool:
        """Adopt the CasparCG already listening on this AMCP port. Returns True if adopted."""
        # Listener first: on Windows a connect to a closed port takes about 2 s.
        proc = _listening_on(self.amcp_port)
        if not proc or not self._client.ping():
            return False
        self._process = _AdoptedProcess(_launch_root(proc).pid)
        return True

    def stop(self) -> None:
        """Stop CasparCG: send BYE then immediately kill the process tree."""
        self._client.send("BYE")
        time.sleep(0.2)  # a moment to exit cleanly before the kill
        if self._process and self._process.poll() is None:
            self._kill_tree(self._process.pid)
        self._process = None

    def exit_code(self) -> Optional[int]:
        """The exit code, or None if unknown (still running, or reconnected to, not launched)."""
        if not self._process or isinstance(self._process, _AdoptedProcess):
            return None
        return self._process.poll()

    def alive(self) -> bool:
        """True while OUR process hasn't exited, whether or not AMCP answers."""
        return bool(self._process) and self._process.poll() is None

    def is_running(self) -> bool:
        """True if OUR process is alive and AMCP answers (never another CasparCG's)."""
        return self.alive() and self._client.ping()

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None

    @staticmethod
    def _kill_tree(pid: int) -> None:
        """Kill a process and all its children (cmd.exe + casparcg.exe)."""
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            parent.terminate()
            psutil.wait_procs([parent] + children, timeout=5)
            # Force-kill anything still alive
            for proc in [parent] + children:
                try:
                    proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except psutil.NoSuchProcess:
            pass

    def _exe_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.exe_path))


def _listening_on(port: int) -> Optional[psutil.Process]:
    """The process listening on a local TCP port, if any."""
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr.port == port and conn.status == psutil.CONN_LISTEN and conn.pid:
            try:
                return psutil.Process(conn.pid)
            except psutil.NoSuchProcess:
                return None
    return None


def _launch_root(proc: psutil.Process) -> psutil.Process:
    """proc, or its cmd.exe parent if v1.x launched it via cmd /k (#12)."""
    try:
        parent = proc.parent()
        return parent if parent and "cmd" in parent.name().lower() else proc
    except psutil.Error:
        return proc

"""The CasparCG instances this PC runs: the one place that starts, stops and polls them."""

import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, NamedTuple, Optional

from caspar_controller.caspar.amcp import AMCPClient
from caspar_controller.caspar.config_xml import (
    ensure_test_pattern_images,
    regenerate_instance_config,
    reset_html_cache,
)
from caspar_controller.caspar.info import EMPTY_LAYER, parse_layer_info
from caspar_controller.caspar.process import CasparProcessManager
from caspar_controller.log import log_event
from caspar_controller.settings import instance_amcp_port, load as load_config

log = logging.getLogger(__name__)


class Probe(NamedTuple):
    live: bool
    layer: dict
    version: str  # CasparCG's VERSION reply, e.g. "2.3.3 Stable"


OFF = Probe(False, dict(EMPTY_LAYER), "")


def _label(inst: dict) -> str:
    return f"Inst {inst['id']} ({inst['name']})"


def _load_instance(inst: dict, client: AMCPClient) -> str:
    """Send the startup command for an instance (always channel 1 in each CasparCG process)."""
    if inst.get("type", "html") == "html":
        url = inst.get("url", "").strip()
        return client.play_html(1, url) if url else client.send("CLEAR 1")
    cmd = inst.get("startup_command", "").strip()
    return client.send(cmd) if cmd else client.send("CLEAR 1")


class InstanceManager:
    """Owns every CasparCG process the app runs.

    Start, stop and restart are jobs run one at a time on one worker thread, so two
    Start Alls never overlap. Only processes this app launched or adopted are touched.
    """

    def __init__(self):
        self._procs: dict[int, CasparProcessManager] = {}  # inst id -> process
        self._clients: dict[int, AMCPClient] = {}  # port -> open AMCP connection
        self._jobs: queue.Queue = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._cancel = threading.Event()  # Stop All interrupts a running start sequence
        self._status: dict[int, Probe] = {}  # inst id -> last poll
        self._status_stop = threading.Event()
        self.notify: Optional[Callable[[str], None]] = None  # the tray's notification

    # ------------------------------------------------------------ jobs

    def _submit(self, label: str, fn: Callable, *args) -> None:
        self._jobs.put((label, fn, args))
        if not self._worker:
            self._worker = threading.Thread(target=self._run_jobs, daemon=True, name="instances")
            self._worker.start()

    def _run_jobs(self) -> None:
        while True:
            label, fn, args = self._jobs.get()
            try:
                fn(*args)
            except Exception as e:  # nobody is waiting on a job, so report it
                log.exception("%s failed", label)
                self._report(f"{label} failed: {e}")
            finally:
                self._jobs.task_done()

    def wait(self) -> None:
        """Block until every submitted job has finished."""
        self._jobs.join()

    def _report(self, message: str) -> None:
        """Tell the operator: the event log, plus a tray notification."""
        log_event(message)
        if self.notify:
            try:
                self.notify(message)
            except Exception:
                log.exception("Tray notification failed")

    def start_all(self) -> None:
        self._submit("Start All", self._start_all)

    def stop_all(self) -> None:
        self._cancel.set()
        self._submit("Stop All", self._stop_all)

    def restart_all(self) -> None:
        self._submit("Restart All", self._restart_all)

    def restart(self, inst_id: int) -> None:
        self._submit(f"Restart of instance {inst_id}", self._restart, inst_id)

    def stop(self, inst_id: int) -> None:
        self._submit(f"Stop of instance {inst_id}", self._stop, inst_id)

    def adopt_running(self) -> None:
        self._submit("Reconnect", self._adopt_running)

    # ------------------------------------------------------------ processes

    def port(self, cfg: dict, inst: dict) -> int:
        """The port a running instance actually uses, else the configured one."""
        m = self._procs.get(inst["id"])
        return m.amcp_port if m else instance_amcp_port(cfg, inst)

    def client(self, cfg: dict, inst: dict) -> AMCPClient:
        """The shared AMCP connection to an instance."""
        return self._client(self.port(cfg, inst))

    def _client(self, port: int) -> AMCPClient:
        return self._clients.setdefault(port, AMCPClient(port=port))

    def _new_process(self, cfg: dict, inst: dict) -> CasparProcessManager:
        return CasparProcessManager(
            exe_path=cfg["caspar_exe_path"],
            amcp_port=instance_amcp_port(cfg, inst),
            config_filename=f"casparcg_inst_{inst['id']}.config",
        )

    def _adopt(self, cfg: dict, inst: dict) -> Optional[CasparProcessManager]:
        """Take over a CasparCG already answering on this instance's port, if there is one."""
        m = self._new_process(cfg, inst)
        if not m.adopt_existing():
            return None
        self._procs[inst["id"]] = m
        return m

    def _is_up(self, cfg: dict, inst: dict) -> bool:
        """True if the instance is running, adopting it if something answers on its port."""
        m = self._procs.get(inst["id"])
        if m and m.alive():  # even if slow to answer: never restart an output that's on air
            return True
        self._procs.pop(inst["id"], None)  # exited
        return self._adopt(cfg, inst) is not None

    def _launch(self, cfg: dict, inst: dict, verb: str = "started") -> bool:
        regenerate_instance_config(cfg, inst)
        reset_html_cache(cfg, inst)
        m = self._procs[inst["id"]] = self._new_process(cfg, inst)
        try:
            ok = m.start()
            error = ""
        except OSError as e:
            ok, error = False, f": {e}"
        if ok:
            res = _load_instance(inst, self._client(m.amcp_port))
            log_event(f"{_label(inst)} {verb} → {res[:60]}")
            return True
        log_event(f"{_label(inst)} FAILED to start{error}")
        self._procs.pop(inst["id"])
        if m.pid:
            m.stop()  # don't leave a half-started process behind
        return False

    def _start_all(self) -> None:
        cfg = load_config()
        ensure_test_pattern_images(cfg)
        todo = [i for i in cfg.get("instances", []) if not self._is_up(cfg, i)]
        if not todo:
            log_event("All instances already running.")
            return
        started, failed = [], []
        # One at a time, 5s apart: receivers lock onto the wrong NDI source if outputs start together.
        for n, inst in enumerate(todo):
            if self._cancel.is_set():
                log_event("Start All cancelled by Stop All.")
                return
            if self._launch(cfg, inst):
                started.append(inst["id"])
                if n < len(todo) - 1:
                    self._gap()
            else:
                failed.append(inst["id"])
        message = f"Started {len(started)}/{len(todo)} instances"
        if failed:
            self._report(f"{message} (failed: {failed})")
        else:
            log_event(message)

    def _gap(self) -> None:
        self._cancel.wait(5)  # Stop All ends the wait at once

    def _stop_all(self) -> None:
        self._cancel.clear()
        cfg = load_config()
        for inst in cfg.get("instances", []):
            if inst["id"] not in self._procs:
                self._adopt(cfg, inst)
        procs, self._procs = list(self._procs.values()), {}
        with ThreadPoolExecutor(max_workers=max(len(procs), 1)) as ex:
            list(ex.map(CasparProcessManager.stop, procs))
        log_event("All CasparCG instances stopped.")

    def _restart_all(self) -> None:
        self._stop_all()
        self._start_all()

    def _find(self, inst_id: int) -> tuple[dict, Optional[dict]]:
        cfg = load_config()
        return cfg, next((i for i in cfg.get("instances", []) if i["id"] == inst_id), None)

    def _stop_one(self, cfg: dict, inst: dict) -> None:
        if self._procs.get(inst["id"]) or self._adopt(cfg, inst):
            self._procs.pop(inst["id"]).stop()

    def _restart(self, inst_id: int) -> None:
        cfg, inst = self._find(inst_id)
        if inst:
            self._stop_one(cfg, inst)
            self._launch(cfg, inst, "restarted")

    def _stop(self, inst_id: int) -> None:
        cfg, inst = self._find(inst_id)
        if inst:
            self._stop_one(cfg, inst)
            log_event(f"{_label(inst)} stopped.")

    def _adopt_running(self) -> None:
        cfg = load_config()
        adopted = [i for i in cfg.get("instances", []) if self._adopt(cfg, i)]
        if adopted:
            log_event(f"Reconnected to {len(adopted)} running CasparCG instance(s).")

    # ------------------------------------------------------------ status

    def _probe(self, port: int) -> Probe:
        client = self._client(port)
        reply = client.send("VERSION")  # liveness: INFO can fail on a live server
        if not reply.startswith("201"):
            return OFF
        version = reply.partition("\n")[2].strip()
        return Probe(True, parse_layer_info(client.info_layer()), version)

    def poll_status(self) -> None:
        """Ask every instance whether it's live and what's playing, then publish one snapshot."""
        cfg = load_config()
        insts = cfg.get("instances", [])
        with ThreadPoolExecutor(max_workers=max(len(insts), 1)) as ex:
            results = list(ex.map(self._probe, [self.port(cfg, i) for i in insts]))
        # A new dict, swapped in whole: readers never see a half-built poll.
        self._status = {i["id"]: r for i, r in zip(insts, results, strict=True)}

    def _status_loop(self) -> None:
        while not self._status_stop.is_set():
            try:
                self.poll_status()
            except Exception:
                log.exception("Status poll failed")
            self._status_stop.wait(0.5)  # browsers see changes within a second

    def start_status_worker(self) -> None:
        self._status_stop.clear()
        threading.Thread(target=self._status_loop, daemon=True, name="status").start()

    def stop_status_worker(self) -> None:
        self._status_stop.set()

    def cached_status(self) -> dict[int, Probe]:
        """The last poll, by instance id."""
        return dict(self._status)


manager = InstanceManager()

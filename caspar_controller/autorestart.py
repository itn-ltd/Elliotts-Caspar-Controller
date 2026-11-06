"""Auto-restart for InstanceManager: crashed or hung instances come back, operators come first.

- Crash: the process exited. Hang: alive, but no VERSION reply for hang_timeout.
- A hung process is stopped as soon as it's found; only the relaunch waits for the back-off.
- Never restarts an output that's on program (tally): it says so and waits until it's off air.
- Back-off 2, 5, 15, 30, then 60 s; after 5 restarts in 10 minutes, "failed" until started again.
- An operator's Stop holds it off until they start the instance again.
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from caspar_controller import tally
from caspar_controller.settings import auto_restart_on, load as load_config

BACKOFF = (2, 5, 15, 30, 60)  # seconds before each relaunch
MAX_RESTARTS = 5  # in RESTART_WINDOW seconds, then give up
RESTART_WINDOW = 600


def label(inst: dict) -> str:
    return f"Inst {inst['id']} ({inst['name']})"


@dataclass
class Watch:
    """One instance's auto-restart record."""

    held: bool = False  # stopped by an operator: never restarted for them
    state: Optional[str] = None  # shown instead of "stopped", e.g. "crashed (1/5)", "failed"
    restarts: deque = field(default_factory=deque)  # times of recent restarts
    due: Optional[float] = None  # when the relaunch runs
    silent_since: Optional[float] = None  # alive but not answering AMCP, since
    told_on_air: bool = False  # already said it hung while on program


class AutoRestart:
    """Mixed into InstanceManager, which provides _procs, _status, _submit, _report, _launch."""

    def __init__(self):
        # Guards the watch records and _procs against the job worker: jobs start and finish
        # under it, and the watchdog only runs between jobs.
        self._lock = threading.RLock()
        self._watch: dict[int, Watch] = {}
        self._busy = False

    def _record(self, inst_id: int) -> Watch:
        return self._watch.setdefault(inst_id, Watch())

    def _operator(self, inst_ids: Optional[list], hold: bool) -> None:
        """An operator's Stop holds auto-restart off until they start it; either resets it."""
        if inst_ids is None:
            inst_ids = [i["id"] for i in load_config().get("instances", [])]
        with self._lock:
            for i in inst_ids:
                self._watch[i] = Watch(held=hold)

    def state_of(self, inst_id: int) -> Optional[str]:
        """crashed, hung, restarting or failed, while auto-restart deals with it."""
        return self._record(inst_id).state

    def watch(self) -> None:
        """Deal with each crashed or hung instance, and relaunch the ones now due."""
        cfg, now = load_config(), time.time()
        on_air = {name for name, t in tally.get_tally().items() if t.get("program")}
        with self._lock:
            if self._busy:  # Start All, Restart All etc. own the processes while they run
                return
            for inst in cfg.get("instances", []):
                self._check(cfg, inst, now, inst["ndi_name"] in on_air)

    def _check(self, cfg: dict, inst: dict, now: float, on_air: bool) -> None:
        i, w = inst["id"], self._record(inst["id"])
        if w.held or w.state == "failed":
            return
        if w.due is not None:
            if now >= w.due:
                w.due = None
                w.state = w.state.replace("crashed", "restarting").replace("hung", "restarting")
                self._submit(f"Auto-restart of instance {i}", self._relaunch, i)
            return
        m = self._procs.get(i)
        if not m:
            return
        enabled = auto_restart_on(cfg, inst)
        if not m.alive():
            code = m.exit_code()
            reason = "crashed" + (f" (exit code {code})" if code is not None else "")
            self._procs.pop(i)
            if enabled:
                self._schedule(inst, "crashed", reason, None)
            else:
                w.state = "crashed"
                self.console(i).add(f"--- {reason} ---")
                self._report(f"{label(inst)} {reason}. Auto-restart is off for it.")
            return
        probe = self._status.get(i)
        if probe and probe.live:
            w.silent_since, w.told_on_air = None, False
            return
        if w.silent_since is None:
            w.silent_since = now
        if not enabled or now - w.silent_since < cfg["hang_timeout"]:
            return
        reason = f"stopped answering for {cfg['hang_timeout']} s"
        if on_air:  # a frozen picture beats a cut to black: the operator decides
            if not w.told_on_air:
                w.told_on_air = True
                self._report(
                    f"{label(inst)} {reason} while on air: not restarting it until it's off air."
                )
            return
        self._schedule(inst, "hung", reason, self._procs.pop(i))

    def _schedule(self, inst: dict, kind: str, reason: str, hung) -> None:
        """Count the restart, stop a hung process now, and set when the relaunch runs."""
        i, now, w = inst["id"], time.time(), self._record(inst["id"])
        w.silent_since = None
        if hung:  # stopped now, so nothing can lose track of it while the back-off runs
            self._submit(f"Stop of hung instance {i}", hung.stop)
        while w.restarts and w.restarts[0] < now - RESTART_WINDOW:
            w.restarts.popleft()  # 10 minutes running cleanly resets the count
        self.console(i).add(f"--- {reason} ---")
        if len(w.restarts) >= MAX_RESTARTS:
            w.state = "failed"
            self._report(
                f"{label(inst)} {reason}. Gave up after {MAX_RESTARTS} restarts in"
                " 10 minutes: start it to try again."
            )
            return
        w.restarts.append(now)
        delay = BACKOFF[min(len(w.restarts), len(BACKOFF)) - 1]
        w.state = f"{kind} ({len(w.restarts)}/{MAX_RESTARTS})"
        w.due = now + delay
        self._report(
            f"{label(inst)} {reason}: restarting in {delay} s ({len(w.restarts)}/{MAX_RESTARTS})."
        )

    def _relaunch(self, inst_id: int) -> None:
        cfg, inst = self._find(inst_id)
        w = self._record(inst_id)
        if not inst or w.held:  # removed, or stopped by an operator meanwhile
            w.state = None
            return
        if self._launch(cfg, inst, "restarted"):
            w.state = None
        else:
            self._schedule(inst, "crashed", "failed to restart", None)

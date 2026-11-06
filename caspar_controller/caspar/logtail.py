"""Follow CasparCG's own log files: the console panel's feed.

Reading the logs, not a pipe, leaves CasparCG independent of this app: outputs keep running
when it quits or is upgraded, and instances it reconnects to have a console too.
"""

import os
import re

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # colour codes, if a build writes them
BACKFILL = 64 * 1024  # bytes read from the end of a log the first time it's seen


class LogTail:
    """New lines from the newest *.log in one folder, following CasparCG's daily rollover."""

    def __init__(self, folder: str):
        self.folder = folder
        self._path = None
        self._offset = 0
        self._partial = b""  # an unfinished last line, kept until its newline arrives

    def _newest(self):
        try:
            logs = [e for e in os.scandir(self.folder) if e.is_file() and e.name.endswith(".log")]
        except OSError:  # no folder yet: the instance hasn't started
            return None
        return max(logs, key=lambda e: e.stat().st_mtime).path if logs else None

    def read(self) -> list:
        newest = self._newest()
        if not newest:
            return []
        skip_first = False
        if newest != self._path:
            # The first log seen: show its last lines. A newer one (rollover): all of it.
            start = max(0, os.path.getsize(newest) - BACKFILL) if self._path is None else 0
            self._path, self._offset, self._partial = newest, start, b""
            skip_first = start > 0  # starting mid-file: the first line is a fragment
        try:
            with open(self._path, "rb") as f:
                if os.fstat(f.fileno()).st_size < self._offset:
                    self._offset = 0  # truncated or replaced
                f.seek(self._offset)
                data = f.read()
        except OSError:
            return []
        self._offset += len(data)
        *lines, self._partial = (self._partial + data).split(b"\n")
        if skip_first:
            lines = lines[1:]
        return [_ANSI.sub("", line.decode("utf-8", "replace")).rstrip("\r") for line in lines]

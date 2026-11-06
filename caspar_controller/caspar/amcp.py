"""CasparCG AMCP TCP client: one open connection, replies read to their end."""

import select
import socket
import threading
import xml.etree.ElementTree as ET


def has_control_chars(text: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in text)


def is_amcp_safe(value: str) -> bool:
    """True if ``value`` can go inside a quoted AMCP argument."""
    return '"' not in value and "\\" not in value and not has_control_chars(value)


def _is_whole_xml(lines: list) -> bool:
    try:
        ET.fromstring("\n".join(lines))
        return True
    except ET.ParseError:
        return False


class AMCPClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 5250, timeout: float = 1.5):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._lock = threading.Lock()
        self._sock = None
        self._buf = b""

    def send(self, command: str) -> str:
        """Send one command and return its whole reply, or ``ERROR: ...``."""
        # CR/LF would end this command and smuggle in another.
        if has_control_chars(command):
            return "ERROR: AMCP commands can't contain control characters"
        with self._lock:
            reused = self._sock is not None
            try:
                try:
                    return self._send_and_read(command)
                except ConnectionError:
                    if not reused:
                        raise
                    # CasparCG restarted since the last command: the old connection is dead.
                    self.close()
                    return self._send_and_read(command)
            except OSError as e:
                self.close()
                return f"ERROR: {e}"

    def _send_and_read(self, command: str) -> str:
        if self._sock is None:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._drain()
        self._sock.sendall((command + "\r\n").encode())
        if command.strip().upper() == "BYE":  # CasparCG closes without replying
            self.close()
            return ""
        return "\r\n".join(self._read_reply())

    def _drain(self) -> None:
        """Drop anything left from earlier replies (e.g. the command a 400 echoes back)."""
        self._buf = b""
        while select.select([self._sock], [], [], 0)[0]:
            if not self._sock.recv(65536):
                raise ConnectionError("CasparCG closed the connection")

    def _read_line(self) -> str:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("CasparCG closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode(errors="replace").rstrip("\r")

    def _read_reply(self) -> list:
        lines = [self._read_line()]
        code = lines[0][:3]
        if code == "200":  # data lines, then a blank line
            while line := self._read_line():
                lines.append(line)
        elif code == "201":  # one line of data, or an XML document over several (INFO)
            lines.append(self._read_line())
            if lines[1].startswith("<"):
                while not _is_whole_xml(lines[1:]):
                    lines.append(self._read_line())
        return lines

    def close(self) -> None:
        if self._sock:
            self._sock.close()
        self._sock = None

    def ping(self) -> bool:
        return self.send("VERSION").startswith("201")

    def info_layer(self, channel: int = 1, layer: int = 1) -> str:
        return self.send(f"INFO {channel}-{layer}")

    def play_html(self, channel: int, url: str) -> str:
        if not is_amcp_safe(url):
            return "ERROR: URL can't contain quotes, backslashes or control characters"
        return self.send(f'PLAY {channel}-1 [HTML] "{url}"')

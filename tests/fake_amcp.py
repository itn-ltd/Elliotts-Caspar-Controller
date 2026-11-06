"""A small fake CasparCG AMCP server for tests.

Records every command it receives and answers with CasparCG-style replies:
``201`` + data for VERSION/INFO, ``202 <VERB> OK`` for everything else.
Replies can be overridden per verb with ``replies={"INFO": "404 INFO FAILED"}``.
"""

import socket
import socketserver
import sys
import threading

EMPTY_LAYER_XML = '<layer><foreground><producer type="empty-producer"/></foreground></layer>'


class _TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets two servers bind the same port, so stopped-server checks lie.
    allow_reuse_address = sys.platform != "win32"


class FakeAMCPServer:
    def __init__(
        self,
        port: int = 0,
        version: str = "2.3.3 Stable",
        info_xml: str = EMPTY_LAYER_XML,
        replies: dict | None = None,
        log_path: str | None = None,
    ):
        self.version = version
        self.info_xml = info_xml
        self.replies = dict(replies or {})
        self.commands = []
        self.connections = []  # every connection accepted, open or closed
        self._dropped = set()
        self.log_path = log_path
        self._lock = threading.Lock()
        self.bye = threading.Event()

        server = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                server.connections.append(self.connection)
                # Like CasparCG: many commands per connection, closed on BYE.
                for raw in self.rfile:
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if self.connection in server._dropped:
                        return
                    if not line:
                        continue
                    server._record(line)
                    reply = server.reply_for(line)
                    if line.upper() == "BYE":
                        return
                    self.wfile.write(reply.encode())

        self._server = _TCPServer(("127.0.0.1", port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _record(self, line: str) -> None:
        with self._lock:
            self.commands.append(line)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

    def reply_for(self, command: str) -> str:
        verb = command.split(" ", 1)[0].upper()
        if verb in self.replies:
            return self.replies[verb] + "\r\n"
        if verb == "VERSION":
            return f"201 VERSION OK\r\n{self.version}\r\n"
        if verb == "INFO":
            return f"201 INFO OK\r\n{self.info_xml}\r\n"
        if verb == "BYE":
            self.bye.set()
            return ""
        return f"202 {verb} OK\r\n"

    def drop_connections(self) -> None:
        """Close every open connection, as a CasparCG restart would."""
        for conn in self.connections:
            self._dropped.add(conn)
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self.drop_connections()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

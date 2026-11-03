"""A small fake CasparCG AMCP server for tests.

Records every command it receives and answers with CasparCG-style replies:
``201`` + data for VERSION/INFO, ``202 <VERB> OK`` for everything else.
Replies can be overridden per verb with ``replies={"INFO": "404 INFO FAILED"}``.
"""

import socketserver
import threading

EMPTY_LAYER_XML = '<layer><foreground><producer type="empty-producer"/></foreground></layer>'


class FakeAMCPServer:
    def __init__(
        self, port=0, version="2.3.3 Stable", info_xml=EMPTY_LAYER_XML, replies=None, log_path=None
    ):
        self.version = version
        self.info_xml = info_xml
        self.replies = dict(replies or {})
        self.commands = []
        self.log_path = log_path
        self._lock = threading.Lock()
        self.bye = threading.Event()

        server = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                # The app opens one connection per command, so reply and close.
                line = self.rfile.readline().decode(errors="replace").rstrip("\r\n")
                if not line:
                    return
                server._record(line)
                reply = server.reply_for(line)
                if reply:
                    self.wfile.write(reply.encode())

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _record(self, line):
        with self._lock:
            self.commands.append(line)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

    def reply_for(self, command):
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

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

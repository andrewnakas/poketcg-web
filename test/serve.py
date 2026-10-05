"""Threaded static server for web/, plus one test-only route for a ROM that lives
outside the repo.

The ROM is never copied into web/ — it is streamed from wherever the caller built
it, under /_test/rom.gbc, and only the automated test ever asks for that path.
"""

import http.server
import os
import socketserver
import threading

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
WEB = os.path.normpath(WEB)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(port, rom_path=None):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=WEB, **kw)

        def do_GET(self):
            if rom_path and self.path.split("?")[0] == "/_test/rom.gbc":
                with open(rom_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return super().do_GET()

        def end_headers(self):
            # Deliberately NOT setting COOP/COEP: the real host is GitHub Pages,
            # which does not set them either, and EJS_threads is false so there is
            # no SharedArrayBuffer to enable. Keep the test environment honest.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass

    srv = Server(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv

"""Zero-dependency web UI for StackSense.

Serves static/index.html and two JSON endpoints using only the Python
standard library:

  GET  /                      the single-page UI
  GET  /api/scenario/<id>     a bundled scenario JSON (P1..P6 or tests/<name>)
  POST /api/recommend         body: scenario JSON -> standard output JSON
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .engine import recommend

BASE = os.path.dirname(os.path.abspath(__file__))
SOURCE_ROOT = os.path.dirname(BASE)
STATIC = os.path.join(BASE, "static")
SCENARIOS = os.path.join(SOURCE_ROOT, "scenarios")
TESTS = os.path.join(SOURCE_ROOT, "tests", "edge_scenarios")


class Handler(BaseHTTPRequestHandler):
    server_version = "StackSense/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            with open(os.path.join(STATIC, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
            return
        if self.path.startswith("/api/scenario/"):
            sid = self.path[len("/api/scenario/"):]
            base = TESTS if sid.startswith("test/") else SCENARIOS
            name = sid[len("test/"):] if sid.startswith("test/") else sid
            if not name.replace("_", "").replace("-", "").isalnum():
                self._send(400, json.dumps({"error": "bad id"}))
                return
            path = os.path.join(base, name + ".json")
            if not os.path.isfile(path):
                self._send(404, json.dumps({"error": "unknown scenario"}))
                return
            with open(path, "rb") as f:
                self._send(200, f.read())
            return
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path == "/api/recommend":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                scenario = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as e:
                self._send(400, json.dumps({"error": f"invalid JSON body: {e}"}))
                return
            self._send(200, json.dumps(recommend(scenario), indent=2, ensure_ascii=False))
            return
        self._send(404, json.dumps({"error": "not found"}))


def serve(port=8000):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"StackSense UI on http://127.0.0.1:{port}")
    httpd.serve_forever()

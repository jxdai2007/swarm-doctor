#!/usr/bin/env python3
"""Static dashboard server (U13): serves dashboard/ plus SSE event streams.

Read-only. Endpoints:
  GET  /                     -> dashboard/index.html
  GET  /<path>               -> static files under dashboard/
  GET  /snapshot?run=<name>  -> page payload from metrics+events JSON
  GET  /events?run=<name>    -> SSE stream of the run's events (fixture replay
                                  or recorded run)

Runs live under runs/<name>/: events.jsonl plus snapshot.json built by the
replay pipeline. No capability token is served or accepted here; the page is
read-only by default.
Usage: python scripts/serve_dashboard.py [port] [runs_dir]
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "dashboard"
MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".json": "application/json", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    runs_dir: Path = ROOT / "runs"

    def log_message(self, *a):  # quiet under capture
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/snapshot", "/events"):
            run = parse_qs(url.query).get("run", [""])[0]
            return self._stream(run, url.path == "/events")
        path = DASHBOARD / (url.path.lstrip("/") or "index.html")
        if not path.resolve().is_relative_to(DASHBOARD) or not path.is_file():
            return self._send(404, b"not found", "text/plain")
        self._send(200, path.read_bytes(), MIME.get(path.suffix, "text/plain"))

    def _stream(self, run, sse):
        run_dir = (self.runs_dir / run).resolve()
        if not run or "." in run or not run_dir.is_relative_to(self.runs_dir):
            return self._send(404, b"unknown run", "text/plain")
        snap = run_dir / "snapshot.json"
        if not snap.is_file():
            return self._send(404, b"no snapshot for run", "text/plain")
        # SSE data lines cannot contain raw newlines: compact framing
        payload = json.dumps(json.loads(snap.read_text()),
                             separators=(",", ":")).encode()
        if not sse:
            return self._send(200, payload, "application/json")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: snapshot\ndata: " + payload + b"\n\n")
        events = run_dir / "events.jsonl"
        if events.is_file():
            for line in events.read_text().splitlines():
                if line.strip():
                    self.wfile.write(b"data: " + line.encode() + b"\n\n")
                self.wfile.flush()
        self.wfile.write(b"data: [done]\n\n")


def serve(port=8765, runs_dir=None):
    Handler.runs_dir = Path(runs_dir) if runs_dir else ROOT / "runs"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"dashboard on http://127.0.0.1:{port} (runs: {Handler.runs_dir})")
    httpd.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765,
          sys.argv[2] if len(sys.argv) > 2 else None)

"""A tiny loopback status server for Deck: the hand gate and the model timeline, read-only.

    GET /api/hand-gate            -> the HandGate.status() dict (404 if there is no gate)
    GET /api/timeline?since=<id>  -> the call-log entries with id greater than `since`

Standard library only, 127.0.0.1 only, GET only; it can read state and nothing else (no way to move, stop or reset anything).
`python -m lookandpick.status_server --fake` serves a cycling fake gate so a UI can be built without a camera.
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

DECK_ORIGINS = ("http://127.0.0.1:8765", "http://localhost:8765")


def make_handler(get_gate, get_timeline=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, body) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin")
            if origin in DECK_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            if self.client_address[0] not in ("127.0.0.1", "::1") or host not in ("127.0.0.1", "localhost", "[::1]"):
                return self._send(403, {"error": "local requests only"})
            parts = urlsplit(self.path)
            if parts.path == "/api/hand-gate":
                status = get_gate()
                return self._send(200, status) if status is not None else self._send(404, {"error": "no hand gate is running"})
            if parts.path == "/api/timeline" and get_timeline is not None:
                try:
                    since = int(parse_qs(parts.query).get("since", ["0"])[0])
                except ValueError:
                    return self._send(400, {"error": "since must be an integer"})
                return self._send(200, [e for e in get_timeline() if e.get("id", 0) > since])
            self._send(404, {"error": "not found"})

        do_POST = do_PUT = do_DELETE = do_PATCH = lambda self: self._send(405, {"error": "read-only"})
    return Handler


def serve(get_gate, get_timeline=None, port: int = 8768) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(get_gate, get_timeline))
    import threading
    threading.Thread(target=server.serve_forever, name="status-server", daemon=True).start()
    return server


def fake_gate_status(now: float | None = None) -> dict:
    """A scripted cycle through every state, 3 s each, so a UI can be built and checked without a camera."""
    now = time.time() if now is None else now
    step = int(now // 3) % 5
    state, scale, nearest, stale = (("clear", 1.0, None, 0.01), ("slow", 0.55, 0.32, 0.02), ("stop", 0.0, 0.10, 0.02),
                                    ("latched", 0.0, 0.10, 0.02), ("stale", 0.0, None, 0.4))[step]
    return {"state": state, "scale": scale, "nearest_m": nearest, "hands": 0 if nearest is None else 1, "fps": 28.5,
            "latency_ms": {"p50": 22.0, "p95": 31.0}, "stale_s": stale, "reason": f"fake feed: {state}", "updated": now}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8768)
    p.add_argument("--fake", action="store_true", required=True, help="serve a cycling fake gate (the only standalone mode)")
    a = p.parse_args()
    serve(fake_gate_status, None, a.port)
    print(f"Fake hand-gate status on http://127.0.0.1:{a.port}/api/hand-gate (Ctrl-C to stop)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Local mission control: python -m lookandpick.mission_control.

Only FakeTracker and SimArm are used. Camera/video preview stays in the browser;
it is never interpreted as tracking data. No model or hardware is connected.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from .adapters.fake import FakeTracker, SimArm
from .safety import Safety, point_segment_distance
from .tools import Tools
from .world import Hand, WorldState

WEB_ROOT = Path(__file__).with_name('web')
DROP_ZONE = (0.12, -0.20, 0.0)


class MissionControl:
    """Small simulation session shared by the UI and its HTTP API."""

    def __init__(self):
        self._lock = threading.RLock()
        self.events = []
        self._event_id = 0
        self._revision = 0
        self.instance_id = uuid4().hex
        self.reset()

    def event(self, source, command, result, latency_ms=0, *, status='ok', scripted=False):
        self._event_id += 1
        self.events.append({
            'id': self._event_id, 'time': datetime.now(timezone.utc).isoformat(),
            'source': source, 'input': command, 'output': result,
            'latency_ms': round(latency_ms, 2), 'status': status, 'scripted': scripted,
        })
        self.events = self.events[-100:]

    def reset(self):
        with self._lock:
            self._revision += 1
            self.tracker, self.arm, self.safety = FakeTracker(), SimArm(), Safety()
            self.tools = Tools(WorldState(), self.tracker, self.arm, self.safety)
            self.selected = 'obj1'
            self.hand_mode = 'clear'
            self.started = time.monotonic()
            self.events = []
            self.event('system', 'start simulation', 'Session ready. No hardware or models connected.')

    def _set_hand(self, mode):
        if mode not in ('clear', 'approach', 'near'):
            raise ValueError('Unknown hand scenario')
        self.hand_mode = mode
        if mode == 'clear':
            self.tracker.set_hands([])
            return
        scene = self.tools.get_scene()
        target = next(o['position'] for o in scene['objects'] if o['id'] == self.selected)
        start = self.arm.position
        # Place the synthetic hand a known perpendicular distance from the path.
        dx, dy = target[0] - start[0], target[1] - start[1]
        length = math.hypot(dx, dy)
        nx, ny = (-dy / length, dx / length) if length > 1e-9 else (1.0, 0.0)
        offset = 0.32 if mode == 'approach' else 0.08
        position = ((start[0] + target[0]) / 2 + nx * offset,
                    (start[1] + target[1]) / 2 + ny * offset,
                    (start[2] + target[2]) / 2)
        self.tracker.set_hands([Hand('demo-hand', position)])

    def state(self):
        with self._lock:
            scene = self.tools.get_scene()
            target = next(o for o in scene['objects'] if o['id'] == self.selected)
            path = (self.arm.position, tuple(target['position']))
            hands = self.tracker.hands()
            scale = self.safety.allowed_speed(hands, path)
            separation = min((point_segment_distance(h.position, *path) for h in hands), default=None)
            return {
                'mode': 'simulation', 'scene': scene, 'selected': self.selected,
                'revision': self._revision,
                'instance_id': self.instance_id,
                'drop_zone': DROP_ZONE, 'hand_mode': self.hand_mode,
                'stopped': self.safety.stopped, 'gripping': self.arm.gripping,
                'speed_scale': scale, 'separation_m': separation,
                'uptime_s': round(time.monotonic() - self.started, 1),
                'events': list(self.events),
            }

    def action(self, payload):
        with self._lock:
            self._revision += 1
            name = payload.get('action')
            t0 = time.perf_counter()
            if name == 'reset':
                self.reset()
                return self.state()
            if name == 'select':
                selected = payload.get('id')
                if selected not in {o['id'] for o in self.tools.get_scene()['objects']}:
                    raise ValueError('Unknown scene object')
                self.selected = selected
                return self.state()
            if name == 'hand':
                self._set_hand(payload.get('mode'))
                result = f'Synthetic hand scenario: {self.hand_mode}'
                source, command = 'safety', f'hand({self.hand_mode})'
            elif name == 'stop':
                result = self.tools.stop()
                source, command = 'safety', 'stop()'
            elif name == 'cue':
                # An explicit demo event, never represented as a real model call.
                source, command = 'Muse Spark', 'pick up the red cube'
                result = 'pick(obj1) — scripted example; API not connected'
                self.event(source, command, result, scripted=True)
                return self.state()
            elif name in ('pick', 'place', 'command'):
                if name == 'command':
                    text = payload.get('text', '')
                    if not isinstance(text, str) or len(text) > 200:
                        raise ValueError('Command must be text under 200 characters')
                    match = re.fullmatch(r'(pick(?: up)?|place) (?:the )?(red cube|blue mug)', text.strip().lower())
                    if not match:
                        raise ValueError('Try “pick up the red cube” or “place the blue mug”. This is a demo parser, not a model.')
                    name = 'pick' if match[1].startswith('pick') else 'place'
                    self.selected = 'obj1' if match[2] == 'red cube' else 'obj2'
                source, command = 'tools', f'{name}({self.selected})'
                if name == 'pick':
                    result = self.tools.pick(self.selected)
                else:
                    result = self.tools.place(self.selected, DROP_ZONE)
                    # Fake detections must reflect a confirmed placement next refresh.
                    if result.startswith('placed') or result.startswith('blocked: released'):
                        self.tracker._objects = [replace(o, position=DROP_ZONE) if o.id == self.selected else o
                                                 for o in self.tracker.objects()]
            else:
                raise ValueError('Unknown action')
            status = 'blocked' if result.startswith(('blocked', 'error')) or name == 'stop' else 'ok'
            self.event(source, command, result, (time.perf_counter() - t0) * 1000, status=status)
            return self.state()


def make_handler(session):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, code, body, content_type='application/json; charset=utf-8'):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, value):
            self._send(code, json.dumps(value, allow_nan=False).encode())

        def _local_request(self):
            port = self.server.server_address[1]
            allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            return host in allowed and (origin is None or origin in {f'http://{h}' for h in allowed})

        def do_GET(self):
            if not self._local_request():
                return self._json(403, {'error': 'Local requests only'})
            path = unquote(urlsplit(self.path).path)
            if path == '/api/state':
                return self._json(200, session.state())
            relative = 'index.html' if path == '/' else path.lstrip('/')
            file = (WEB_ROOT / relative).resolve()
            if not file.is_relative_to(WEB_ROOT.resolve()) or not file.is_file():
                return self._json(404, {'error': 'Not found'})
            types = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
                     '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml',
                     '.ttf': 'font/ttf', '.txt': 'text/plain; charset=utf-8', '.json': 'application/json'}
            self._send(200, file.read_bytes(), types.get(file.suffix, 'application/octet-stream'))

        def do_POST(self):
            if not self._local_request():
                return self._json(403, {'error': 'Local requests only'})
            if self.path != '/api/action':
                return self._json(404, {'error': 'Not found'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self._json(415, {'error': 'Expected application/json'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 4096:
                    raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('Expected an object')
                self._json(200, session.action(payload))
            except (ValueError, TypeError) as exc:
                self._json(400, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(MissionControl()))
    print(f'Mission control: http://127.0.0.1:{server.server_port} (simulation only)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

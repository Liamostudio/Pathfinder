"""Local mission control: python -m lookandpick.mission_control.

Robot controls use FakeTracker and SimArm. Optional workspace reconstruction and
RGB people/hand monitoring run in separate local workers. No physical arm is connected.
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
from urllib.parse import unquote, urlsplit, parse_qs
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
            self.tracker, self.arm = FakeTracker(), SimArm(); self.safety = Safety.for_arm(self.arm)
            self.tools = Tools(WorldState(), self.tracker, self.arm, self.safety)
            self.tools.waits_for_hands = False     # this scripted backend answers a button press at once; the live runtime waits
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


def make_handler(session, workspace_service=None):
    from .workspaces.service import WorkspaceService
    from .workspaces.http import dispatch
    workspaces=workspace_service or WorkspaceService()
    from .deck_telemetry import Telemetry, sample
    def telemetry_event(source,command,result,latency_ms=0,**kwargs):
        if hasattr(session,'runtime'): session.runtime.log.record(source,command,result,latency_ms,**kwargs)
        else: session.event(source,command,result,latency_ms,**kwargs)
    telemetry=Telemetry(telemetry_event)
    session.telemetry=telemetry
    from .glasses_api import dispatch as glasses_dispatch
    if hasattr(session,'runtime') and not hasattr(session,'glasses_inputs'):
        from .glasses_input import GlassesInputs
        GlassesInputs(session)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, code, body, content_type='application/json; charset=utf-8'):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors " + ("'self'" if self.path.startswith('/replay/') else "'none'"))
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
            if glasses_dispatch(self,session,'GET'):return
            if dispatch(self,workspaces,'GET'):return
            path = unquote(urlsplit(self.path).path)
            try:
                if path == '/api/real-arm': return self._json(200,telemetry.snapshot())
                if path == '/api/real-arm/joints':
                    from .workspaces.placement import calibrated_joints
                    data=telemetry.snapshot()
                    if data['state']!='LIVE / READ-ONLY' or time.time()-(data['updated'] or 0)>1.5: raise ValueError('Fresh read-only telemetry is required')
                    return self._json(200,{'joint_positions':calibrated_joints(data['motors'])})
                if path == '/api/real-arm/sample': return self._json(200,sample())
                if path == '/api/state': return self._json(200, session.state())
                if path == '/api/timeline':
                    since=int(parse_qs(urlsplit(self.path).query).get('since',['0'])[0])
                    events=session.timeline() if hasattr(session,'timeline') else session.state()['events']
                    return self._json(200,[e for e in events if e['id']>since])
                if path == '/api/hand-gate':
                    if not hasattr(session,'hand_gate'): return self._json(503,{'error':'NOT CONNECTED'})
                    return self._json(200,session.hand_gate())
                if path == '/api/demo-replay':
                    if not hasattr(session,'demo_result'): raise ValueError('Run the seeded demo first')
                    return self._json(200,session.demo_result)
                if path == '/api/replay' and hasattr(session,'replay'): return self._json(200,session.replay())
                if path.startswith('/api/commands/') and hasattr(session,'job'): return self._json(200,session.job(path.rsplit('/',1)[1]))
            except (ValueError,TypeError) as exc: return self._json(400,{'error':str(exc)})
            except (OSError,RuntimeError) as exc: return self._json(503,{'error':str(exc)})
            if path.startswith('/replay/'):
                filename=path.removeprefix('/replay/')
                if filename not in {'index.html','style.css','replay.js','demo-data.js','demo.json'}:return self._json(404,{'error':'Not found'})
                file=Path(__file__).resolve().parents[1]/'replay'/filename
                if not file.is_file():return self._json(404,{'error':'Replay asset missing'})
                return self._send(200,file.read_bytes(),{'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json'}[file.suffix])
            relative = 'index.html' if path == '/' else path.lstrip('/')
            file = (WEB_ROOT / relative).resolve()
            if not file.is_relative_to(WEB_ROOT.resolve()) or not file.is_file():
                return self._json(404, {'error': 'Not found'})
            types = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
                     '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml',
                     '.ttf': 'font/ttf', '.txt': 'text/plain; charset=utf-8', '.json': 'application/json'}
            self._send(200, file.read_bytes(), types.get(file.suffix, 'application/octet-stream'))

        def do_PATCH(self):
            if not self._local_request():return self._json(403,{'error':'Local requests only'})
            if not dispatch(self,workspaces,'PATCH'):self._json(404,{'error':'Not found'})

        def do_DELETE(self):
            if not self._local_request():return self._json(403,{'error':'Local requests only'})
            if not dispatch(self,workspaces,'DELETE'):self._json(404,{'error':'Not found'})

        def do_POST(self):
            if not self._local_request():
                return self._json(403, {'error': 'Local requests only'})
            if glasses_dispatch(self,session,'POST'):return
            if dispatch(self,workspaces,'POST'):return
            if self.path not in ('/api/action','/api/command','/api/stop','/api/real-arm/connect','/api/real-arm/disconnect','/api/demo'):
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
                if self.path == '/api/demo' and hasattr(session,'submit_demo'):result=session.submit_demo()
                elif self.path == '/api/real-arm/connect': result=telemetry.connect(payload.get('port'))
                elif self.path == '/api/real-arm/disconnect': result=telemetry.disconnect()
                elif self.path == '/api/command' and hasattr(session,'submit'):
                    result=session.submit(payload.get('text'))
                elif self.path == '/api/stop': result=session.action({'action':'stop'})
                else: result=session.action(payload)
                self._json(202 if 'job_id' in result else 200,result)
            except (ValueError, TypeError) as exc:
                self._json(400, {'error': str(exc)})
            except (OSError,RuntimeError) as exc:
                self._json(503, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--workspace-data',type=Path,default=Path(__file__).resolve().parents[1]/'recordings/workspaces')
    parser.add_argument('--backend',choices=('fake','sim'),default='fake')
    parser.add_argument('--model',choices=('fake','spark','glimmer'),default='fake')
    parser.add_argument('--sim-url',default='http://127.0.0.1:8766')
    parser.add_argument('--hand-gate',type=int,metavar='CAM',help='Explicitly start a real camera hand gate controlling the SIMULATED arm')
    parser.add_argument('--intrinsics',help='Lens calibration for --hand-gate')
    parser.add_argument('--hand-gate-status-url',help='External loopback status server; display only')
    parser.add_argument('--second-camera',type=int,metavar='CAM',help='With --hand-gate: a second camera (its own lens file) joins the same gate; a hand seen by either slows the arm and a stalled camera stops it')
    parser.add_argument('--second-intrinsics',help='Lens calibration for --second-camera (run at 1280x720)')
    parser.add_argument('--gate-size',default='640x360',help='Main hand-gate camera resolution WxH; its lens file must be calibrated at the same size (default 640x360)')
    parser.add_argument('--sam-people',action='store_true',help='With --hand-gate: also segment people and hands with SAM 3.1 (needs scripts/sam_worker.py running) into the voxel map; slow, advisory')
    parser.add_argument('--sam-main-only',action='store_true',help='With --sam-people: ask SAM about the main camera only (halves the GPU and CPU load; the hand gate still uses every camera)')
    args = parser.parse_args()
    from .workspaces.service import WorkspaceService
    if args.backend=='sim':
        from .deck_runtime import create_session
        session=create_session(args.model,args.sim_url,hand_camera=args.hand_gate,intrinsics=args.intrinsics,status_url=args.hand_gate_status_url,second_camera=args.second_camera,second_intrinsics=args.second_intrinsics,gate_size=tuple(int(v) for v in args.gate_size.lower().split('x')))
    else: session=MissionControl()
    workspaces=WorkspaceService(args.workspace_data)
    runtime=getattr(session,'runtime',None)
    if runtime is not None and hasattr(runtime,'tools'):
        # The no-go cubes a person draws in the active workspace are hard rules for the (simulated) arm's moves.
        from .zones import from_areas
        runtime.tools.zones=from_areas(workspaces.areas)
    feed=sam_feed=None;sam_extra=[]
    gate=getattr(getattr(session,'runtime',None),'gate',None)
    if gate is not None:
        # The safety camera's hands, shown live in the active workspace's voxel map. Read-only and isolated from the gate (advisory display).
        from .voxel_feed import for_workspaces, gate_hands
        feed=for_workspaces(workspaces.areas,gate_hands(gate));feed.start()
        if args.sam_people:
            from .adapters.sam_client import SamClient
            from .sam_people import SamPeopleFeed
            from .voxel_feed import workspace_target
            current,observe=workspace_target(workspaces.areas)
            sam_feed=SamPeopleFeed(current,observe,lambda:(gate.camera.latest() or (None,None))[1],SamClient(timeout_s=20),gate.calibration);sam_feed.start()
            for extra in ([] if args.sam_main_only else gate.extra_cameras):   # SAM on each extra camera too: its own calibration, its own source name
                more=SamPeopleFeed(current,observe,lambda cam=extra.camera:(cam.latest() or (None,None))[1],SamClient(timeout_s=20),extra.calibration,source='sam-people-'+extra.name);more.start();sam_extra.append(more)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(session,workspaces))
    print(f'Mission control: http://127.0.0.1:{server.server_port} (simulation only)' + (' with live hands in the voxel map' if feed else ''), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if feed: feed.close()
        if sam_feed: sam_feed.close()
        for more in sam_extra: more.close()
        if hasattr(session,'close'): session.close()
        session.telemetry.disconnect()


if __name__ == '__main__':
    main()

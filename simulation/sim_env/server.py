"""Loopback-only simulation HTTP bridge and browser workbench."""
from __future__ import annotations
import argparse
import io
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4
import numpy as np
from .engine import Simulation
from .cameras import Cameras

WEB = Path(__file__).resolve().parents[1] / 'web'


class Session:
    def __init__(self, *, render=True, realtime=True, camera_options=None):
        self.sim = Simulation(realtime=realtime)
        self.cameras = Cameras(self.sim, **(camera_options or {})) if render else None
        self.jobs = {}
        self.lock = threading.Lock()
        self.active = None
        self.closing = False

    def state(self):
        result = self.sim.state()
        result['cameras'] = self.cameras.snapshot() if self.cameras else {'ready':False, 'error':'Rendering disabled'}
        with self.lock:
            result['active_job'] = self.active
        return result

    def submit(self, payload):
        action = payload.get('action')
        if action == 'stop':
            self.sim.halt()
            return {'result':'stopped', 'state': self.state()}
        if action == 'hand':
            self.sim.set_hand(payload.get('position'))
            return {'result':'hand updated'}
        if action == 'camera':
            if not self.cameras:
                raise ValueError('Rendering disabled')
            self.cameras.configure(payload.get('name'), payload.get('pose', {}))
            return {'result':'camera updated'}
        if action == 'speed':
            self.sim.set_speed_scale(payload.get('scale'))
            return {'result':'speed updated'}
        if action not in ('pick','place','move','joints','grip','reset'):
            raise ValueError('Unknown action')
        with self.lock:
            if self.closing:
                raise ValueError('Session closing')
            if self.active:
                raise ValueError('Another action is in progress; stop is always available')
            job_id = uuid4().hex
            self.active = job_id
            self.jobs[job_id] = {'id':job_id, 'action':action, 'status':'running'}
            # Bound history, keeping current job.
            while len(self.jobs) > 100:
                del self.jobs[next(iter(self.jobs))]
        threading.Thread(target=self._execute, args=(job_id, payload), name='sim-command', daemon=True).start()
        return {'job_id':job_id}

    def _execute(self, job_id, payload):
        action = payload['action']
        try:
            if action == 'pick':
                result = self.sim.tools.pick(payload['id'])
            elif action == 'place':
                result = self.sim.tools.place(payload['id'], tuple(payload['position']))
            elif action == 'move':
                result = self.sim.move_to(tuple(payload['position']), payload.get('speed_scale',1.0))
            elif action == 'joints':
                result = self.sim.move_joints(payload['positions'])
            elif action == 'grip':
                result = self.sim.grip(payload['closed'])
            else:
                self.sim.reset()
                result = 'reset'
            with self.sim.lock:
                self.sim.event(f'{action}: {result}')
            status = 'completed'
            if result is False or (isinstance(result,str) and result.startswith(('blocked:', 'error:'))):
                status = 'refused'
            with self.lock:
                self.jobs[job_id].update(status=status, result=result, reason=self.sim.reason)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            with self.lock:
                self.jobs[job_id].update(status='error', error=str(exc))
        except Exception as exc:
            self.sim.halt()
            with self.lock:
                self.jobs[job_id].update(status='error', error=f'{type(exc).__name__}: {exc}')
        finally:
            with self.lock:
                self.active = None

    def job(self, key):
        with self.lock:
            if key not in self.jobs:
                raise KeyError('Unknown or expired job')
            return dict(self.jobs[key])

    def close(self):
        with self.lock:
            self.closing = True
        self.sim.close()
        if self.cameras:
            self.cameras.close()


def handler_for(session):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def local(self):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            return self.headers.get('Host') in hosts and self.headers.get('Origin') in (None, *('http://'+h for h in hosts))

        def send(self, code, body, content_type='application/json'):
            self.send_response(code)
            for key, value in {'Content-Type':content_type, 'Content-Length':str(len(body)), 'Cache-Control':'no-store',
                               'X-Content-Type-Options':'nosniff',
                               'Content-Security-Policy':"default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'"}.items():
                self.send_header(key,value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def json(self, code, value):
            self.send(code,json.dumps(value,allow_nan=False).encode())

        def do_GET(self):
            if not self.local():
                return self.json(403, {'error':'Loopback requests only'})
            path = urlsplit(self.path).path
            try:
                if path == '/api/replay':
                    return self.json(200, session.sim.replay())
                if path == '/api/state':
                    return self.json(200, session.state())
                if path.startswith('/api/jobs/'):
                    return self.json(200,session.job(path.rsplit('/',1)[1]))
                if path.startswith('/api/cameras/'):
                    if not session.cameras:
                        raise RuntimeError('Rendering disabled')
                    name, kind = path.removeprefix('/api/cameras/').split('/')
                    frame = session.cameras.frame(name)
                    if kind == 'rgb.jpg':
                        return self.send(200,frame['jpeg'],'image/jpeg')
                    if kind == 'metadata':
                        return self.json(200,frame['metadata'])
                    if kind == 'frame.npz':
                        buffer = io.BytesIO()
                        np.savez_compressed(buffer, rgb=frame['rgb'], depth=frame['depth'], segmentation=frame['segmentation'],
                                            K=np.array(frame['metadata']['K']), world_from_camera=np.array(frame['metadata']['world_from_camera']),
                                            dist=np.array(frame['metadata']['dist']), time_s=np.array(frame['metadata']['time_s']), timestamp=np.array(frame['metadata']['timestamp']))
                        return self.send(200,buffer.getvalue(),'application/octet-stream')
                routes = {'/':'index.html','/app.js':'app.js','/style.css':'style.css','/replay.html':'replay.html','/replay.js':'replay.js'}
                if path not in routes:
                    return self.json(404,{'error':'Not found'})
                file = WEB / routes[path]
                return self.send(200,file.read_bytes(),{'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css'}[file.suffix])
            except KeyError as exc:
                self.json(404,{'error':str(exc)})
            except (ValueError, TypeError) as exc:
                self.json(400,{'error':str(exc)})
            except RuntimeError as exc:
                self.json(503,{'error':str(exc)})

        def do_POST(self):
            if not self.local():
                return self.json(403,{'error':'Loopback requests only'})
            if self.path != '/api/action':
                return self.json(404,{'error':'Not found'})
            try:
                if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                    raise ValueError('Expected application/json')
                size = int(self.headers.get('Content-Length',0))
                if not 0 < size <= 8192:
                    raise ValueError('Request must contain 1–8192 bytes')
                payload = json.loads(self.rfile.read(size), parse_constant=lambda s: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
                if not isinstance(payload,dict):
                    raise ValueError('Expected an object')
                result = session.submit(payload)
                self.json(202 if 'job_id' in result else 200,result)
            except (ValueError, TypeError, KeyError) as exc:
                self.json(400,{'error':str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--no-render',action='store_true',help='Controller/physics only, no OpenGL required')
    parser.add_argument('--resolution', choices=['640x360','1280x720'], default='640x360')
    parser.add_argument('--fps', type=int, choices=range(1,31), default=10)
    parser.add_argument('--calibration-dir', type=Path)
    args = parser.parse_args()
    width, height = map(int,args.resolution.split('x'))
    session = Session(render=not args.no_render, camera_options={'width':width,'height':height,'fps':args.fps,'calibration_dir':args.calibration_dir})
    server = ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(session))
    print(f'SO-101 simulation: http://127.0.0.1:{server.server_port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        session.close()

if __name__ == '__main__':
    main()

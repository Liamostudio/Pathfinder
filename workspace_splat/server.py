"""Local capture ingest/job service and embeddable Gaussian-splat viewer."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit,unquote
from uuid import uuid4

WEB=Path(__file__).with_name('web')
SAFE_ID=re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')


def worker_alive(pid,capture):
    """Verify a detached Linux worker before reporting or signalling a saved PID."""
    if type(pid) is not int or pid<=1:return False
    try:
        proc=Path('/proc')/str(pid)
        if proc.stat().st_uid!=os.getuid():return False
        if (proc/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':return False
        args=(proc/'cmdline').read_bytes().decode().split('\0')
        module=args.index('-m')+1
        if args[module] not in ('workspace_splat.worker','workspace_splat'):return False
        job=args[module+1:]
        if args[module]=='workspace_splat.worker':
            target=job[job.index('--capture')+1]
        else:
            if job[0] not in ('train','reconstruct'):return False
            target=job[1]
        return Path(target).resolve()==capture.resolve() and os.getpgid(pid)==pid
    except (OSError,ValueError,IndexError,UnicodeError):return False


class Library:
    def __init__(self,data):
        self.data=Path(data).resolve();self.data.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.process=None;self.active=None
        self._fal_key=""

    def capture(self,key):
        if not isinstance(key,str) or not SAFE_ID.fullmatch(key):raise ValueError('Invalid capture ID')
        result=self.data/key
        if not result.resolve().is_relative_to(self.data):raise ValueError('Invalid capture path')
        return result

    def list(self):
        result=[]
        for path in sorted(self.data.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
            if not path.is_dir() or not SAFE_ID.fullmatch(path.name):continue
            result.append(self.status(path.name))
        return result

    def status(self,key):
        capture=self.capture(key)
        if not capture.is_dir():raise FileNotFoundError('Capture not found')
        result={'id':key,'name':key,'stage':'new','trained':False}
        for filename,label in [('status.json',None),('capture.json','capture'),('reconstruction.json','reconstruction'),('output/scene.json','scene')]:
            path=capture/filename
            if path.is_file():
                value=json.loads(path.read_text())
                if label:result[label]=value
                else:result.update(value)
        result['ready']='scene' in result
        result['trained']=bool(result.get('scene',{}).get('trained',False))
        result['generated']=bool(result.get('scene',{}).get('generated',False))
        if not result.get('worker_pid') and (capture/'worker.json').is_file():
            result['worker_pid']=json.loads((capture/'worker.json').read_text()).get('worker_pid')
        with self.lock:
            running=self.active==key and self.process is not None and self.process.poll() is None
            if self.active==key and self.process is not None and self.process.poll() is not None:
                if result['stage'] not in ('complete','failed','cancelled'):
                    result['stage']='failed';result['error']='Worker exited. Inspect this capture’s worker.log.'
        if result.get('worker_pid') and result['stage'] not in ('complete','failed','cancelled'):
            running=running or worker_alive(result['worker_pid'],capture)
            if not running:
                result['stage']='failed';result['error']='Worker stopped before completion; retry this capture.'
        result['running']=running
        return result

    def providers(self):
        with self.lock:
            return {'local':{'available':True},
                    'sam3d-fal':{'available':True,'configured':bool(self._fal_key),
                                  'model':'Meta SAM 3D Objects','host':'fal.ai'},
                    'meta-direct':{'available':False,'reason':'No public SAM 3D endpoint is documented on Meta Model API.'}}

    def set_key(self,provider,key):
        if provider!='sam3d-fal':raise ValueError('This backend requires a fal API key; Meta Model API keys are not compatible')
        if not isinstance(key,str) or len(key)>4096 or any(ord(c)<33 or ord(c)>126 for c in key.strip()):
            raise ValueError('Invalid API key')
        with self.lock:self._fal_key=key.strip()
        return {'configured':bool(key.strip()),'storage':'server memory only'}

    def new(self):
        key='capture-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6]
        folder=self.capture(key);(folder/'uploads').mkdir(parents=True)
        return key

    def upload(self,key,filename,content):
        with self.lock:return self._upload(key,filename,content)

    def _upload(self,key,filename,content):
        from .ingest import IMAGE_EXTENSIONS,VIDEO_EXTENSIONS
        capture=self.capture(key)
        if not capture.is_dir():raise FileNotFoundError('Create a capture first')
        if Path(filename).name!=filename or Path(filename).suffix.lower() not in IMAGE_EXTENSIONS|VIDEO_EXTENSIONS|{'.json'}:
            raise ValueError('Upload images, videos or timestamp sidecars with a simple filename')
        if self.status(key)['running']:raise ValueError('Cannot upload while processing')
        directory=capture/'uploads';directory.mkdir(exist_ok=True)
        path=directory/filename
        if path.exists():raise ValueError('Duplicate upload filename')
        size=sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
        if size+len(content)>2*1024**3:raise ValueError('Capture upload budget is 2 GiB')
        temp=path.with_name(path.name+'.upload');temp.write_bytes(content);temp.replace(path)
        return {'uploaded':filename,'bytes':len(content)}

    def launch(self,key, *, steps=2500,backend='local',image='',prompt='table',allow_external_upload=False):
        capture=self.capture(key)
        if not capture.is_dir():raise FileNotFoundError('Capture not found')
        if backend not in ('local','sam3d-fal'):raise ValueError('Unknown reconstruction backend')
        if backend=='sam3d-fal':
            if allow_external_upload is not True:raise ValueError('Confirm sending the selected image to fal.ai and using fal billing')
            if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>200:raise ValueError('Object prompt must contain 1–200 characters')
            if not isinstance(image,str) or (image and Path(image).name!=image):raise ValueError('Invalid source image')
        if type(steps) is not int or not 100<=steps<=10000:raise ValueError('Training steps must be an integer from 100 to 10000')
        with self.lock:
            if self.status(key)['running']:raise ValueError('This capture is already processing')
            if self.process is not None and self.process.poll() is None:raise ValueError('A GPU job is already running')
            if any(entry['running'] for entry in self.list()):raise ValueError('A capture job is already running')
            if backend=='sam3d-fal' and not self._fal_key:raise ValueError('Add a fal API key before using SAM 3D')
            if (capture/'output/scene.json').exists():
                if backend=='local':raise ValueError('This capture already has a scene; create a new capture to rebuild')
                manifest=json.loads((capture/'capture.json').read_text())
                if image and not any(image in (i['name'],i['source']) for i in manifest['images']):raise ValueError('Selected image is not in this capture')
                previous=capture
                key=self.new();capture=self.capture(key)
                shutil.copytree(previous/'images',capture/'images')
                shutil.copy2(previous/'capture.json',capture/'capture.json')
            if backend=='sam3d-fal':
                if not self._fal_key:raise ValueError('Add a fal API key before using SAM 3D')
                if not (capture/'capture.json').is_file() and not (capture/'uploads').is_dir():raise ValueError('Upload an image first')
                args=['--capture',str(capture),'--backend','sam3d-fal']
                if not (capture/'capture.json').is_file():args.append('--uploads')
            elif (capture/'initial.npz').is_file():
                args=['train',str(capture),'--steps',str(steps)]
            elif (capture/'capture.json').is_file():
                args=['reconstruct',str(capture)]
                # Existing ingested dataset: reconstruct then train in one worker wrapper.
                args=['--capture',str(capture),'--steps',str(steps)]
            else:
                uploads=capture/'uploads'
                if not uploads.exists() or not any(uploads.iterdir()):raise ValueError('Upload some images or a video first')
                args=['--capture',str(capture),'--steps',str(steps),'--uploads']
            log=(capture/'worker.log').open('ab')
            (capture/'worker.json').unlink(missing_ok=True)
            temp=capture/'status.json.tmp'
            temp.write_text(json.dumps({'stage':'queued','updated_at':time.time()}));temp.replace(capture/'status.json')
            try:
                module='workspace_splat.worker' if args[0].startswith('--') else 'workspace_splat'
                self.process=subprocess.Popen([sys.executable,'-u','-m',module,*args],cwd=Path(__file__).resolve().parent.parent,
                                              stdin=subprocess.PIPE if backend=='sam3d-fal' else subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
                temp=capture/'worker.json.tmp';temp.write_text(json.dumps({'worker_pid':self.process.pid}));temp.replace(capture/'worker.json')
                if backend=='sam3d-fal':
                    credentials=json.dumps({'api_key':self._fal_key,'image':image,'prompt':prompt}).encode()
                    try:
                        self.process.stdin.write(credentials);self.process.stdin.close()
                    except OSError:
                        self.process.terminate()
                        try:self.process.wait(timeout=5)
                        except subprocess.TimeoutExpired:pass
                        raise ValueError('Worker stopped before accepting credentials; inspect worker.log') from None
            except OSError:
                temp=capture/'status.json.tmp';temp.write_text(json.dumps({'stage':'failed','error':'Could not start the capture worker'}));temp.replace(capture/'status.json')
                raise ValueError('Could not start the capture worker; inspect the local runtime') from None
            finally:log.close()
            self.active=key
            return {'id':key,'pid':self.process.pid}

    def cancel(self,key):
        capture=self.capture(key)
        with self.lock:
            local=self.active==key and self.process is not None and self.process.poll() is None
            status=self.status(key)
            pid=self.process.pid if local else status.get('worker_pid')
            if not status['running'] or not worker_alive(pid,capture):raise ValueError('No active job for this capture')
            try:os.killpg(pid,signal.SIGTERM)
            except ProcessLookupError:pass
            deadline=time.monotonic()+5
            while worker_alive(pid,capture) and time.monotonic()<deadline:time.sleep(.05)
            if worker_alive(pid,capture):raise ValueError('Cancellation requested; worker is still stopping. Try again shortly.')
            if local:self.process.wait(timeout=1)
            temp=capture/'status.json.tmp';temp.write_text(json.dumps({'stage':'cancelled','updated_at':time.time()}));temp.replace(capture/'status.json')
            self.active=None
        return {'cancelled':key}


def handler_for(library):
    class Handler(BaseHTTPRequestHandler):
        timeout=60
        def log_message(self,*args):pass
        def local(self):
            hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            return self.headers.get('Host') in hosts and self.headers.get('Origin') in {None,*('http://'+h for h in hosts)}
        def send(self,code,body,kind='application/json'):
            self.send_response(code)
            headers={'Content-Type':kind,'Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                     'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'self' http://127.0.0.1:8766 http://localhost:8766 http://127.0.0.1:8765 http://localhost:8765"}
            for k,v in headers.items():self.send_header(k,v)
            self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def json(self,code,payload):self.send(code,json.dumps(payload,allow_nan=False).encode())
        def do_GET(self):
            if not self.local():return self.json(403,{'error':'Loopback requests only'})
            path=urlsplit(self.path).path
            try:
                if path=='/api/providers':return self.json(200,library.providers())
                if path=='/api/captures':return self.json(200,library.list())
                if path.startswith('/api/captures/'):
                    return self.json(200,library.status(path.split('/')[3]))
                if path.startswith('/scenes/'):
                    parts=path.split('/');key=parts[2];relative='/'.join(parts[3:])
                    capture=library.capture(key)
                    # Only publish explicit products/thumbnails, never arbitrary input paths.
                    allowed={'scene.json','scene.splat','scene.ply','gaussians.npz'}
                    if relative in allowed:file=capture/'output'/relative
                    elif re.fullmatch(r'previews/\d{3}\.jpg',relative):file=capture/'output'/relative
                    elif re.fullmatch(r'images/\d{5}\.jpg',relative):file=capture/relative
                    else:raise FileNotFoundError('Unknown scene product')
                    if not file.is_file():raise FileNotFoundError('Product not available yet')
                    return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                routes={'/':'index.html','/viewer':'viewer.html','/viewer.js':'viewer.js','/app.js':'app.js','/style.css':'style.css','/viewer.css':'viewer.css'}
                if path not in routes:raise FileNotFoundError('Not found')
                file=WEB/routes[path]
                self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'text/plain')
            except (ValueError,KeyError,TypeError) as exc:self.json(400,{'error':str(exc)})
            except FileNotFoundError as exc:self.json(404,{'error':str(exc)})
        def do_POST(self):
            if not self.local():return self.json(403,{'error':'Loopback requests only'})
            try:
                length=int(self.headers.get('Content-Length',0))
                path=urlsplit(self.path).path
                if path.startswith('/api/upload/'):
                    if not 1<=length<=256*1024**2:raise ValueError('Each upload must be 1 byte to 256 MiB')
                    key=path.split('/')[3];filename=unquote(self.headers.get('X-Filename',''))
                    body=self.rfile.read(length)
                    if len(body)!=length:raise ValueError('Incomplete upload')
                    return self.json(201,library.upload(key,filename,body))
                if not 1<=length<=8192:raise ValueError('Invalid request length')
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Expected JSON')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('Expected object')
                if path=='/api/providers/key':return self.json(200,library.set_key(body.get('provider'),body.get('api_key')))
                if path=='/api/captures':return self.json(201,{'id':library.new()})
                if path=='/api/run':return self.json(202,library.launch(body.get('id'),steps=body.get('steps',2500),backend=body.get('backend','local'),image=body.get('image',''),prompt=body.get('prompt','table'),allow_external_upload=body.get('allow_external_upload',False)))
                if path=='/api/cancel':return self.json(200,library.cancel(body.get('id')))
                raise FileNotFoundError('Unknown action')
            except (ValueError,KeyError,TypeError) as exc:self.json(400,{'error':str(exc)})
            except FileNotFoundError as exc:self.json(404,{'error':str(exc)})
    return Handler


def serve(data,port=8790):
    library=Library(data)
    server=ThreadingHTTPServer(('127.0.0.1',port),handler_for(library))
    print(f'Workspace Gaussian lab: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

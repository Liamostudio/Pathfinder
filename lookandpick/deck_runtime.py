"""Deck's asynchronous Runtime adapter. Simulation only; never imports a hardware adapter."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from .models.base import ChatResult, ModelError, ToolCall
from .models.calllog import CallLog
from .runtime import Runtime, make_model
from .safety import point_segment_distance


def simulated_gate(state):
    scene = state['scene']; scale = state.get('speed_scale', 1.)
    hands = scene.get('hands', [])
    nearest = min((max(0., math.dist(h['position'], scene['arm_position'])-h.get('radius_m', 0)) for h in hands), default=None)
    return {'state': 'latched' if state.get('stopped') else 'stop' if scale == 0 else 'slow' if scale < 1 else 'clear',
            'scale': scale, 'nearest_m': nearest, 'hands': len(hands), 'fps': 0., 'latency_ms': {'p50': 0., 'p95': 0.},
            'stale_s': 0., 'updated': time.time(), 'source': 'simulation ground truth', 'simulated': True}


class ScriptedModel:
    """Offline scripted tools, visibly labelled; same Runtime/Tools path as a paid model."""
    name = 'Scripted model / no API'
    def __init__(self, log): self.log = log; self.requests = []
    def chat(self, messages, tools=None, max_tokens=1500):
        self.requests.append(messages)
        user = messages[1]['content']; scene_text, command = user.split('\n\nRequest: ', 1)
        scene = json.loads(scene_text.removeprefix('Scene: ')); objects = scene['objects']
        head=command.lower().split(' and ',1)[0]
        target=next((o for o in objects if o['label'].lower() in head),None)
        previous = [m for m in messages if m['role'] == 'tool']
        calls = ()
        if not target:
            reply = 'No object is available.'
        elif not previous:
            if command.lower().startswith('place'):
                calls = (ToolCall('place', 'place', json.dumps({'id':target['id'],'loc':[.18,-.2,.018]})),)
            else: calls = (ToolCall('pick', 'pick', json.dumps({'id':target['id']})),)
            reply = 'Scripted pick / place.'
        elif any(m['content'].startswith(('blocked','error')) for m in previous):
            reply = 'The tool refused the scripted action.'
        elif len(previous) == 1 and any(w in command.lower() for w in ('put','place')) and not command.lower().startswith('place'):
            blue = next((o for o in objects if 'blue' in o['label']), None)
            loc = [blue['position'][0], blue['position'][1]-.07, blue['position'][2]] if blue else [.18,-.2,.018]
            calls = (ToolCall('place', 'place', json.dumps({'id':target['id'],'loc':loc})),)
            reply = 'Scripted placement.'
        else: reply = 'Scripted run complete.'
        self.log.record(self.name, command, reply, 0., tokens={'input_tokens':0,'output_tokens':0})
        return ChatResult(reply, calls)


class RuntimeSession:
    def __init__(self, runtime, *, hand_gate=None):
        self.runtime = runtime; self.arm = runtime.arm; self.safety = runtime.safety
        self.instance_id = uuid4().hex; self.started = time.monotonic()
        self._lock = threading.RLock(); self._revision = 1; self.jobs = {}; self.active = None
        self.selected = None; self.hand_mode = 'clear'; self._hand_gate = hand_gate or (runtime.hand_gate_status if getattr(runtime,'gate',None) is not None else None)
        self.frames = []; self._record_start = time.monotonic(); self._shutdown = threading.Event()
        self.runtime.log.record('system','start simulator runtime','Runtime connected to simulation only',0)
        self._sampler = threading.Thread(target=self._sample,daemon=True,name='deck-recorder'); self._sampler.start()

    def _sample(self):
        while not self._shutdown.wait(.05):
            if self.active:
                try: self.state()
                except Exception: pass

    def state(self):
        raw = self.arm.state() if hasattr(self.arm,'state') else {}
        scene = raw.get('scene') or self.runtime.tools.get_scene()
        objects = scene['objects']
        if self.selected not in {o['id'] for o in objects}: self.selected = objects[0]['id'] if objects else None
        stopped = self.safety.stopped or raw.get('stopped',False)
        data = {'mode':'simulation','backend':'sim','scene':scene,'selected':self.selected,'revision':self._revision,
                'instance_id':self.instance_id,'drop_zone':[.18,-.2,.018],'hand_mode':self.hand_mode,
                'stopped':stopped,'gripping':raw.get('gripping',any(o.get('held') for o in objects)),
                'speed_scale':0 if stopped else raw.get('speed_scale',1.),'separation_m':None,
                'uptime_s':time.monotonic()-self.started,'events':self.timeline(),'busy':self.active is not None,
                'active_job':self.active,'joints':raw.get('joints',{}),'model':self.runtime.model.name}
        gate = self.hand_gate(data); data['hand_gate'] = gate; data['separation_m'] = gate.get('nearest_m') if gate else None
        with self._lock:
            frame = {'t':round(time.monotonic()-self._record_start,3),'scene':scene,'hand_gate':gate,'stopped':stopped,
                     'joints':data['joints'],'speed_scale':data['speed_scale']}
            if not self.frames or frame['t']-self.frames[-1]['t']>=.04: self.frames=(self.frames+[frame])[-18000:]
        return data

    def timeline(self): return self.runtime.timeline()

    def hand_gate(self, state=None):
        if self._hand_gate: return self._hand_gate()
        if state is None:
            raw=self.arm.state(); state={**raw,'stopped':self.safety.stopped or raw.get('stopped',False)}
        return simulated_gate(state)

    def submit(self, text):
        if not isinstance(text,str) or not text.strip() or len(text)>1000: raise ValueError('Enter 1–1,000 characters')
        from .models.orchestrator import STOP_WORDS
        if STOP_WORDS.match(text): self.stop(); return {'status':'stopped'}
        with self._lock:
            if self.active: raise ValueError('A command is already running; STOP is always available')
            if self.safety.stopped: raise ValueError('Stop is latched; reset the simulation first')
            key=uuid4().hex; self.jobs[key]={'id':key,'status':'running','command':text}; self.active=key; self._revision+=1
            while len(self.jobs)>100: self.jobs.pop(next(iter(self.jobs)))
        threading.Thread(target=self._run,args=(key,text),daemon=True,name='deck-runtime-command').start()
        return {'job_id':key,'status':'running'}

    def submit_demo(self):
        with self._lock:
            if self.active:raise ValueError('A command is already running; stop or wait first')
            if self.safety.stopped:raise ValueError('Stop is latched; reset the simulation first')
            self._demo_cancel=threading.Event()
            key=uuid4().hex;self.active=key;self.jobs[key]={'id':key,'status':'running','command':'seeded simulation demo'};self._revision+=1
        threading.Thread(target=self._run_demo,args=(key,),daemon=True,name='deck-seeded-demo').start()
        return {'job_id':key,'status':'running'}

    def _run_demo(self,key):
        root=Path(__file__).resolve().parents[1]
        try:
            with tempfile.TemporaryDirectory(prefix='pathfinder-demo-') as tmp:
                if self._demo_cancel.is_set():raise RuntimeError('Demo cancelled')
                output=Path(tmp)/'demo.json'
                self._demo_process=subprocess.Popen([str(root/'simulation/.venv/bin/python'),'-m','lookandpick.deck_demo','--output',str(output)],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                if self._demo_cancel.is_set():self._demo_process.terminate()
                stdout,stderr=self._demo_process.communicate(timeout=90)
                if self._demo_process.returncode:raise RuntimeError('Seeded demo interrupted or failed: '+stderr[-800:])
                self.demo_result=json.loads(output.read_text())
                job={'status':'completed','result':{'replay_url':'/api/demo-replay'}}
        except Exception as exc:
            if getattr(self,'_demo_process',None) and self._demo_process.poll() is None:self._demo_process.kill();self._demo_process.wait()
            job={'status':'error','error':str(exc)}
        finally:
            if self._demo_cancel.is_set():job={'status':'stopped','result':{'reply':'Demo cancelled'}}
            self._demo_process=None
            with self._lock:self.jobs[key].update(job);self.active=None;self._revision+=1

    def _run(self,key,text):
        try:
            result=self.runtime.command(text)
            job={'status':'stopped' if self.safety.stopped else 'error' if result.error else 'completed','result':asdict(result)}
        except Exception as exc:
            try: self.runtime.stop()
            except Exception: pass
            job={'status':'error','error':str(exc)}
        finally:
            with self._lock:
                self.jobs[key].update(job); self.active=None; self._revision+=1

    def job(self,key):
        with self._lock:
            if key not in self.jobs: raise ValueError('Unknown or expired command')
            return dict(self.jobs[key])

    def stop(self):
        # No action/command lock: must interrupt a command or an in-flight model request.
        self.safety.stop()
        if getattr(self,'_demo_cancel',None):self._demo_cancel.set()
        process=getattr(self,'_demo_process',None)
        if process and process.poll() is None:process.terminate()
        try: return self.runtime.stop()
        finally:
            self.runtime.log.record('Safety','stop()','Stop latched locally; no model call',0,status='blocked')
            self._revision+=1

    def action(self,payload):
        name=payload.get('action')
        if name=='stop': self.stop()
        elif name=='reset':
            with self._lock:
                if self.active: raise ValueError('Wait for the interrupted command to finish before reset')
                self.runtime.reset(); self.frames=[]; self._record_start=time.monotonic(); self._revision+=1
        elif name=='select':
            if payload.get('id') not in {o['id'] for o in self.runtime.tools.get_scene()['objects']}: raise ValueError('Unknown object')
            self.selected=payload['id']; self._revision+=1
        elif name=='hand':
            mode=payload.get('mode')
            if mode not in ('clear','approach','near'): raise ValueError('Unknown hand scenario')
            p=self.arm.position
            self.arm.action('hand',position=None if mode=='clear' else [p[0],p[1]+(.32 if mode=='approach' else .03),p[2]])
            self.hand_mode=mode; self._revision+=1
            self.runtime.log.record('Safety','synthetic hand',mode,0)
        elif name in ('pick','place','command'):
            target=next((o for o in self.runtime.tools.get_scene()['objects'] if o['id']==self.selected),None)
            text=payload.get('text') if name=='command' else f'{name} the {target["label"]}' if target else ''
            return self.submit(text)
        else: raise ValueError('Unknown runtime action')
        return self.state()

    def replay(self):
        events=self.timeline(); epoch=datetime.now(timezone.utc).timestamp()-(time.monotonic()-self._record_start)
        return {'schema_version':1,'mode':'simulation replay','title':'Recorded Deck runtime','seed':None,
                'frames':list(self.frames),'events':[{**e,'t':max(0,datetime.fromisoformat(e['time']).timestamp()-epoch)} for e in events],
                'limitations':['Simulation only; schematic playback. No physical safety validation.']}

    def close(self):
        self._shutdown.set()
        if getattr(self.runtime,'gate',None) is not None:self.runtime.gate.close()


def create_session(model_kind='fake',sim_url='http://127.0.0.1:8766',*,hand_camera=None,intrinsics=None,status_url=None):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'simulation'))
    from sim_env.client import SimulationClient
    log=CallLog(); model=ScriptedModel(log) if model_kind=='fake' else make_model(model_kind,log)
    arm=SimulationClient(sim_url);gate=None;tracker=arm
    if hand_camera is not None:
        from .hand_gate import live_hand_gate
        if status_url:raise ValueError('Choose a local gate or an external display source, not both')
        gate=live_hand_gate(arm,hand_camera,'weights/hand_landmarker.task',marker_mm=120.,intrinsics=intrinsics,hand_height=.08,width=640,height=360)
        gate.start();arm=gate.arm;tracker=gate.tracker(tracker)
    runtime=Runtime(model,arm=arm,tracker=tracker,log=log,say=lambda text:None,gate=gate,safety=gate.safety if gate else None)
    provider=None
    if status_url:
        from urllib.parse import urlsplit
        import urllib.request
        parsed=urlsplit(status_url)
        if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('Status source must be a loopback HTTP base URL')
        def provider():
            try:
                with urllib.request.urlopen(status_url.rstrip('/')+'/api/hand-gate',timeout=.1) as response:
                    raw=response.read(32769)
                    if len(raw)>32768:return None
                    data=json.loads(raw)
                if data is not None and not isinstance(data,dict):return None
                if data is not None:data.update(source='External hand-gate status / display only',external=True)
                return data
            except (OSError,ValueError):return None
    return RuntimeSession(runtime,hand_gate=provider)

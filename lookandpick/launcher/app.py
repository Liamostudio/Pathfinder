"""Click-driven simulator demo orchestration. Safety decisions remain in Deck."""
from datetime import datetime, timezone
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from uuid import uuid4

from .config import CAMERAS, Config
from .preflight import Probes, gpu_lease, workspace_status
from .supervisor import Supervisor, identity, port_owner


def request_json(base, path, payload=None, timeout=2):
    request=urllib.request.Request(base+path, data=None if payload is None else json.dumps(payload).encode(),
                                   headers={'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            data=response.read(24*1024*1024+1)
            if len(data)>24*1024*1024:raise ValueError('Service response too large')
            return json.loads(data)
    except urllib.error.HTTPError as exc:
        try:message=json.loads(exc.read(4096)).get('error',str(exc.code))
        except ValueError:message=f'HTTP {exc.code}'
        raise ValueError(str(message)) from None


class Launcher:
    def __init__(self, config=None, *, probes=None, request=request_json, workspace=workspace_status):
        self.config=config or Config();self.supervisor=Supervisor(self.config)
        self.probes=probes or Probes(self.config);self.request=request;self.workspace=workspace
        self.lock=threading.RLock();self.operation=threading.Lock()
        self.models=self.config.model_choices()
        self.options={'model':'fake','second_camera':True,'sam_people':True}
        self.safety={'arm_unpowered':False,'hands_out':False}
        self.checks=[];self.details={};self.external_owners={};self.job=None;self.cancel=None;self.mode='idle';self.error=None
        self.take=None;self.last_take=None;self.open_url=None;self.live={}
        self.cost_seen=set();self.spark_total=0.;self.spark_last=0.
        self.closed=threading.Event()
        self.monitor=threading.Thread(target=self._monitor,daemon=True);self.monitor.start()

    def snapshot(self):
        with self.lock:
            value={'application':'Pathfinder launcher','options':dict(self.options),'models':list(self.models),'safety':dict(self.safety),
                   'checks':self.checks,'details':self.details,'job':self.job,'mode':self.mode,
                   'error':self.error,'take':self.take,'last_take':self.last_take,'live':self.live,
                   'open_url':self.open_url,'preview':getattr(self.config,'preview',False),
                   'links':{'deck':self.config.url('deck'),'setup':self.config.url('deck')+'/#setup',
                            'workspace':self.config.url('deck')+'/#workspace','logs':'/logs',
                            'demo':'/docs/demo','guide':'/docs/launcher'},
                   'services':self.supervisor.status()}
            value=deepcopy(value)
        return self.supervisor.redactor.clean(value)

    def configure(self, payload):
        if set(payload)-{'model','second_camera','sam_people'}:raise ValueError('Unknown option')
        with self.lock:
            if self.job and self.job['status']=='running':raise ValueError('Wait for the current stage to finish')
            if 'model' in payload and payload['model'] not in self.models:raise ValueError('Choose a supported model')
            if any(k in payload and type(payload[k]) is not bool for k in ('second_camera','sam_people')):raise ValueError('Expected on/off options')
            self.options.update(payload)

    def attest(self, payload):
        if set(payload)!={'arm_unpowered','hands_out'} or any(type(v) is not bool for v in payload.values()):raise ValueError('Confirm both safety boxes explicitly')
        with self.lock:self.safety=dict(payload)
        if not all(payload.values()) and self.cancel:self.cancel.set()

    def require_safety(self):
        if not all(self.safety.values()):raise ValueError('Tick both manual safety checks before continuing; the real arm must stay unpowered')

    def launch(self, action):
        allowed={'check','setup','existing','live','reset','selfcheck','tls','relay_start','relay_stop','stop_all'}
        if action not in allowed:raise ValueError('Unknown launcher action')
        with self.lock:
            if action!='stop_all' and self.job and self.job['status']=='running':raise ValueError('A stage is already running')
            if action not in ('check','stop_all','relay_stop'):self.require_safety()
            if self.take and action not in ('check','stop_all'):raise ValueError('End the current take before changing services or resetting the scene')
            if action=='stop_all' and self.cancel:self.cancel.set()
            cancel=threading.Event();self.cancel=cancel
            job={'id':uuid4().hex,'action':action,'status':'running','lines':[],'started':time.time()}
            self.job=job;self.error=None;self.open_url=None
        def work():
            with self.operation:
                outcome='completed';error=None
                try:
                    getattr(self,'_'+action)(cancel,job)
                    if cancel.is_set():raise InterruptedError('Stopped by you')
                except Exception as exc:
                    outcome='cancelled' if isinstance(exc,InterruptedError) else 'failed'
                    error=self.supervisor.redactor.text(exc)
                finally:
                    with self.lock:
                        if error is not None:
                            job['error']=error
                            if self.job is job:self.error=error
                        job['finished']=time.time()
                        job['status']=outcome
        threading.Thread(target=work,daemon=True,name='launcher-'+action).start()
        return job['id']

    def line(self, job, text):
        with self.lock:
            job['lines'].append(self.supervisor.redactor.text(text));job['lines']=job['lines'][-80:]

    def ensure(self, cancel):
        if cancel.is_set() or self.closed.is_set():raise InterruptedError('Stopped by you')
        self.require_safety()

    def _check(self, cancel, job):
        self.line(job,'Checking local hardware; probes never command the real arm')
        checks=[]
        def add(key,status,message,**extra):checks.append({'id':key,'status':status,'message':message,**extra})
        nodes=self.probes.cameras()
        for name,(label,size,lens) in CAMERAS.items():
            if cancel.is_set():raise InterruptedError('Stopped by you')
            if name=='ugreen' and not self.options['second_camera']:
                add(name,'warn',f'{label}: second camera switched off');continue
            if name not in nodes:add(name,'fail',f'{label} missing; re-seat the cable');continue
            # Do not fight a running Deck for its cameras. A new Go live stops it before probing.
            if self.supervisor.running('deck') and self.mode=='live':
                add(name,'pass',f'{label}: held by the live gate; see freshness below')
            else:
                frame=self.probes.frame(nodes[name])
                add(name,'pass' if frame.get('ok') and frame.get('marker') else 'warn' if frame.get('ok') else 'fail',
                    f'{label} / video{nodes[name]["index"]}: '+('marker found' if frame.get('marker') else 'marker NOT in view; show the marker' if frame.get('ok') else frame.get('message','No frame; re-seat the cable')),
                    thumbnail=frame.get('thumbnail'))
            add(name+'-lens','pass' if self.probes.lens(lens,size) else 'fail',f'{lens}: expected {size[0]}×{size[1]}; '+('matched' if self.probes.lens(lens,size) else 'missing or mismatched; run lens calibration'))
        gpu=self.probes.gpu()
        gpu_ok=gpu['available'] and gpu['enough'] and gpu['free']
        add('gpu','pass' if gpu_ok else 'warn',f'GPU: {gpu.get("free_mb") or 0} MB free; '+('lease free' if gpu['free'] else 'lease held by '+str(gpu.get('owner')))+('. Switch SAM people off if no GPU is available.' if not gpu['available'] else ''))
        ports=self.probes.ports();self.external_owners=ports
        port_status={}
        for name,owner in ports.items():
            owned=self.supervisor.running(name)
            can_stop=bool(owned or self._external_allowed(name,owner))
            argv=owner.get('argv',[]) if owner else []
            process=Path(argv[0]).name if argv else 'process unavailable'
            script=next((Path(v).name for v in argv[1:] if v.endswith('.py')),None)
            if script:process+=' / '+script
            port_status[name]={'port':self.config.ports[name],'busy':owner is not None,'pid':owner.get('pid') if owner else None,'process':process if owner else None,'can_stop':can_stop}
            add('port-'+name,'pass' if owner is None or owned else 'warn',f'{name} {self.config.ports[name]}: '+('launcher-owned' if owned else f'busy · {process} · PID {owner.get("pid") or "unavailable"}; '+('Stop available' if can_stop else 'stop it locally; process group is not verified') if owner else 'free'),service=name if can_stop else None)
        present=self.probes.secrets();weights=self.probes.weights();network=self.probes.network();microphone=self.probes.microphone()
        add('key','pass' if present else 'warn','MODEL_API_KEY: '+('present (never displayed)' if present else 'missing in .env; add it locally for Spark and Voice'))
        for name,exists in weights.items():add('weights-'+name,'pass' if exists or name=='people_optional' else 'warn',{'hands':'Hand Landmarker','sam':'SAM 3.1','people_optional':'Optional browser person detector'}[name]+': '+('present' if exists else 'not installed; not required for SAM people' if name=='people_optional' else 'missing; install locally before using this detector'))
        add('network','pass' if network['meta_reachable'] and network.get('ip') else 'warn',f'Wi-Fi IPv4: {network.get("ip") or "not found"}; Meta API '+('reachable' if network['meta_reachable'] else 'unreachable; reconnect Wi-Fi')+'. Spark and Voice need internet; safety never does.')
        add('microphone','pass' if microphone else 'warn','Microphone: '+('present' if microphone else 'not found; connect one for voice, or use typed requests'))
        for name,path in [('setup',self.config.workspace_python),('live',self.config.hands_python),('simulator',self.config.sim_python),('SAM',self.config.sam_python)]:
            add('runtime-'+name,'pass' if Path(path).is_file() else 'warn',f'{name} interpreter: '+('present' if Path(path).is_file() else 'missing; restore the documented environment'))
        workspace=self.workspace(self.config)
        active=workspace['active'];active_text=f'{active["id"]} / {active["version"]}' if active else 'none'
        add('workspace','pass' if active else 'warn',f'Active workspace: {active_text} · {workspace["manual_count"]} manual no-go cubes')
        for warning in workspace['warnings']:add('zones','warn',warning)
        with self.lock:self.checks=checks;self.details={'cameras':nodes,'gpu':gpu,'ports':port_status,'network':network,'weights':weights,'key_present':present,'workspace':workspace,'tls':self.tls_status(network.get('ip'))}
        self.line(job,'Checks complete. Amber items explain the fallback or fix.')

    def _external_allowed(self, name, owner):
        if not owner or not owner.get('pid') or owner['pid']!=owner.get('pgid') or owner['pgid']==os.getpgrp():return False
        try:
            cwd=(Path('/proc')/str(owner['pid'])/'cwd').resolve(strict=True)
            if cwd!=self.config.root:return False
        except OSError:return False
        argv=owner['argv']
        def script(relative):return any((cwd/v).resolve()==(self.config.root/relative).resolve() for v in argv if v.endswith('.py'))
        if name=='deck':return 'lookandpick.mission_control' in argv and argv.count('--backend')==1 and argv[argv.index('--backend')+1:argv.index('--backend')+2]==['sim']
        if name=='simulator':return script('simulation/run.py')
        if name=='sam':return script('scripts/sam_worker.py')
        if name=='relay':return 'lookandpick.glasses_gateway' in argv and self.config.url('deck') in argv
        return False

    def open_logs(self):
        self.supervisor.start('logs',self.config.service('logs'))

    def stop_service(self, name):
        if name not in self.config.ports:raise ValueError('Unknown service')
        if self.take:raise ValueError('End the current take first, or use Stop all')
        if self.job and self.job['status']=='running':raise ValueError('Wait for the current stage, or use Stop all')
        if not self.supervisor.running(name):
            previous=self.external_owners.get(name) or {}
            current=port_owner(self.config.ports[name])
            if not current:return
            if any(current.get(k)!=previous.get(k) for k in ('pid','start','pgid','argv_hash')) or not self._external_allowed(name,current):raise ValueError('Process changed or is not a verified isolated demo service; re-check or stop it locally')
            self.supervisor.services[name]={'identity':current,'process':None,'reader':None}
        self.supervisor.stop(name)

    def wait_for(self, cancel, job, description, predicate, timeout, service=None):
        deadline=time.monotonic()+timeout;last='not ready'
        while time.monotonic()<deadline:
            self.ensure(cancel)
            if service and not self.supervisor.running(service):raise ValueError(f'{description}: process exited. '+self.supervisor.tail(service))
            try:
                if predicate():return
            except (OSError,ValueError,urllib.error.URLError) as exc:last=str(exc)
            cancel.wait(self.config.poll_s)
        raise ValueError(f'{description} timed out: {last}. '+(self.supervisor.tail(service) if service else 'Check the status and try again.'))

    def get(self, name, path, payload=None):return self.request(self.config.url(name),path,payload,timeout=self.config.request_timeout)
    def start_service(self, name, cancel, job, **kwargs):
        self.ensure(cancel);spec=self.config.service(name,options=self.options,**kwargs)
        self.line(job,f'Starting {name}…');self.supervisor.start(name,spec,cancel.is_set)
        if spec.health:self.wait_for(cancel,job,name+' health',lambda:self.get(name,spec.health).get('loaded',False if name=='sam' else True) is True,spec.timeout,name)
        self.line(job,f'{name} ready')

    def workspace_ready(self):
        value=self.workspace(self.config)
        return bool(value['active'] and value['active']['ready'] and not value['jobs'])

    def _setup(self, cancel, job):
        self.ensure(cancel);self.supervisor.stop('relay');self.supervisor.stop('deck');self.supervisor.stop('sam')
        jobs=self.workspace(self.config)['jobs']
        if jobs:self.line(job,'Reattaching the running reconstruction in Deck; finish or cancel it there')
        else:
            self.line(job,'Waiting for GPU lease to be free for reconstruction')
            self.wait_for(cancel,job,'GPU handover',lambda:gpu_lease(self.config.gpu_lock)['free'],self.config.gpu_timeout)
        self.start_service('simulator',cancel,job);self.start_service('deck',cancel,job,live=False)
        self.mode='setup';self.open_url=self.config.url('deck')+'/#setup'
        self.line(job,'Setup ready. Import a slow 20–30 second glasses clip, sample about 1 fps, reconstruct, align, save and activate.')

    def _existing(self, cancel, job):
        self.ensure(cancel)
        if not self.workspace_ready():raise ValueError('No Ready active workspace. Finish/cancel reconstruction, then save and activate a version in Deck.')
        self.line(job,'Existing active workspace is Ready; ingest skipped. Choose Go live when ready.')
        self.mode='existing'

    def _live(self, cancel, job):
        self.ensure(cancel)
        self.line(job,'Waiting for reconstruction to finish or be cancelled')
        def ready():
            value=self.workspace(self.config)
            if value['jobs']:self.line(job,'Reconstruction: '+', '.join(str(v['stage']) for v in value['jobs']));return False
            return True
        self.wait_for(cancel,job,'Reconstruction',ready,self.config.gpu_timeout)
        if not self.workspace_ready():raise ValueError('Save and activate a Ready workspace first, or choose Use the existing workspace')
        self.supervisor.stop('relay');self.supervisor.stop('deck');self.supervisor.stop('sam')
        self.line(job,'Waiting for GPU handover')
        self.wait_for(cancel,job,'GPU lease',lambda:gpu_lease(self.config.gpu_lock)['free'],self.config.gpu_timeout)
        self._check(cancel,job)
        required={'ace','ace-lens'}|({'ugreen','ugreen-lens'} if self.options['second_camera'] else set())
        failed=[c['message'] for c in self.checks if c['id'] in required and c['status']!='pass']
        if not self.details['weights']['hands']:failed.append('Hand Landmarker weights missing')
        if self.options['model'].startswith('spark') and not self.details['key_present']:failed.append('MODEL_API_KEY missing in .env')
        if self.options['sam_people'] and not (self.details['gpu']['available'] and self.details['gpu']['enough'] and self.details['weights']['sam']):failed.append('SAM needs cached weights and at least 2.8 GB free GPU memory; switch SAM people off for the CPU gate')
        if failed:raise ValueError('; '.join(failed))
        self.start_service('simulator',cancel,job)
        if self.options['sam_people']:self.start_service('sam',cancel,job)
        self.line(job,'Hands out of both views · calibration starts now (about 10 seconds)')
        countdown=time.monotonic()+10
        spec=self.config.service('deck',live=True,options=self.options,cameras=self.details['cameras'])
        self.supervisor.start('deck',spec,cancel.is_set)
        def deck_ready():
            remaining=max(0,int(countdown-time.monotonic()+.999))
            with self.lock:job['countdown_s']=remaining
            self.get('deck','/api/state');return True
        self.wait_for(cancel,job,'Camera calibration / Deck startup',deck_ready,spec.timeout,'deck')
        with self.lock:job['countdown_s']=None
        self.line(job,'Checking gate fps and camera freshness')
        def healthy():
            gate=self.get('deck','/api/hand-gate')
            if gate.get('fps',0)<=15 or gate.get('state') not in ('clear','slow','stop'):return False
            age=gate.get('stale_s')
            if age is None or age>.15 or time.time()-gate.get('updated',0)>1.5:return False
            cameras=gate.get('cameras',{})
            return not self.options['second_camera'] or len(cameras)>=2 and all(v.get('age_s',999)<=.15 for v in cameras.values())
        self.wait_for(cancel,job,'Live hand gate (>15 fps; every camera fresh)',healthy,self.config.gate_timeout,'deck')
        if self.options['sam_people'] and self.get('sam','/health').get('loaded') is not True:raise ValueError('SAM worker lost its loaded model; restart Go live')
        self.mode='live';self.open_url=self.config.url('deck')+'/#workspace'
        self.line(job,'Live gate ready. Voxel sources appear once a hand/person is observed; no empty-space clearance is inferred.')

    def _reset(self, cancel, job):
        self.ensure(cancel)
        workspace=self.workspace(self.config)
        preset_warnings=[w for w in workspace['warnings'] if 'preset:' in w]
        if preset_warnings:raise ValueError('; '.join(preset_warnings))
        self.line(job,'Resetting Deck latches and returning the simulated arm to Rest')
        try:self.get('deck','/api/action',{'action':'reset'})
        except (TimeoutError,urllib.error.URLError):self.line(job,'Deck reset is still running; waiting for Rest')
        def at_rest():
            state=self.get('simulator','/api/state')
            p=state.get('scene',{}).get('arm_position',[]);r=state.get('rest_position',[])
            return not state.get('busy') and not state.get('stopped') and len(p)==len(r)==3 and sum((a-b)**2 for a,b in zip(p,r))<.0009
        self.wait_for(cancel,job,'Reset ending at Rest',at_rest,60,'deck')
        reply=self.get('simulator','/api/action',{'action':'scene_preset','positions':self.config.preset})
        if reply.get('job_id'):
            key=reply['job_id'];self.wait_for(cancel,job,'Scene preset',lambda:self.get('simulator','/api/jobs/'+key).get('status')=='completed',15,'simulator')
        self.line(job,'Scene reset; preset objects clear of manual no-go cubes. Arm remains at Rest.')

    def _selfcheck(self, cancel, job):
        self.ensure(cancel)
        self.supervisor.stop('relay');self.supervisor.stop('deck');self.supervisor.stop('sam')
        self.start_service('simulator',cancel,job)
        self.supervisor.stop('selfcheck')
        log=self.config.logs/'selfcheck.log';offset=log.stat().st_size if log.exists() else 0
        spec=self.config.service('selfcheck');self.supervisor.start('selfcheck',spec,cancel.is_set)
        self.wait_for(cancel,job,'Simulator self-check',lambda:not self.supervisor.running('selfcheck'),spec.timeout)
        item=self.supervisor.services['selfcheck']
        if item['process']:item['process'].wait(timeout=2)
        if item['reader']:item['reader'].join(timeout=2)
        text=self.supervisor.tail('selfcheck',15000,offset)
        self.line(job,text)
        if item['process'] and item['process'].returncode!=0 or 'FAIL ' in text or not re.search(r'\b([1-9]\d*)/\1 passed',text):raise ValueError('Self-check failed or did not finish; inspect its PASS/FAIL log')
        self.line(job,'Self-check PASS (simulator only)')
        self.start_service('deck',cancel,job,live=False);self.mode='setup'

    def take_start(self):
        with self.lock:
            self.require_safety()
            if self.take or self.job and self.job['status']=='running':raise ValueError('Finish the current take or stage first')
            if self.mode!='live':raise ValueError('Go live before recording a take')
            folder=self.config.logs/'takes';folder.mkdir(exist_ok=True)
            count=max([0]+[int(match[1]) for p in folder.glob('take-*.json') if (match:=re.fullmatch(r'take-(\d+)(?:\.start)?',p.stem))])+1
            baseline=self.get('deck','/api/replay');frames=baseline.get('frames',[])
            self.take={'number':count,'start':time.time(),'start_utc':datetime.now(timezone.utc).isoformat(),
                       'replay_start_t':frames[-1].get('t',0) if frames else 0}
            (folder/f'take-{count:03d}.start.json').write_text(json.dumps(self.take))

    def take_end(self, interrupted=False):
        with self.lock:
            if not self.take:raise ValueError('No take is recording')
            take=dict(self.take)
        replay=self.get('deck','/api/replay');end=time.time();start=take['replay_start_t']
        # Preserve the Deck replay schema; hosted player reads frames/events directly.
        replay['frames']=[{**f,'t':max(0,f.get('t',0)-start)} for f in replay.get('frames',[]) if f.get('t',0)>=start]
        replay['events']=[{**e,'t':max(0,e.get('t',0)-start)} for e in replay.get('events',[]) if e.get('t',0)>=start]
        replay['take']={**take,'end':end,'end_utc':datetime.now(timezone.utc).isoformat(),'interrupted':interrupted}
        path=self.config.logs/'takes'/f'take-{take["number"]:03d}.json'
        path.write_text(json.dumps(self.supervisor.redactor.clean(replay),allow_nan=False))
        with self.lock:
            self.last_take={'number':take['number'],'file':str(path),'url':f'/takes/{take["number"]}'};self.take=None
        return self.last_take

    def tls_status(self, ip):
        folder=self.config.root/'recordings/glasses-gateway/tls'
        result={'ip':ip,'exists':(folder/'server.crt').exists(),'match':False,'ca_path':str(folder/'ca.crt'),
                'token_path':str(self.config.root/'recordings/glasses-gateway/pairing-token')}
        if result['exists'] and ip:
            try:
                cert=ssl._ssl._test_decode_cert(str(folder/'server.crt'))
                result['match']=('IP Address',ip) in cert.get('subjectAltName',())
            except (OSError,ssl.SSLError):pass
        return result
    def _tls(self, cancel, job):
        network=self.probes.network()
        with self.lock:self.details['network']=network
        ip=network.get('ip');tls=self.tls_status(ip)
        with self.lock:self.details['tls']=tls
        if tls['exists']:
            if not tls['match']:raise ValueError('Certificate exists for a different IP; review it locally. Existing private keys will not be overwritten.')
            self.line(job,f'Certificate already exists for {ip}');return
        self.start_service('tls',cancel,job,ip=ip)
        self.wait_for(cancel,job,'TLS certificate creation',lambda:not self.supervisor.running('tls'),25)
        tls=self.tls_status(ip)
        with self.lock:self.details['tls']=tls
        if not tls['match']:raise ValueError('TLS certificate creation failed; '+self.supervisor.tail('tls'))
        self.line(job,'Certificate ready; install only ca.crt on the iPhone')
    def _relay_start(self, cancel, job):
        if not self.supervisor.running('deck'):raise ValueError('Start Setup or Go live before the glasses relay')
        network=self.probes.network()
        with self.lock:self.details['network']=network
        ip=self.details['network'].get('ip')
        with self.lock:self.details['tls']=self.tls_status(ip)
        if not self.details['tls']['match']:raise ValueError('Create a certificate matching the current Wi-Fi IPv4 first')
        self.start_service('relay',cancel,job,ip=ip)
        self.wait_for(cancel,job,'Glasses relay',lambda:port_owner(self.config.ports['relay']) is not None,10,'relay')
        self.line(job,f'Relay at https://{ip}:{self.config.ports["relay"]}. Pairing token contents are never displayed.')
    def _relay_stop(self, cancel, job):self.supervisor.stop('relay');self.line(job,'Relay stopped')
    def _stop_all(self, cancel, job):
        if self.take:
            try:self.take_end(interrupted=True)
            except (OSError,ValueError):
                self.line(job,'Could not export the interrupted take; its start timestamp is saved');self.take=None
        errors=[]
        self.supervisor.stop('relay')
        # Existing workspace ownership checks verify each worker before signalling its group.
        jobs=self.workspace(self.config)['jobs']
        if any(v.get('id') for v in jobs):
            from lookandpick.workspaces.service import WorkspaceService
            service=WorkspaceService(self.config.workspace)
            for worker in jobs:
                if worker.get('id'):
                    try:
                        self.line(job,'Cancelling reconstruction '+worker['id']);service.cancel(worker['id'])
                    except (OSError,ValueError) as exc:errors.append(str(exc))
        errors+=self.supervisor.stop_all();self.mode='idle'
        lease=gpu_lease(self.config.gpu_lock)
        if not lease['free']:errors.append('GPU lease is still held by '+str(lease.get('owner'))+'; an external worker may still be running')
        for name,port in self.config.ports.items():
            owner=port_owner(port)
            if owner:errors.append(f'{name}: port {port} still held by PID {owner.get("pid") or "unavailable"} (not stopped without verified ownership)')
        if errors:raise ValueError('; '.join(errors))
        self.line(job,'All launcher services stopped; no owned child processes remain; GPU lease free')

    def _monitor(self):
        while not self.closed.wait(1):
            try:
                result={'updated':time.time(),'workspace':self.workspace(self.config)}
                if self.supervisor.running('deck'):
                    state=self.get('deck','/api/state');result['deck']={'busy':state.get('busy'),'backend':state.get('backend')}
                    library=self.get('deck','/api/workspaces')
                    if 'workspaces' in library:
                        current=library.get('active')
                        result['workspace']['active']={'id':current['id'],'version':current['version_id'],'ready':not current.get('needs_rebuild',False)} if current else None
                        result['workspace']['api_stages']=[{'workspace':v['id'],'stage':v['job'].get('stage'),'state':v['job'].get('state')} for v in library['workspaces'] if v.get('job')]
                    try:result['gate']=self.get('deck','/api/hand-gate')
                    except (OSError,ValueError):result['gate']={'state':'not connected','fps':0}
                    events=self.get('deck','/api/timeline');calls=[e for e in events if 'spark' in str(e.get('source','')).lower()]
                    for entry in calls:
                        key=(self.supervisor.services['deck']['identity']['pid'],str(entry.get('id',json.dumps(entry,sort_keys=True))))
                        if key not in self.cost_seen:
                            self.cost_seen.add(key);self.spark_last=float(entry.get('cost_usd',0));self.spark_total+=self.spark_last
                    result['spark']={'last_usd':self.spark_last,'total_usd':round(self.spark_total,7),'scope':'observed Deck timeline calls'}
                    active=result['workspace']['active']
                    if active:
                        voxels=self.get('deck','/api/workspaces/'+active['id']+'/voxels')
                        result['voxels']={k:voxels.get(k) for k in ('counts','sources','fresh')}
                if self.supervisor.running('sam'):result['sam']=self.get('sam','/health')
                with self.lock:self.live=self.supervisor.redactor.clean(result)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                with self.lock:self.live={'updated':time.time(),'error':self.supervisor.redactor.text(exc)}

    def close(self):
        self.closed.set()
        if self.cancel:self.cancel.set()
        with self.operation:
            try:self._stop_all(threading.Event(),{'lines':[]})
            except (OSError,ValueError) as exc:self.error=self.supervisor.redactor.text(exc)
        self.monitor.join(timeout=3)


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def local(self):
            port=self.server.server_port
            allowed={f'127.0.0.1:{port}',f'localhost:{port}'}
            return self.client_address[0]=='127.0.0.1' and self.headers.get('Host') in allowed and (self.headers.get('Origin') is None or self.headers['Origin'] in {'http://'+h for h in allowed})
        def send(self,status,data,kind='application/json'):
            if kind=='application/json':data=json.dumps(app.supervisor.redactor.clean(data),allow_nan=False).encode()
            elif isinstance(data,str):data=data.encode()
            self.send_response(status)
            for k,v in {'Content-Type':kind,'Content-Length':str(len(data)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                        'Content-Security-Policy':"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():self.send_header(k,v)
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):
            if not self.local():return self.send(403,{'error':'Local requests only'})
            if self.path=='/api/state':return self.send(200,app.snapshot())
            routes={'/':('web/index.html','text/html'),'/app.js':('web/app.js','text/javascript'),'/style.css':('web/style.css','text/css')}
            if self.path in routes:
                path,kind=routes[self.path];return self.send(200,(Path(__file__).parent/path).read_bytes(),kind)
            if self.path in ('/docs/demo','/docs/launcher'):
                name='demo-2026-11-04.md' if self.path=='/docs/demo' else 'launcher.md'
                try:return self.send(200,app.supervisor.redactor.text((app.config.root/'docs'/name).read_text()),'text/plain; charset=utf-8')
                except OSError:return self.send(404,{'error':'Document not found'})
            if self.path=='/logs':
                return self.send(200,{'folder':str(app.config.logs),'logs':{name:app.supervisor.tail(name,12000) for name in app.supervisor.ORDER}})
            match=re.fullmatch(r'/takes/([1-9]\d{0,5})',self.path)
            if match:
                try:return self.send(200,json.loads((app.config.logs/'takes'/f'take-{int(match[1]):03d}.json').read_text()))
                except (OSError,ValueError):return self.send(404,{'error':'Take not found'})
            return self.send(404,{'error':'Not found'})
        def do_POST(self):
            self.connection.settimeout(4)
            if not self.local():return self.send(403,{'error':'Local requests only'})
            if self.headers.get('Transfer-Encoding'):return self.send(400,{'error':'Chunked bodies are not accepted'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send(415,{'error':'Expected application/json'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:return self.send(413,{'error':'Request body must be 1–4096 bytes'})
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('Expected a JSON object')
                if self.path=='/api/options':app.configure(body)
                elif self.path=='/api/safety':app.attest(body)
                elif self.path=='/api/action':
                    if set(body)!={'action'}:raise ValueError('Only a fixed action is accepted')
                    return self.send(202,{'job':app.launch(body['action'])})
                elif self.path=='/api/stop-service':
                    if set(body)!={'service'}:raise ValueError('Only a service name is accepted')
                    app.stop_service(body['service'])
                elif self.path=='/api/open-logs':
                    if body:raise ValueError('No path arguments are accepted')
                    app.open_logs()
                elif self.path=='/api/take-start':
                    if body:raise ValueError('No take arguments are accepted')
                    app.take_start()
                elif self.path=='/api/take-end':
                    if body:raise ValueError('No take arguments are accepted')
                    app.take_end()
                else:return self.send(404,{'error':'Not found'})
                return self.send(200,app.snapshot())
            except (ValueError,TypeError,KeyError,OSError) as exc:return self.send(400,{'error':str(exc)})
    return Handler


def make_server(app, port=8760):
    server=ThreadingHTTPServer(('127.0.0.1',port),handler_for(app));server.daemon_threads=True;return server

"""Launcher contracts using disposable loopback services; no devices, GPU or internet."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection

import pytest

from lookandpick.launcher.app import Launcher, make_server, request_json
from lookandpick.launcher.config import Config, Service
from lookandpick.launcher.preflight import Probes, camera_nodes, gpu_lease, workspace_status
from lookandpick.launcher.supervisor import Supervisor, group_members, identity, port_owner

WORKER = r'''
import argparse,fcntl,json,os,signal,subprocess,sys,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
p=argparse.ArgumentParser();p.add_argument('--port',type=int);p.add_argument('--name');p.add_argument('--lease');p.add_argument('--ignore',action='store_true');p.add_argument('--child',action='store_true');p.add_argument('--exit',action='store_true');a=p.parse_args()
if a.exit:print('marker seen in 0 frames',flush=True);sys.exit(3)
if a.ignore:signal.signal(signal.SIGTERM,signal.SIG_IGN)
if a.child:subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)'])
if a.lease:
 lease=open(a.lease,'a+');fcntl.flock(lease,fcntl.LOCK_EX);lease.seek(0);lease.truncate();json.dump({'pid':os.getpid(),'owner':'fake SAM'},lease);lease.flush()
secret=os.environ.get('MODEL_API_KEY','no-key')
sys.stdout.write('x'*4090+secret[:5]);sys.stdout.flush();time.sleep(.02);print(secret[5:],flush=True)
calls=0
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,value):
  raw=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  global calls
  if self.path=='/health':return self.send({'loaded':True,'ok':True})
  if self.path=='/api/hand-gate':return self.send({'state':'clear','fps':30,'updated':time.time(),'stale_s':0,'cameras':{'main':{'age_s':0,'hands':0},'second':{'age_s':0,'hands':0}}})
  if self.path=='/api/timeline':return self.send([{'source':'Muse Spark','cost_usd':.001}])
  if self.path=='/api/replay':
   calls+=1;return self.send({'schema_version':1,'frames':[{'t':i,'scene':{}} for i in range(2+calls)],'events':[{'t':1,'source':'system'},{'t':3,'source':'Arm'}],'limitations':['fake']})
  if self.path.endswith('/voxels'):return self.send({'sources':[{'name':'hand-gate','age_ms':1},{'name':'sam-people-person','age_ms':1},{'name':'sam-people-second-hand','age_ms':1}],'fresh':True,'counts':{}})
  return self.send({'backend':'sim','scene':{'arm_position':[.1,.1,.1]},'rest_position':[.1,.1,.1],'busy':False,'stopped':False})
 def do_POST(self):
  data=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))));self.send({'result':True})
server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
print('READY '+a.name,flush=True);server.serve_forever()
'''


def unused_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


class FakeConfig(Config):
    def __init__(self, root):
        super().__init__(root)
        self.ports={name:unused_port() for name in self.ports}
        self.alternate_deck_ports=()
        self.worker=self.root/'fake-service.py';self.worker.write_text(WORKER)
        self.workspace_python=self.hands_python=self.sim_python=self.sam_python=Path(sys.executable)
        self.shutdown_timeout=.1;self.poll_s=.02;self.gpu_timeout=2;self.gate_timeout=1;self.request_timeout=.5
        self.start_order=[]
    def model_choices(self):return ['fake','spark','spark-open']
    def service(self, name, **kwargs):
        self.start_order.append(name)
        return Service((sys.executable,str(self.worker),'--name',name,'--port',str(self.ports.get(name,unused_port())),
                        *(['--lease',str(self.gpu_lock)] if name=='sam' else [])),self.ports.get(name),'/health' if name=='sam' else '/api/state' if name in ('deck','simulator') else None,3)


class FakeProbes:
    def cameras(self):return {'ace':{'index':22,'size':[640,360]},'ugreen':{'index':8,'size':[1280,720]}}
    def frame(self,camera):return {'ok':True,'marker':True,'thumbnail':None}
    def lens(self,*args):return True
    def gpu(self):return {'free':True,'available':True,'enough':True,'free_mb':8000}
    def ports(self):return {}
    def secrets(self):return True
    def weights(self):return {'hands':True,'sam':True,'people_optional':False}
    def network(self):return {'ip':None,'meta_reachable':True}
    def microphone(self):return True


def fake_workspace(config):
    return {'active':{'id':'ws-test','version':'v0001','ready':True},'manual_count':0,'warnings':[],'jobs':[]}


def until(test, timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if test():return
        time.sleep(.02)
    raise AssertionError('Timed out waiting for fake service')


@pytest.fixture
def app(tmp_path):
    cfg=FakeConfig(tmp_path);cfg.gpu_lock.parent.mkdir(parents=True,exist_ok=True)
    value=Launcher(cfg,probes=FakeProbes(),workspace=fake_workspace)
    yield value
    value.close()


def run(app, action):
    app.launch(action);job=app.job;until(lambda:job['status']!='running',12);return job


def attest(app):app.attest({'arm_unpowered':True,'hands_out':True})


def test_safety_boxes_and_fixed_service_names_block_early(app):
    for action in ('setup','existing','live','reset','selfcheck','tls','relay_start'):
        with pytest.raises(ValueError,match='both manual safety'):app.launch(action)
    with pytest.raises(ValueError):app.launch('arbitrary shell')
    with pytest.raises(ValueError):app.configure({'model':'fake; rm'})
    with pytest.raises(ValueError):app.configure({'command':['anything']})
    assert not app.supervisor.services
    assert run(app,'stop_all')['status']=='completed'


def test_setup_live_handover_waits_for_reconstruction_lock_and_stop_releases_it(app):
    attest(app)
    assert run(app,'setup')['status']=='completed',app.job
    assert app.config.start_order==['simulator','deck']
    events=[];original=app.workspace;busy={'value':True}
    app.workspace=lambda c:{**original(c),'active':{'id':'ws-test','version':'v0002','ready':True},'jobs':[{'stage':'appearance'}] if busy['value'] else []}
    with app.config.gpu_lock.open('a+') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX)
        app.launch('live');time.sleep(.1)
        assert 'sam' not in app.config.start_order
        busy['value']=False;time.sleep(.1)
        assert 'sam' not in app.config.start_order
        fcntl.flock(lease,fcntl.LOCK_UN)
    until(lambda:app.job['status']!='running',8)
    assert app.job['status']=='completed',app.job
    assert app.config.start_order==['simulator','deck','simulator','sam','deck']
    assert not gpu_lease(app.config.gpu_lock)['free']
    assert app.mode=='live'
    until(lambda:app.live.get('voxels',{}).get('sam_people_seen'))
    assert app.live['voxels']['hand_gate_seen']
    assert run(app,'stop_all')['status']=='completed'
    assert gpu_lease(app.config.gpu_lock)['free']
    assert all(port_owner(p) is None for p in app.config.ports.values())


def test_gate_unhealthy_times_out_plainly_and_stop_all_cancels_start(app):
    attest(app)
    original=app.request
    def request(base,path,payload=None,timeout=2):
        data=original(base,path,payload,timeout)
        if path=='/api/hand-gate':data['cameras']['second']['age_s']=10
        return data
    app.request=request
    job=run(app,'live')
    assert job['status']=='failed' and 'every camera fresh' in job['error'] and 'timed out' in job['error']
    app.workspace=lambda c:{**fake_workspace(c),'jobs':[{'stage':'geometry'}]}
    app.launch('live');time.sleep(.1)
    assert run(app,'stop_all')['status']=='completed'
    assert not app.supervisor.services


def test_process_exit_port_conflict_and_term_resistant_child_are_handled(app,monkeypatch):
    supervisor=app.supervisor
    spec=Service((sys.executable,str(app.config.worker),'--name','dead','--exit'),timeout=.3)
    supervisor.start('deck',spec)
    until(lambda:not supervisor.running('deck'))
    attest(app)
    with pytest.raises(ValueError,match='process exited'):
        app.wait_for(threading.Event(),{'lines':[]},'Deck startup',lambda:False,.1,'deck')
    supervisor.stop('deck')
    spec=Service((sys.executable,str(app.config.worker),'--name','stubborn','--ignore','--child','--port',str(app.config.ports['deck'])),app.config.ports['deck'])
    supervisor.start('deck',spec)
    until(lambda:port_owner(spec.port) is not None)
    pid=supervisor.services['deck']['identity']['pid'];until(lambda:len(group_members(pid))>=2)
    with pytest.raises(ValueError,match=f'PID {pid}'):
        supervisor.start('simulator',Service((sys.executable,'-c','pass'),spec.port))
    supervisor.stop_all();assert group_members(pid)==[]


def test_secrets_are_scrubbed_from_logs_responses_and_rotated_values(app,monkeypatch):
    key='FAKE_PRIVATE_KEY_123456789';token='FAKE_PAIRING_TOKEN_987654321'
    monkeypatch.setenv('MODEL_API_KEY',key)
    folder=app.config.root/'recordings/glasses-gateway';folder.mkdir(parents=True);(folder/'pairing-token').write_text(token)
    (app.config.root/'.env').write_text("MODEL_API_KEY='"+key+"' # comment\n")
    attest(app);assert run(app,'setup')['status']=='completed',app.job
    until(lambda:'[REDACTED]' in app.supervisor.tail('deck'))
    app.line(app.job,key+' '+token)
    raw=json.dumps(app.snapshot())
    assert key not in raw and token not in raw
    monkeypatch.setenv('MODEL_API_KEY','rotated_key')
    assert key not in app.supervisor.redactor.text(key)
    run(app,'stop_all')
    for log in app.config.logs.glob('*.log'):assert key not in log.read_text() and token not in log.read_text()


def test_http_loopback_origin_body_bounds_and_no_arbitrary_arguments(app):
    server=make_server(app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def call(method,path,body=None,headers=None):
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=2)
        raw=json.dumps(body) if body is not None else None
        conn.request(method,path,raw,{'Content-Type':'application/json',**(headers or {})})
        reply=conn.getresponse();raw=reply.read();data=json.loads(raw) if 'application/json' in reply.getheader('Content-Type','') else raw.decode();conn.close();return reply.status,data
    try:
        assert call('GET','/api/state')[0]==200
        assert 'type="module"' in call('GET','/')[1]
        assert 'export function confirmation' in call('GET','/confirmation.js')[1]
        assert call('POST','/api/real-arm/connect',{})[0]==404
        assert call('POST','/api/real-arm/disconnect',{})[0]==404
        assert call('GET','/api/state',headers={'Host':'evil.example'})[0]==403
        assert call('POST','/api/action',{'action':'stop_all'},headers={'Origin':'https://evil.example'})[0]==403
        assert call('POST','/api/action',{'action':'setup','command':'nope'})[0]==400
        assert call('POST','/api/action',{'action':'setup'})[0]==400
        assert call('POST','/api/options',{'path':'/tmp/nope'})[0]==400
        assert call('POST','/api/action',{'action':'x'*5000})[0]==413
        assert call('GET','/takes/../../.env')[0]==404
    finally:server.shutdown();server.server_close();thread.join(2)


def test_take_export_is_a_trimmed_deck_replay_and_blocks_selfcheck(app):
    attest(app);assert run(app,'live')['status']=='completed'
    app.take_start()
    with pytest.raises(ValueError,match='End the current take'):app.launch('selfcheck')
    take=app.take_end();data=json.loads(Path(take['file']).read_text())
    assert data['schema_version']==1 and data['take']['start_utc'] and data['take']['end_utc']
    assert data['frames'][0]['t']==0 and len(data['frames'])==2
    assert all(event['t']>=0 for event in data['events'])
    app.take_start();assert app.take['number']==2
    app.take_end()


def test_camera_name_resolution_ignores_integrated_and_metadata_nodes(tmp_path):
    for index,name,capture in [(0,'Integrated Camera',0),(22,'Insta360 Ace Pro',0),(23,'Insta360 Ace Pro',1),(8,'UGREEN Camera',0)]:
        folder=tmp_path/f'video{index}';folder.mkdir();(folder/'name').write_text(name);(folder/'index').write_text(str(capture))
    result=camera_nodes(tmp_path)
    assert result['ace']['index']==22 and result['ugreen']['index']==8 and len(result)==2


def test_gpu_old_metadata_is_not_a_held_lease(tmp_path):
    path=tmp_path/'gpu.lock';path.write_text('{"pid":999999,"owner":"old worker"}')
    assert gpu_lease(path)['free']
    with path.open('a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        assert not gpu_lease(path)['free']


def test_zone_warning_uses_real_grid_and_manual_zone_guard(tmp_path):
    from lookandpick.workspaces.service import WorkspaceService
    from lookandpick.workspaces.voxels import Grid
    from lookandpick.workspaces.schema import IDENTITY,settings
    cfg=FakeConfig(tmp_path);service=WorkspaceService(cfg.workspace);repo=service.repo;value=repo.create('launcher test')
    area={'enabled':True,'centre':[0.,0.],'width':.6,'depth':.6,'height':.4,'base':0.,'yaw':0.,'cell_size':.05,'ttl_ms':500,'hold_ms':750,'margin':0.}
    surface={'name':'table','centre':[0.,0.,0.],'width':1.,'depth':.6,'yaw':0.,'height':0.,'enabled':True}
    value['draft']={'schema_version':1,'id':value['id'],'name':value['name'],'assets':{},'surfaces':[surface],'fitted_surfaces':[surface],
      'frames':{'canonical_from_map':IDENTITY,'workspace_from_map':IDENTITY},'calibration':{'meters_per_map_unit':1.,'scale_status':'measured','robot_alignment_status':'approximate'},
      'robot':{'translation':[0.,0.,0.],'rotation':[0.,0.,0.],'scale':1.,'joint_positions':{}},'layers':dict.fromkeys(['background','surfaces','robot','observed'],True),
      'needs_rebuild':False,'settings':settings(),'units':'metres','working_area':area,
      'marker_alignment':{'canonical_from_marker':IDENTITY,'canonical_units_per_metre':1.,'status':'test'}}
    repo.write(value);key=value['id'];snap=service.areas.snapshot(key);indices=list(Grid(area).cells_at([*cfg.preset['obj1'],.018],.01))
    service.areas.edit(key,{'revision':snap['revision'],'policy_revision':snap['policy_revision'],'indices':indices,'blocked':True})
    saved=repo.save(key);repo.activate(key,saved['version_id'])
    result=workspace_status(cfg)
    assert result['manual_count']==len(indices)
    assert any('obj1 preset:' in warning and 'no-go zone' in warning for warning in result['warnings'])


def test_reset_keeps_default_positions_and_warns_about_blocked_presets(app):
    attest(app);assert run(app,'live')['status']=='completed'
    requests=[];original=app.request
    def record(base,path,payload=None,timeout=2):
        if payload:requests.append((base,path,payload))
        return original(base,path,payload,timeout)
    app.request=record
    assert run(app,'reset')['status']=='completed'
    assert [v[2]['action'] for v in requests]==['reset']
    assert app.config.preset=={'obj1':[.23,.10],'obj2':[.22,-.13]}
    app.workspace=lambda c:{**fake_workspace(c),'warnings':['obj1 preset: the path crosses a no-go zone']}
    requests.clear();job=run(app,'reset')
    assert job['status']=='completed' and any('no-go zone' in line for line in job['lines'])
    assert [v[2]['action'] for v in requests]==['reset']


def test_default_commands_are_fixed_sim_only_and_use_name_resolved_camera_ids(tmp_path):
    cfg=Config(tmp_path);options={'model':'spark','second_camera':True,'sam_people':True}
    live=cfg.service('deck',live=True,options=options,cameras=FakeProbes().cameras())
    assert live.argv[live.argv.index('--backend')+1]=='sim'
    assert live.argv[live.argv.index('--hand-gate')+1]=='22'
    assert live.argv[live.argv.index('--second-camera')+1]=='8'
    assert '--sam-people' in live.argv
    setup=cfg.service('deck',live=False,options=options)
    assert '--hand-gate' not in setup.argv and '--sam-people' not in setup.argv
    assert live.argv[0]==str(cfg.hands_python) and setup.argv[0]==str(cfg.workspace_python)
    with pytest.raises(ValueError):cfg.service('shell')


def test_setup_reattaches_a_running_reconstruction_instead_of_waiting_it_out(app):
    attest(app)
    app.workspace=lambda c:{**fake_workspace(c),'jobs':[{'stage':'appearance'}]}
    with app.config.gpu_lock.open('a+') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX)
        assert run(app,'setup')['status']=='completed',app.job
        assert 'sam' not in app.config.start_order
        assert 'Reattaching' in '\n'.join(app.job['lines'])


def test_restarted_supervisor_reattaches_only_matching_process_identity(app):
    spec=app.config.service('simulator');app.supervisor.start('simulator',spec)
    until(lambda:port_owner(spec.port) is not None)
    old=app.supervisor.services['simulator'];pid=old['identity']['pid']
    other=Supervisor(app.config);assert other.running('simulator')
    other.stop_all();assert group_members(pid)==[]
    # Original Popen still owns the wait/reaping obligation in this test process.
    app.supervisor.stop_all()


def test_camera_probe_sets_working_mode_reports_marker_and_always_closes(monkeypatch):
    import cv2
    import numpy as np
    from lookandpick.launcher.preflight import camera_probe
    frame=np.full((360,640,3),255,dtype=np.uint8)
    marker=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),0,120)
    frame[100:220,200:320]=cv2.cvtColor(marker,cv2.COLOR_GRAY2BGR)
    class Capture:
        def __init__(self):self.settings=[];self.closed=False
        def set(self,*values):self.settings.append(values)
        def read(self):return True,frame
        def release(self):self.closed=True
    cap=Capture();monkeypatch.setattr(cv2,'VideoCapture',lambda *args:cap)
    result=camera_probe(22,640,360)
    assert result['ok'] and result['marker'] and result['thumbnail'].startswith('data:image/jpeg;base64,')
    assert (cv2.CAP_PROP_FRAME_WIDTH,640) in cap.settings and (cv2.CAP_PROP_FRAME_HEIGHT,360) in cap.settings and cap.closed
    cap.read=lambda:(False,None)
    assert not camera_probe(22,640,360)['ok'] and cap.closed


def test_existing_tls_certificate_is_never_overwritten_and_ip_mismatch_is_flagged(app,monkeypatch):
    import ssl
    attest(app)
    folder=app.config.root/'recordings/glasses-gateway/tls';folder.mkdir(parents=True)
    cert=folder/'server.crt';cert.write_text('existing certificate')
    app.probes.network=lambda:{'ip':'192.168.1.22','meta_reachable':True}
    monkeypatch.setattr(ssl._ssl,'_test_decode_cert',lambda path:{'subjectAltName':[('IP Address','192.168.1.21')]})
    job=run(app,'tls');assert job['status']=='failed' and 'different IP' in job['error']
    assert not app.details['tls']['match'] and not app.config.start_order and cert.read_text()=='existing certificate'
    monkeypatch.setattr(ssl._ssl,'_test_decode_cert',lambda path:{'subjectAltName':[('IP Address','192.168.1.22')]})
    assert run(app,'tls')['status']=='completed' and not app.config.start_order


def test_selfcheck_requires_stopped_live_deck_reads_only_this_run_and_restarts_setup(app):
    attest(app);assert run(app,'live')['status']=='completed'
    assert run(app,'selfcheck')['status']=='failed'
    app.stop_service('deck')
    log=app.config.logs/'selfcheck.log';log.write_text('FAIL old run\n0/1 passed\n')
    service=app.config.service
    def spec(name,**kwargs):
        if name=='selfcheck':return Service((sys.executable,'-c','print("PASS fake scenario\\n2/2 passed",flush=True)'),timeout=1)
        return service(name,**kwargs)
    app.config.service=spec
    result=run(app,'selfcheck')
    assert result['status']=='completed',result
    assert app.mode=='setup' and app.supervisor.running('deck') and not app.supervisor.running('sam')
    assert not any('old run' in line for line in result['lines'])


def test_confirmed_no_go_clear_reuses_revision_checked_voxel_endpoint(app):
    attest(app);assert run(app,'setup')['status']=='completed'
    original=app.request;writes=[];active={'id':'ws-test','version_id':'v0001'}
    observed=['occupied','unknown','unknown'];snap={'revision':'geometry-live','policy_revision':7,'manual_blocked':[1,2],'observed_states':observed[:]}
    def request(base,path,payload=None,timeout=2):
        if path=='/api/workspaces':return {'active':dict(active),'workspaces':[]}
        if path.endswith('/voxels'):return dict(snap)
        if path.endswith('/voxel-edits'):
            writes.append((path,payload));return {**snap,'manual_blocked':[]}
        return original(base,path,payload,timeout)
    app.request=request
    app.checks=[{'id':'workspace','status':'pass','message':'2 manual no-go cubes'}]
    body={'confirmed':False,'workspace_id':'ws-test','version_id':'v0001'}
    with pytest.raises(ValueError,match='Confirm'):app.clear_no_go(body)
    assert not writes
    app.clear_no_go({**body,'confirmed':True})
    assert writes==[('/api/workspaces/ws-test/versions/v0001/voxel-edits',{'revision':'geometry-live','policy_revision':7,'indices':[1,2],'blocked':False})]
    assert snap['observed_states']==observed and 'Cleared 2' in app.manual_reset
    assert '0 manual no-go cubes' in app.checks[0]['message']
    writes.clear()
    def changed(base,path,payload=None,timeout=2):
        result=request(base,path,payload,timeout)
        if path.endswith('/voxels'):active['version_id']='v0002'
        return result
    app.request=changed
    with pytest.raises(ValueError,match='changed'):app.clear_no_go({**body,'confirmed':True})
    assert not writes


def test_reconstruction_is_cancelled_through_deck_before_it_is_stopped(app):
    attest(app);assert run(app,'setup')['status']=='completed'
    busy={'value':True};app.workspace=lambda c:{**fake_workspace(c),'active':{'id':'ws-test','version':'v0002','ready':True},'jobs':[{'id':'run-test','stage':'appearance'}] if busy['value'] else []}
    original=app.request;cancelled=[]
    with app.config.gpu_lock.open('a+') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX)
        def request(base,path,payload=None,timeout=2):
            if path=='/api/workspace-jobs/run-test/cancel':
                assert app.supervisor.running('deck')
                cancelled.append(path);busy['value']=False;fcntl.flock(lease,fcntl.LOCK_UN);return {'state':'cancelled'}
            return original(base,path,payload,timeout)
        app.request=request
        job=run(app,'live');assert job['status']=='completed',job
    assert cancelled==['/api/workspace-jobs/run-test/cancel'] and 'sam' in app.config.start_order


def test_adopted_process_requires_confirmation_and_stops_pid_only(app):
    foreign=Supervisor(app.config)
    port=app.config.ports['simulator']
    spec=Service((sys.executable,str(app.config.worker),'--name','external','--child','--port',str(port)),port)
    foreign.start('simulator',spec);pid=foreign.services['simulator']['identity']['pid']
    try:
        until(lambda:len(group_members(pid))>=2 and port_owner(port) is not None)
        app.supervisor.adopt('simulator',identity(pid),port)
        assert app.supervisor.status()['simulator']['external']
        with pytest.raises(ValueError,match='confirm'):app.supervisor.stop('simulator')
        with pytest.raises(ValueError,match='Confirm'):app.launch('stop_all')
        app.supervisor.stop('simulator',external_confirmed=True)
        assert identity(pid) is None and group_members(pid)  # Its child was NOT group-killed.
    finally:foreign.stop_all()
    assert group_members(pid)==[]


def test_health_checked_existing_deck_is_adopted_and_restart_requires_confirmation(app,monkeypatch):
    foreign=Supervisor(app.config);spec=app.config.service('deck');foreign.start('deck',spec)
    pid=foreign.services['deck']['identity']['pid']
    try:
        until(lambda:port_owner(spec.port) is not None)
        monkeypatch.setattr(app,'_external_allowed',lambda name,owner:name=='deck' and owner['pid']==pid)
        app.probes.ports=lambda:{'deck':identity(pid)}
        assert run(app,'check')['status']=='completed'
        assert app.supervisor.services['deck']['external']
        attest(app)
        with pytest.raises(ValueError,match='Confirm'):app.launch('setup')
        with pytest.raises(ValueError,match='confirm'):app.stop_service('deck')
        app.stop_service('deck',confirmed=True);assert identity(pid) is None
    finally:foreign.stop_all()


def test_setup_is_not_ready_until_a_new_saved_version_is_activated(app):
    attest(app);assert run(app,'setup')['status']=='completed'
    assert not app.workspace_ready() and not app.setup_ready
    app.workspace=lambda c:{**fake_workspace(c),'active':{'id':'ws-test','version':'v0002','ready':True}}
    until(lambda:app.setup_ready)
    assert app.workspace_ready()


def test_single_camera_gate_still_requires_camera_observations(app):
    attest(app);app.configure({'second_camera':False})
    original=app.request
    def request(base,path,payload=None,timeout=2):
        result=original(base,path,payload,timeout)
        if path=='/api/hand-gate':result.pop('cameras',None);result['simulated']=True
        return result
    app.request=request
    job=run(app,'live')
    assert job['status']=='failed' and 'hand gate' in job['error']

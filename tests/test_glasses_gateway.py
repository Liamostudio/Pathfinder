"""Real loopback HTTP/TLS with fake models. No microphone, Meta account or motors."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest
from lookandpick.adapters.fake import FakeTracker, SimArm
from lookandpick.deck_runtime import RuntimeSession, ScriptedModel
from lookandpick.glasses_gateway import deck_base, load_token, make_server
from lookandpick.mission_control import MissionControl, make_handler
from lookandpick.models.calllog import CallLog
from lookandpick.runtime import Runtime
from scripts.create_glasses_tls import create

TOKEN='test-pairing-token-not-a-model-key'

@contextmanager
def serving(server):
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:yield server
    finally:server.shutdown();server.server_close();worker.join(2)

def request(base,path,body=None,*,token=TOKEN,headers=None,context=None):
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',**(headers or {})}
    data=None if body is None else json.dumps(body).encode()
    value=urllib.request.Request(base+path,data=data,headers=headers)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(value,timeout=2) as reply:return reply.status,json.load(reply)
    except urllib.error.HTTPError as exc:return exc.code,json.load(exc)

@pytest.fixture
def linked():
    log=CallLog();session=RuntimeSession(Runtime(ScriptedModel(log),arm=SimArm(),tracker=FakeTracker(),log=log,say=lambda _:None))
    try:
        with serving(ThreadingHTTPServer(('127.0.0.1',0),make_handler(session,workspace_service=object()))) as deck:
            upstream=f'http://127.0.0.1:{deck.server_port}'
            with serving(make_server('127.0.0.1',0,upstream,TOKEN)) as relay:
                yield session,f'http://127.0.0.1:{relay.server_port}',upstream
    finally:session.close()

def wait(base,key,status):
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        code,job=request(base,'/v1/jobs/'+key)
        if code==200 and job['status']==status:return job
        time.sleep(.005)
    raise AssertionError(job)

def test_phone_review_executes_through_deck_queue_and_stop_is_direct(linked):
    session,base,_=linked
    assert request(base,'/v1/status')[1]['voice_mode']=='scripted'
    code,job=request(base,'/v1/input',{'request_id':str(uuid.uuid4()),'text':'pick up the red cube'})
    assert code==202
    key=job['job_id'];wait(base,key,'awaiting_confirmation')
    assert not session.arm.log and not session.runtime.model.requests
    assert request(base,'/v1/confirm',{'job_id':key})[0]==202
    assert wait(base,key,'completed')['result']['steps'][0]['result']=='picked red cube'
    calls=len(session.runtime.model.requests)
    assert request(base,'/v1/confirm',{'job_id':key})[0]==202
    assert len(session.runtime.model.requests)==calls
    assert request(base,'/v1/stop',{})==(200,{'status':'stopped'})
    assert session.safety.stopped
    assert request(base,'/v1/reset',{})[0]==404

def test_relay_auth_and_fixed_surface(linked):
    _,base,_=linked
    assert request(base,'/v1/status',token='wrong')[0]==401
    assert request(base,'/v1/status',headers={'Origin':'https://other.example'})[0]==401
    assert request(base,'/v1/status',headers={'Host':'other.example'})[0]==401
    assert request(base,'/v1/status?token=anything')[0]==400
    assert request(base,'/api/action',{'action':'pick'})[0]==404
    assert request(base,'/v1/input',{'audio_wav':'broken','request_id':str(uuid.uuid4())})[0]==400
    assert request(base,'/v1/stop',{'ignored':'x'*5000})[0]==413
    assert request(base,'/v1/input',{},headers={'Content-Type':'text/plain'})[0]==415

def test_legacy_deck_does_not_pretend_to_support_models():
    with serving(ThreadingHTTPServer(('127.0.0.1',0),make_handler(MissionControl(),workspace_service=object()))) as deck:
        assert request(f'http://127.0.0.1:{deck.server_port}','/api/glasses/status')[0]==409

def test_phone_gateway_requires_tls_and_token_is_private(tmp_path):
    with pytest.raises(ValueError,match='TLS'):make_server('0.0.0.0',0,'http://127.0.0.1:8769',TOKEN)
    for base in ('https://127.0.0.1:8769','http://example.com','http://127.0.0.1:8769/api','http://user@localhost'):
        with pytest.raises(ValueError):deck_base(base)
    path=tmp_path/'pairing-token';token=load_token(path)
    assert token==load_token(path) and path.stat().st_mode&0o077==0
    path.chmod(0o644)
    with pytest.raises(ValueError,match='600'):load_token(path)

def test_https_works_with_normal_certificate_trust_and_rejects_untrusted_ca(linked,tmp_path):
    _,_,upstream=linked
    tls=create('127.0.0.1',tmp_path/'tls')
    with serving(make_server('127.0.0.1',0,upstream,TOKEN,cert=tls/'server.crt',key=tls/'server.key')) as server:
        base=f'https://127.0.0.1:{server.server_port}'
        trusted=ssl.create_default_context(cafile=str(tls/'ca.crt'))
        assert request(base,'/v1/status',context=trusted)[1]['simulator_only'] is True
        with pytest.raises(urllib.error.URLError):request(base,'/v1/status')
    with pytest.raises(FileExistsError):create('127.0.0.1',tls)

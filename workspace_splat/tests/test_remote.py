import io
import json
from pathlib import Path
import urllib.error
import urllib.request
from unittest.mock import Mock
import numpy as np
from PIL import Image
import pytest
from workspace_splat.formats import export
from workspace_splat.ingest import ingest
from workspace_splat.remote import FalClient,RemoteError,QUEUE,asset_url,queue_url,read_gaussian_ply,generate
from workspace_splat.server import Library

KEY='fake-fal-key-only-for-unit-tests'
BASE='https://queue.fal.run/fal-ai/sam-3/requests/test-request'

@pytest.fixture
def ply(tmp_path):
    rng=np.random.default_rng(3);n=32
    export(tmp_path/'gaussians',rng.normal(size=(n,3)),np.full((n,3),.03),np.tile([1,0,0,0],(n,1)),rng.uniform(0,1,(n,3)),np.full(n,.6))
    return (tmp_path/'gaussians/scene.ply').read_bytes()

class Response(io.BytesIO):
    def __init__(self,value):
        super().__init__(value if isinstance(value,bytes) else json.dumps(value).encode())
        self.headers={}

class Opener:
    def __init__(self,values):self.values=list(values);self.requests=[]
    def open(self,request,timeout):
        self.requests.append(request)
        value=self.values.pop(0)
        if isinstance(value,Exception):raise value
        return Response(value)


def test_documented_queue_to_ply_to_viewer_without_external_network(tmp_path,ply):
    image=tmp_path/'input.jpg';Image.new('RGB',(320,240),'green').save(image)
    capture=tmp_path/'capture';ingest([image],capture,min_images=1)
    opener=Opener([{'request_id':'test-request','status_url':BASE+'/status','response_url':BASE,'cancel_url':BASE+'/cancel'},
                   {'status':'IN_PROGRESS'},{'status':'COMPLETED'},
                   {'gaussian_splat':{'url':'https://v3.fal.media/result.ply'}},ply])
    client=FalClient(KEY,opener=opener,sleep=lambda _:None)
    events=[];progress_details=[]
    def progress(stage,info):events.append(stage);progress_details.append(info)
    scene=generate(capture,KEY,image_name='input.jpg',prompt='robot arm',client=client,progress=progress)
    assert scene['generated'] is True and scene['trained'] is False
    assert scene['model']=='Meta SAM 3D Objects' and scene['provider']=='fal.ai'
    assert scene['count']==32 and scene['metric'] is False
    assert scene['cameras'][0]['kind']=='inspection'
    assert (capture/'output/scene.splat').stat().st_size==32*32
    sent=json.loads(opener.requests[0].data)
    assert sent['prompt']=='robot arm' and sent['enable_safety_checker'] is True
    assert sent['image_url'].startswith('data:image/jpeg;base64,')
    assert opener.requests[0].full_url==QUEUE
    assert opener.requests[0].get_header('Authorization')=='Key '+KEY
    assert opener.requests[-1].get_header('Authorization') is None
    assert 'remote_inference' in events and events[-1]=='complete'
    assert any(info.get('request_id')=='test-request' for info in progress_details)
    assert all(KEY.encode() not in p.read_bytes() for p in capture.rglob('*') if p.is_file())


@pytest.mark.parametrize('url',['http://127.0.0.1/private','https://evil.example/result','https://fal.media.evil.example/file','https://key@fal.media/file'])
def test_artifact_credential_and_ssrf_boundaries(url):
    with pytest.raises(RemoteError):asset_url(url)


def test_queue_url_boundary():
    with pytest.raises(RemoteError):queue_url('https://evil.example/requests/id/status')
    with pytest.raises(RemoteError):queue_url('https://queue.fal.run/other-model/requests/id')
    assert queue_url(BASE+'/status')==BASE+'/status'


def test_auth_failures_do_not_echo_secrets_or_provider_body(tmp_path):
    error=urllib.error.HTTPError(QUEUE,401,'key '+KEY,{},io.BytesIO(KEY.encode()))
    client=FalClient(KEY,opener=Opener([error]))
    with pytest.raises(RemoteError) as exc:client.json(QUEUE,{})
    assert 'HTTP 401' in str(exc.value) and KEY not in str(exc.value)


def test_cancelled_job_requests_provider_cancellation(tmp_path):
    image=tmp_path/'image.jpg';image.write_bytes(b'test')
    opener=Opener([{'request_id':'test-request','status_url':BASE+'/status','response_url':BASE,'cancel_url':BASE+'/cancel'},
                   {'status':'IN_PROGRESS'},{'status':'OK'}])
    def interrupted(_):raise KeyboardInterrupt()
    client=FalClient(KEY,opener=opener,sleep=interrupted)
    with pytest.raises(KeyboardInterrupt):client.generate(image,'table',lambda *_:None)
    assert opener.requests[-1].get_method()=='PUT'
    assert opener.requests[-1].full_url==BASE+'/cancel'


def test_not_a_gaussian_splat_rejected():
    fake=b'ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nend_header\n1\n'
    with pytest.raises(RemoteError):read_gaussian_ply(fake)


def test_key_is_memory_only_and_explicit_external_consent_is_required(tmp_path,monkeypatch):
    library=Library(tmp_path/'data');key=library.new();library.upload(key,'photo.jpg',b'jpeg')
    library.set_key('sam3d-fal',KEY)
    assert KEY not in json.dumps(library.providers())
    with pytest.raises(ValueError,match='Confirm'):library.launch(key,backend='sam3d-fal')
    class Pipe(io.BytesIO):
        def close(self):pass
    process=Mock();process.pid=1234;process.stdin=Pipe();process.poll.return_value=None
    popen=Mock(return_value=process);monkeypatch.setattr('workspace_splat.server.subprocess.Popen',popen)
    library.launch(key,backend='sam3d-fal',allow_external_upload=True,image='photo.jpg')
    assert KEY not in repr(popen.call_args)
    credentials=json.loads(process.stdin.getvalue())
    assert credentials['api_key']==KEY
    assert all(KEY.encode() not in p.read_bytes() for p in library.data.rglob('*') if p.is_file())
    library.set_key('sam3d-fal','')
    assert not library.providers()['sam3d-fal']['configured']


def test_remote_result_does_not_replace_existing_local_scene(tmp_path,monkeypatch):
    library=Library(tmp_path/'data');key=library.new();capture=library.capture(key)
    image=tmp_path/'photo.jpg';Image.new('RGB',(320,240),'green').save(image)
    # The local product is immutable; SAM 3D gets a separate capture.
    ingest([image],capture/'staged',min_images=1)
    import shutil
    shutil.move(str(capture/'staged/images'),capture/'images')
    shutil.move(str(capture/'staged/capture.json'),capture/'capture.json')
    (capture/'output').mkdir();(capture/'output/scene.json').write_text('{"trained":true}')
    library.set_key('sam3d-fal',KEY)
    class Pipe(io.BytesIO):
        def close(self):pass
    process=Mock();process.pid=1234;process.stdin=Pipe();process.poll.return_value=None
    monkeypatch.setattr('workspace_splat.server.subprocess.Popen',Mock(return_value=process))
    result=library.launch(key,backend='sam3d-fal',image='00000.jpg',allow_external_upload=True)
    assert result['id']!=key
    assert (capture/'output/scene.json').read_text()=='{"trained":true}'
    assert (library.capture(result['id'])/'images/00000.jpg').is_file()

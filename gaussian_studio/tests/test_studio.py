import io
import json
import zipfile
from pathlib import Path
from unittest.mock import Mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
import threading
import numpy as np
from PIL import Image
import pytest
from workspace_splat.custom import CustomClient,generate,validate_endpoint
from workspace_splat.remote import RemoteError
from workspace_splat.formats import export
from workspace_splat.ingest import ingest
from workspace_splat.server import Library,handler_for

ENDPOINT='https://provider.example/v1/splats'
CONFIG={'api_key':'fake-provider-key-test-only','endpoint':ENDPOINT,'model':'test-model'}

class Response(io.BytesIO):
    def __init__(self,value):
        self.headers={'Content-Type':'application/octet-stream' if isinstance(value,bytes) else 'application/json'}
        super().__init__(value if isinstance(value,bytes) else json.dumps(value).encode())

class Opener:
    def __init__(self,values):self.values=list(values);self.requests=[];self.archive=None
    def open(self,request,timeout):
        self.requests.append(request)
        if hasattr(request.data,'read'):self.archive=request.data.read()
        value=self.values.pop(0)
        if isinstance(value,BaseException):raise value
        return Response(value)

@pytest.fixture
def capture(tmp_path):
    source=tmp_path/'photo.png';Image.new('RGB',(256,256),'green').save(source)
    target=tmp_path/'capture';ingest([source],target,min_images=1)
    return target

@pytest.fixture
def ply(tmp_path):
    rng=np.random.default_rng(22);n=32
    export(tmp_path/'product',rng.normal(size=(n,3)),np.full((n,3),.03),np.tile([1,0,0,0],(n,1)),rng.uniform(0,1,(n,3)),np.full(n,.8))
    return (tmp_path/'product/scene.ply').read_bytes()

def test_all_stills_are_ingested_past_old_160_limit(tmp_path):
    source=tmp_path/'source';source.mkdir()
    for i in range(163):Image.new('RGB',(32,32),(i,255-i,20)).save(source/f'{i:04d}.png')
    result=ingest([source],tmp_path/'capture',all_images=True,max_frames=2)
    assert len(result['images'])==163
    assert not any('capped' in warning for warning in result['warnings'])

def test_unlimited_frames_and_invalid_limits(tmp_path):
    source=tmp_path/'source';source.mkdir()
    for i in range(3):Image.new('RGB',(32,32),(i*40,255,20)).save(source/f'{i}.png')
    assert len(ingest([source],tmp_path/'all',max_frames=0)['images'])==3
    with pytest.raises(ValueError):ingest([source],tmp_path/'invalid',max_frames=-1)

def test_streaming_upload_is_bounded_and_incomplete_files_are_removed(tmp_path):
    library=Library(tmp_path,upload_budget=None,upload_limit=4*1024**2);key=library.new()
    class Stream(io.BytesIO):
        def read(self,n):assert n<=1024**2;return super().read(n)
    content=b'x'*(2*1024**2+12)
    assert library.upload_stream(key,'video.mp4',Stream(content),len(content))['bytes']==len(content)
    with pytest.raises(ValueError,match='Incomplete'):
        library.upload_stream(key,'bad.mp4',Stream(b'few'),200)
    assert not (library.capture(key)/'uploads/bad.mp4').exists()
    assert not list(library.capture(key).rglob('*.upload'))

def test_custom_key_configuration_is_memory_only_and_can_be_cleared(tmp_path):
    library=Library(tmp_path)
    library.set_key('custom-http',CONFIG['api_key'],endpoint=ENDPOINT,model='test-model')
    assert library.providers()['custom-http']['configured']
    assert CONFIG['api_key'] not in json.dumps(library.providers())
    assert not list(tmp_path.iterdir())
    library.set_key('custom-http','')
    assert not library.providers()['custom-http']['configured']

@pytest.mark.parametrize('url',['http://provider.example/splats','https://localhost/splats','https://127.0.0.1/','https://10.0.0.1/','https://[::1]/','https://user:secret@provider.example/','https://provider.example:8080/'])
def test_custom_endpoint_rejects_local_insecure_and_credential_urls(url):
    with pytest.raises(ValueError):validate_endpoint(url)

def test_direct_provider_result_round_trips_to_real_splat(capture,ply):
    opener=Opener([ply]);client=CustomClient(CONFIG,opener=opener)
    result=generate(capture,CONFIG,client=client)
    assert result['count']==32 and result['generated'] and not result['trained']
    assert (capture/'output/scene.splat').stat().st_size==32*32
    with zipfile.ZipFile(io.BytesIO(opener.archive)) as archive:
        assert set(archive.namelist())=={'capture.json','images/00000.jpg'}
        assert json.loads(archive.read('capture.json'))['model']=='test-model'
    assert opener.requests[0].get_header('Authorization')=='Bearer '+CONFIG['api_key']
    assert CONFIG['api_key'].encode() not in opener.archive
    assert all(CONFIG['api_key'].encode() not in p.read_bytes() for p in capture.rglob('*') if p.is_file())

def test_queued_provider_poll_and_result(capture,ply):
    status=ENDPOINT+'/jobs/123';artifact=ENDPOINT+'/result.ply'
    opener=Opener([{'status_url':status},{'status':'queued'},{'status':'processing'},{'status':'completed','ply_url':artifact},ply])
    client=CustomClient(CONFIG,opener=opener,sleep=lambda _:None)
    assert client.generate(capture,lambda *_:None)==ply
    assert [r.get_method() for r in opener.requests]==['POST','GET','GET','GET','GET']

def test_cross_origin_provider_jobs_never_receive_credentials(capture):
    opener=Opener([{'status_url':'https://other.example/private'}]);client=CustomClient(CONFIG,opener=opener)
    with pytest.raises(ValueError):client.generate(capture,lambda *_:None)
    assert len(opener.requests)==1

def test_custom_auth_error_redacts_provider_body_and_key(capture):
    error=urllib.error.HTTPError(ENDPOINT,401,CONFIG['api_key'],{},io.BytesIO(CONFIG['api_key'].encode()))
    client=CustomClient(CONFIG,opener=Opener([error]))
    with pytest.raises(RemoteError) as exc:client.generate(capture,lambda *_:None)
    assert 'HTTP 401' in str(exc.value) and CONFIG['api_key'] not in str(exc.value)

def test_queued_custom_cancellation_uses_delete(capture):
    status=ENDPOINT+'/jobs/123';opener=Opener([{'status_url':status},{'status':'processing'},{'status':'cancelled'}])
    def interrupted(_):raise KeyboardInterrupt()
    client=CustomClient(CONFIG,opener=opener,sleep=interrupted)
    with pytest.raises(KeyboardInterrupt):client.generate(capture,lambda *_:None)
    assert opener.requests[-1].get_method()=='DELETE'
    assert opener.requests[-1].full_url==status

def test_custom_launch_requires_consent_and_passes_key_only_over_stdin(tmp_path,monkeypatch):
    library=Library(tmp_path);key=library.new();library.upload(key,'photo.jpg',b'photo')
    library.set_key('custom-http',CONFIG['api_key'],endpoint=ENDPOINT,model=CONFIG['model'])
    with pytest.raises(ValueError,match='Confirm'):library.launch(key,backend='custom-http')
    class Pipe(io.BytesIO):
        def close(self):pass
    process=Mock();process.pid=1234;process.stdin=Pipe();process.poll.return_value=None
    popen=Mock(return_value=process);monkeypatch.setattr('workspace_splat.server.subprocess.Popen',popen)
    library.launch(key,backend='custom-http',allow_external_upload=True,all_images=True,max_frames=0,sample_fps=3)
    command=popen.call_args.args[0]
    assert '--all-images' in command and command[command.index('--max-frames')+1]=='0'
    assert json.loads(process.stdin.getvalue())==CONFIG
    assert CONFIG['api_key'] not in repr(popen.call_args)

def test_ultra_high_budget_reaches_worker_and_original_scene_is_preserved(tmp_path,monkeypatch):
    library=Library(tmp_path/'data',rebuild_completed=True);key=library.new();capture=library.capture(key)
    (capture/'images').mkdir();(capture/'images/00000.jpg').write_bytes(b'photo')
    (capture/'capture.json').write_text('{"images":[{"name":"00000.jpg","source":"photo.jpg"}]}')
    (capture/'output').mkdir();(capture/'output/scene.json').write_text('{"trained":true}')
    process=Mock();process.pid=1234;process.poll.return_value=None
    popen=Mock(return_value=process);monkeypatch.setattr('workspace_splat.server.subprocess.Popen',popen)
    result=library.launch(key,steps=20000,max_gaussians=160000,train_edge=1280)
    assert result['id']!=key
    assert (capture/'output/scene.json').read_text()=='{"trained":true}'
    command=popen.call_args.args[0]
    for name,value in [('--steps','20000'),('--max-gaussians','160000'),('--train-edge','1280')]:
        assert command[command.index(name)+1]==value

@pytest.mark.parametrize('options',[{'steps':30001},{'max_gaussians':500001},{'train_edge':1921}])
def test_invalid_quality_budgets_are_rejected_before_worker_launch(tmp_path,options):
    library=Library(tmp_path);key=library.new()
    with pytest.raises(ValueError):library.launch(key,**options)
    assert library.process is None

def test_standalone_routes_and_provider_settings_over_http(tmp_path):
    library=Library(tmp_path,upload_budget=None,rebuild_completed=True)
    web=Path(__file__).resolve().parents[1]/'web'
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(library,web,app_name='splat-studio'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(url+'/api/health') as response:assert json.load(response)['app']=='splat-studio'
        for route,expected in [('/',b'Ultra-high detail'),('/quality.js',b'QUALITY_PRESETS'),('/viewer.js',b'GaussianViewer')]:
            with urllib.request.urlopen(url+route) as response:assert expected in response.read()
        body=json.dumps({'provider':'custom-http',**CONFIG}).encode()
        request=urllib.request.Request(url+'/api/providers/key',body,{'Content-Type':'application/json'})
        with urllib.request.urlopen(request) as response:assert json.load(response)['configured']
        with urllib.request.urlopen(url+'/api/providers') as response:
            content=response.read();assert CONFIG['api_key'].encode() not in content
            assert json.loads(content)['custom-http']['endpoint']==ENDPOINT
        with urllib.request.urlopen(url+'/api/captures') as response:assert json.load(response)==[]
    finally:server.shutdown();server.server_close();thread.join(timeout=2)

def test_video_sampling_budget_does_not_drop_still_images(tmp_path):
    import cv2
    source=tmp_path/'sources';source.mkdir();video=source/'a-video.avi'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),10,(64,64));assert writer.isOpened()
    for i in range(10):writer.write(np.full((64,64,3),i*20,dtype=np.uint8))
    writer.release()
    for i in range(3):Image.new('RGB',(32,32),(i*50,200,20)).save(source/f'z-{i}.png')
    result=ingest([source],tmp_path/'capture',all_images=True,max_frames=2,fps=10)
    assert len(result['images'])==5
    assert sum(image['source'].endswith('.png') for image in result['images'])==3
    assert any('Sampling limit' in warning for warning in result['warnings'])
    unlimited=ingest([video],tmp_path/'unlimited',max_frames=0,fps=10)
    assert len(unlimited['images'])==10

from http.server import ThreadingHTTPServer
import json
import threading
import urllib.request
import urllib.error
import pytest
from workspace_splat.server import Library,handler_for

@pytest.fixture
def service(tmp_path):
    library=Library(tmp_path/'private-data')
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(library))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}',library
    server.shutdown();server.server_close();thread.join(timeout=2)


def test_capture_upload_status_and_private_paths(service):
    url,library=service
    request=urllib.request.Request(url+'/api/captures',b'{}',{'Content-Type':'application/json'})
    with urllib.request.urlopen(request) as r:key=json.load(r)['id']
    request=urllib.request.Request(url+'/api/upload/'+key,b'bytes',{'X-Filename':'sample.jpg'})
    with urllib.request.urlopen(request) as r:assert json.load(r)['bytes']==5
    with urllib.request.urlopen(url+'/api/captures/'+key) as r:assert json.load(r)['trained'] is False
    for path in ('/scenes/'+key+'/uploads/sample.jpg','/scenes/'+key+'/../../capture.json','/.env'):
        with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(url+path)
        assert exc.value.code in (400,404)
    request=urllib.request.Request(url+'/api/captures',headers={'Origin':'https://external.example'})
    with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(request)
    assert exc.value.code==403


def test_embeddable_viewer_csp_and_js_mime(service):
    url,_=service
    with urllib.request.urlopen(url+'/viewer') as r:
        assert b'Interactive Gaussian' in r.read()
        assert 'http://127.0.0.1:8766' in r.headers['Content-Security-Policy']
    with urllib.request.urlopen(url+'/viewer.js') as r:
        assert 'javascript' in r.headers['Content-Type']
        assert b'drawArraysInstanced' in r.read()


def test_credentials_endpoint_never_returns_the_key(service):
    url,library=service
    secret='mock-fal-key-for-service-tests'
    request=urllib.request.Request(url+'/api/providers/key',json.dumps({'provider':'sam3d-fal','api_key':secret}).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(request) as response:
        payload=response.read()
        assert secret.encode() not in payload and json.loads(payload)['configured']
    with urllib.request.urlopen(url+'/api/providers') as response:
        payload=response.read();assert secret.encode() not in payload
        assert json.loads(payload)['sam3d-fal']['configured']
    request=urllib.request.Request(url+'/api/providers/key',b'{"provider":"sam3d-fal","api_key":""}',{'Content-Type':'application/json'})
    with urllib.request.urlopen(request) as response:assert not json.load(response)['configured']


def test_remote_consent_enforced_over_http_before_spawning(service):
    url,library=service;key=library.new();library.upload(key,'photo.jpg',b'jpeg')
    library.set_key('sam3d-fal','fake-key')
    request=urllib.request.Request(url+'/api/run',json.dumps({'id':key,'backend':'sam3d-fal'}).encode(),{'Content-Type':'application/json'})
    with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(request)
    assert exc.value.code==400 and b'Confirm' in exc.value.read()
    assert library.process is None

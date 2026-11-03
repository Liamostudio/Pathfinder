from email.message import Message
from io import BytesIO
import json
from types import SimpleNamespace
import pytest
from lookandpick.mission_control import MissionControl,make_handler
from lookandpick.workspaces.service import WorkspaceService
from lookandpick.workspaces.repository import atomic_json


def request(service,path,method='GET',payload=None,raw=None,headers=None):
    cls=make_handler(MissionControl(),service);h=cls.__new__(cls);h.server=SimpleNamespace(server_address=('127.0.0.1',8765));h.path=path;h.headers=Message();h.headers['Host']='127.0.0.1:8765'
    if payload is not None:raw=json.dumps(payload).encode();h.headers['Content-Type']='application/json'
    raw=raw or b'';h.headers['Content-Length']=str(len(raw))
    for k,v in (headers or {}).items():h.headers[k]=v
    h.rfile=BytesIO(raw);h.wfile=BytesIO();h.status=None;h.response_headers={}
    h.send_response=lambda code:setattr(h,'status',code);h.send_header=lambda k,v:h.response_headers.update({k:v});h.end_headers=lambda:None
    getattr(h,'do_'+method)();return h


def test_main_app_navigation_and_workspace_route_contract(tmp_path):
    s=WorkspaceService(tmp_path)
    page=request(s,'/');assert page.status==200 and b'set up workspace' in page.wfile.getvalue()
    created=request(s,'/api/workspaces','POST',{'name':'Local desk'});assert created.status==201
    key=json.loads(created.wfile.getvalue())['id']
    upload=request(s,f'/api/workspaces/{key}/sources','POST',raw=b'photo bytes',headers={'X-Filename':'desk.jpg'})
    assert upload.status==201
    data=json.loads(upload.wfile.getvalue());assert data['sources'][0]['sha256']
    removed=request(s,f'/api/workspaces/{key}/sources/{data["sources"][0]["id"]}','DELETE')
    assert removed.status==200 and json.loads(removed.wfile.getvalue())['sources'][0]['excluded']
    for path in ['/api/workspaces/../assets/.env','/api/robot-assets/../../.env','/api/workspaces/'+key+'/assets/.env']:
        assert request(s,path).status in (400,404)
    blocked=request(s,'/api/workspaces','POST',{'name':'bad'},headers={'Origin':'https://external.example'})
    assert blocked.status==403


def test_registered_video_assets_support_ranges_and_do_not_serve_private_logs(tmp_path):
    s=WorkspaceService(tmp_path);w=s.repo.create('Preview');key=w['id'];folder=s.repo.folder(key)
    (folder/'preview.mp4').write_bytes(b'0123456789');(folder/'private.log').write_text('private')
    w['draft']={'assets':{'preview.mp4':'preview.mp4'}};s.repo.write(w)
    response=request(s,f'/api/workspaces/{key}/assets/preview.mp4',headers={'Range':'bytes=2-5'})
    assert response.status==206 and response.wfile.getvalue()==b'2345'
    assert response.response_headers['Content-Range']=='bytes 2-5/10'
    assert response.response_headers['Content-Type']=='video/mp4'
    assert request(s,f'/api/workspaces/{key}/assets/preview.mp4',headers={'Range':'bytes=99-100'}).status==416
    assert request(s,f'/api/workspaces/{key}/assets/private.log').status==404


def test_unknown_settings_and_arbitrary_paths_do_not_launch_worker(tmp_path):
    s=WorkspaceService(tmp_path);w=s.repo.create('Invalid')
    response=request(s,f'/api/workspaces/{w["id"]}/jobs','POST',{'settings':{'output':'/tmp/unsafe'}})
    assert response.status==400 and not s.processes

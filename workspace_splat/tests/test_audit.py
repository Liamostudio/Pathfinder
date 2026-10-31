"""Regression tests for the October 31 audit findings."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

from PIL import Image
import pytest

from workspace_splat.ingest import ingest
from workspace_splat.remote import FalClient,RemoteError,generate
from workspace_splat.server import Library,worker_alive
from workspace_splat.worker import adopt_capture
from test_remote import BASE,KEY,Opener,ply


def test_selected_normalised_id_beats_colliding_source_filename(tmp_path,ply):
    first=tmp_path/'00001.jpg';second=tmp_path/'other.jpg'
    Image.new('RGB',(320,240),'red').save(first)
    Image.new('RGB',(320,240),'green').save(second)
    capture=tmp_path/'capture';ingest([first,second],capture)
    client=Mock();client.generate.return_value=(ply,'test-request')
    scene=generate(capture,KEY,image_name='00001.jpg',client=client)
    assert client.generate.call_args.args[0]==capture/'images/00001.jpg'
    assert scene['selected_image']=='00001.jpg'
    with Image.open(client.generate.call_args.args[0]) as image:
        assert image.getpixel((10,10))[1]>100


@pytest.mark.parametrize('field,value',[
    ('status_url','https://external.example/requests/test-request/status'),
    ('response_url',BASE.replace('test-request','another-request')),
])
def test_invalid_queue_response_cancels_submitted_job(tmp_path,field,value):
    image=tmp_path/'image.jpg';image.write_bytes(b'jpeg')
    submitted={'request_id':'test-request','status_url':BASE+'/status','response_url':BASE,'cancel_url':BASE+'/cancel'}
    submitted[field]=value
    opener=Opener([submitted,{'status':'OK'}])
    with pytest.raises(RemoteError):FalClient(KEY,opener=opener).generate(image,'table',lambda *_:None)
    assert len(opener.requests)==2
    assert opener.requests[-1].full_url==BASE+'/cancel'
    assert opener.requests[-1].get_method()=='PUT'


def test_concurrent_duplicate_upload_has_one_winner(tmp_path):
    library=Library(tmp_path);key=library.new()
    def upload(content):
        try:return library.upload(key,'same.jpg',content)
        except ValueError as exc:return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(upload,[b'first',b'second']))
    assert sum(isinstance(r,dict) for r in results)==1
    assert 'Duplicate upload filename' in results
    assert (library.capture(key)/'uploads/same.jpg').read_bytes() in (b'first',b'second')
    assert not list(library.capture(key).rglob('*.upload'))


def test_cancel_recovers_verified_worker_after_server_restart(tmp_path):
    library=Library(tmp_path/'data');key=library.new();capture=library.capture(key)
    # A test module has the same command-line contract, without importing a GPU runtime.
    package=tmp_path/'workspace_splat';package.mkdir();(package/'__init__.py').touch()
    (package/'worker.py').write_text('import time; time.sleep(30)\n')
    process=subprocess.Popen([sys.executable,'-m','workspace_splat.worker','--capture',str(capture)],cwd=tmp_path,start_new_session=True)
    try:
        (capture/'status.json').write_text(json.dumps({'stage':'training','worker_pid':process.pid}))
        assert worker_alive(process.pid,capture)
        recovered=Library(library.data)
        assert recovered.status(key)['running']
        other=recovered.new();recovered.upload(other,'image.jpg',b'jpeg')
        with pytest.raises(ValueError,match='already running'):recovered.launch(other)
        assert recovered.cancel(key)=={'cancelled':key}
        process.wait(timeout=2)
        assert recovered.status(key)['stage']=='cancelled'
        assert not recovered.status(key)['running']
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=2)


def test_saved_unrelated_pid_is_never_cancelled(tmp_path):
    library=Library(tmp_path);key=library.new()
    (library.capture(key)/'status.json').write_text(json.dumps({'stage':'training','worker_pid':os.getpid()}))
    assert not library.status(key)['running']
    assert library.status(key)['stage']=='failed'
    with pytest.raises(ValueError,match='No active'):library.cancel(key)


def test_ingest_retry_replaces_partial_images_without_nested_directory(tmp_path):
    capture=tmp_path/'capture';(capture/'images').mkdir(parents=True)
    (capture/'images/partial.jpg').write_bytes(b'partial')
    staged=tmp_path/'staged';(staged/'images').mkdir(parents=True)
    (staged/'images/00000.jpg').write_bytes(b'new')
    (staged/'capture.json').write_text('{"images": []}')
    adopt_capture(staged,capture)
    assert [p.name for p in (capture/'images').iterdir()]==['00000.jpg']
    assert (capture/'capture.json').is_file()


def test_worker_start_failure_is_a_clear_retryable_error(tmp_path,monkeypatch):
    library=Library(tmp_path);key=library.new();library.upload(key,'photo.jpg',b'jpeg')
    monkeypatch.setattr('workspace_splat.server.subprocess.Popen',Mock(side_effect=OSError('Runtime unavailable')))
    with pytest.raises(ValueError,match='Could not start'):library.launch(key)
    assert library.status(key)['stage']=='failed'
    assert not library.status(key)['running']


def test_closed_credential_pipe_stops_worker_without_echoing_key(tmp_path,monkeypatch):
    library=Library(tmp_path);key=library.new();library.upload(key,'photo.jpg',b'jpeg');library.set_key('sam3d-fal',KEY)
    process=Mock();process.pid=1234;process.poll.return_value=None
    process.stdin.write.side_effect=BrokenPipeError()
    monkeypatch.setattr('workspace_splat.server.subprocess.Popen',Mock(return_value=process))
    with pytest.raises(ValueError,match='accepting credentials') as exc:
        library.launch(key,backend='sam3d-fal',allow_external_upload=True)
    process.terminate.assert_called_once()
    assert KEY not in str(exc.value)
    assert all(KEY.encode() not in p.read_bytes() for p in library.data.rglob('*') if p.is_file())

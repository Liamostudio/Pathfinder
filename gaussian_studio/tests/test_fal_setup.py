"""fal server configuration, redaction and non-billable failure-path checks."""
import io
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request
from unittest.mock import Mock
import pytest
from gaussian_studio.__main__ import create_server
from workspace_splat.remote import FalClient,RemoteError,QUEUE
from workspace_splat.server import Library,library_from_env

KEY='fake-fal-env-key-for-tests'


def unset_fal_key(monkeypatch):
    # Track both mutations so monkeypatch also removes values added by load_env.
    monkeypatch.setenv('FAL_KEY','')
    monkeypatch.delenv('FAL_KEY')


def test_private_env_key_is_loaded_without_changing_the_file(tmp_path,monkeypatch):
    unset_fal_key(monkeypatch)
    path=tmp_path/'.env';content=f"# Private configuration\nFAL_KEY='{KEY}'\n"
    path.write_text(content)
    library=library_from_env(tmp_path/'captures',env_file=path)
    status=library.providers()['sam3d-fal']
    assert status['configured'] and status['key_source']=='server configuration'
    assert status['model_id']=='fal-ai/sam-3/3d-objects'
    assert KEY not in json.dumps(library.providers())
    assert path.read_text()==content


def test_process_environment_has_precedence_over_env_file(tmp_path,monkeypatch):
    monkeypatch.setenv('FAL_KEY',KEY)
    path=tmp_path/'.env';path.write_text("FAL_KEY='different-fake-key'\n")
    library=library_from_env(tmp_path/'captures',env_file=path)
    assert library._fal_key==KEY
    assert path.read_text()=="FAL_KEY='different-fake-key'\n"


def test_missing_env_configuration_keeps_local_mode_available(tmp_path,monkeypatch):
    unset_fal_key(monkeypatch)
    library=library_from_env(tmp_path/'captures',env_file=tmp_path/'not-present')
    assert not library.providers()['sam3d-fal']['configured']
    assert library.providers()['local']['available']


def test_clearing_or_overriding_a_session_does_not_replace_saved_key(tmp_path,monkeypatch):
    monkeypatch.setenv('FAL_KEY',KEY)
    path=tmp_path/'.env';path.write_text(f"FAL_KEY='{KEY}'\n")
    library=library_from_env(tmp_path/'captures',env_file=path)
    library.set_key('sam3d-fal','fake-session-override')
    assert library.providers()['sam3d-fal']['key_source']=='session'
    library.set_key('sam3d-fal','')
    assert not library.providers()['sam3d-fal']['configured']
    assert path.read_text()==f"FAL_KEY='{KEY}'\n"
    restarted=library_from_env(tmp_path/'captures',env_file=path)
    assert restarted.providers()['sam3d-fal']['configured']


def test_worker_gets_key_over_stdin_and_not_in_arguments_or_environment(tmp_path,monkeypatch):
    monkeypatch.setenv('FAL_KEY',KEY)
    library=Library(tmp_path,fal_key=KEY);capture=library.new();library.upload(capture,'photo.jpg',b'jpeg')
    class Pipe(io.BytesIO):
        def close(self):pass
    process=Mock();process.pid=1234;process.poll.return_value=None;process.stdin=Pipe()
    spawn=Mock(return_value=process);monkeypatch.setattr('workspace_splat.server.subprocess.Popen',spawn)
    library.launch(capture,backend='sam3d-fal',allow_external_upload=True)
    assert json.loads(process.stdin.getvalue())['api_key']==KEY
    assert 'FAL_KEY' not in spawn.call_args.kwargs['env']
    assert KEY not in repr(spawn.call_args)
    assert all(KEY.encode() not in p.read_bytes() for p in library.data.rglob('*') if p.is_file())


def test_desktop_server_exposes_configuration_flags_but_no_secrets(tmp_path,monkeypatch):
    unset_fal_key(monkeypatch)
    path=tmp_path/'.env';path.write_text(f"FAL_KEY='{KEY}'\n")
    server=create_server(tmp_path/'captures',env_file=path)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(url+'/api/providers') as response:
            payload=response.read();status=json.loads(payload)['sam3d-fal']
            assert status['configured'] and status['key_source']=='server configuration'
            assert KEY.encode() not in payload
        with urllib.request.urlopen(url+'/') as response:
            html=response.read();assert KEY.encode() not in html
            assert b'Clear session key' in html
        with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(url+'/.env')
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.parametrize('detail,expected',[
    ('User is locked. Reason: Exhausted balance','Add credits'),
    ('User is locked. Reason: Account budget limit','budget limit'),
    ('User is locked. Reason: Archived account','Account archived'),
    ('User is locked. Reason: Safety review','safety lock'),
    ('User is locked. Reason: Administrator lock','administrator lock'),
    ('User is locked. Reason: Unknown internal detail','Account locked'),
])
def test_403_remedies_are_redacted_and_never_retried(detail,expected):
    error=urllib.error.HTTPError(QUEUE,403,'Forbidden',{},io.BytesIO(json.dumps({'detail':detail,'private_debug':KEY}).encode()))
    opener=Mock();opener.open.side_effect=error
    client=FalClient(KEY,opener=opener)
    with pytest.raises(RemoteError) as exc:client.json(QUEUE,{'image_url':'test'})
    assert expected in str(exc.value) and KEY not in str(exc.value)
    assert opener.open.call_count==1

import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import Mock
import pytest
from lookandpick.workspaces.repository import atomic_json
from lookandpick.workspaces.service import WorkspaceService,identity
from lookandpick.workspaces.runtimes import Runtimes


def test_runtime_interpreter_preserves_venv_symlink_path(tmp_path):
    directory=tmp_path/'venv/bin';directory.mkdir(parents=True);(directory/'python').symlink_to(sys.executable)
    runtime=Runtimes({'geometry_python':str(directory/'python')})
    assert runtime.paths['geometry_python']==str(directory/'python')


def test_missing_runtime_is_actionable_without_importing_models(tmp_path):
    runtime=Runtimes(dict.fromkeys(['geometry_python','sam_python','gaussian_python'],str(tmp_path/'missing')))
    result=runtime.check();assert not result['available']
    assert any('PATHFINDER_GEOMETRY_PYTHON' in s for s in result['issues'])


def test_job_recovery_does_not_trust_an_unrelated_live_pid(tmp_path):
    import os
    service=WorkspaceService(tmp_path);w=service.repo.create('Interrupted');run='run-fake';directory=service.repo.folder(w['id'])/'runs'/run;directory.mkdir(parents=True)
    w['jobs']=[run];service.repo.write(w)
    atomic_json(directory/'job.json',{'id':run,'workspace_id':w['id'],'state':'running','stage':'geometry','created_at':0})
    atomic_json(directory/'owner.json',{'pid':os.getpid(),'start':identity(os.getpid())['start']})
    assert not service.owned(w['id'],run)
    assert service.job(w['id'],run)['state']=='failed'
    with pytest.raises(ValueError,match='No owned'):service.cancel(run)


def test_cancel_surviving_owned_worker_after_server_restart_releases_gpu(tmp_path,monkeypatch):
    real_popen=subprocess.Popen;lease=tmp_path/'gpu.lock';ready=tmp_path/'ready'
    script=f'import fcntl,time,pathlib; f=open({str(lease)!r},"w"); fcntl.flock(f,fcntl.LOCK_EX); pathlib.Path({str(ready)!r}).touch(); time.sleep(30)'
    def launch(command,**kwargs):return real_popen([command[0],'-c',script,*command[1:]],**kwargs)
    monkeypatch.setattr('lookandpick.workspaces.service.subprocess.Popen',launch)
    service=WorkspaceService(tmp_path/'data');w=service.repo.create('Cancellable')
    job=service.start(w['id'],{'mode':'fixture'})
    try:
        deadline=time.monotonic()+3
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert ready.exists()
        recovered=WorkspaceService(service.repo.root)
        assert recovered.job(w['id'],job['id'])['state']=='queued'
        assert recovered.cancel(job['id'])['state']=='cancelled'
        with lease.open('a') as handle:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:
        process=service.processes[job['id']]
        if process.poll() is None:process.terminate()
        process.wait(timeout=3)


def test_corrupt_cached_stage_is_not_accepted(tmp_path):
    import numpy as np
    from lookandpick.workspaces.worker import valid_stage
    atomic_json(tmp_path/'report.json',{'images':[{'name':'00000.jpg'},{'name':'00001.jpg'}]})
    for name in ['00000','00001']:np.savez_compressed(tmp_path/(name+'.npz'),table=np.ones((2,2),bool))
    assert valid_stage(tmp_path,'segmentation')
    (tmp_path/'00001.npz').write_bytes(b'broken archive')
    assert not valid_stage(tmp_path,'segmentation')


def test_retry_uses_failed_settings_instead_of_new_form_values(tmp_path,monkeypatch):
    from lookandpick.workspaces.schema import settings
    s=WorkspaceService(tmp_path);w=s.repo.create('Retry');folder=s.repo.folder(w['id']);run='run-old';d=folder/'runs'/run;d.mkdir(parents=True)
    w['jobs']=[run];w['sources']=[{'name':'x.jpg','sha256':'a','file':'sources/x.jpg'}];s.repo.write(w)
    atomic_json(d/'job.json',{'id':run,'state':'failed','stage':'geometry','created_at':0})
    atomic_json(d/'settings.json',settings({'steps':1000,'video_frames':17}))
    atomic_json(d/'request.json',{'mode':'build'})
    monkeypatch.setattr(s,'capabilities',lambda:{'available':True})
    monkeypatch.setattr('lookandpick.workspaces.service.subprocess.Popen',lambda *a,**k:Mock(pid=99999999))
    new=s.start(w['id'],{'mode':'retry','settings':{'steps':8000}})
    cfg=json.loads((folder/'runs'/new['id']/'settings.json').read_text())
    assert cfg['steps']==1000 and cfg['video_frames']==17


def test_cpu_worker_survives_the_spawning_request_thread(tmp_path):
    import threading
    code="from lookandpick.workspaces.people_hands import start_parent_watchdog; import sys; start_parent_watchdog(.01); print('ready',flush=True); [print(line.strip(),flush=True) for line in sys.stdin]"
    handles=[]
    def request_thread():
        process=subprocess.Popen([sys.executable,'-u','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        assert process.stdout.readline().strip()=='ready';handles.append(process)
    thread=threading.Thread(target=request_thread);thread.start();thread.join(timeout=3);assert not thread.is_alive()
    process=handles[0]
    try:
        time.sleep(.05);assert process.poll() is None
        process.stdin.write('second request\n');process.stdin.flush()
        assert process.stdout.readline().strip()=='second request'
    finally:process.stdin.close();process.wait(timeout=3)

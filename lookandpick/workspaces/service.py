"""Main-app workspace service: subprocess jobs, lifecycle and registered assets."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import zipfile
from .repository import Repository,atomic_json,read_json,fresh_id
from .schema import settings,ID
from .runtimes import Runtimes,ROOT


def identity(pid):
    try:
        proc=Path('/proc')/str(pid);stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
        if proc.stat().st_uid!=os.getuid() or stat[0]=='Z':return None
        return {'start':stat[19],'args':(proc/'cmdline').read_bytes().decode().split('\0')}
    except (OSError,ValueError,IndexError):return None


def group_alive(group):
    # A dying process may clear cmdline before closing its GPU lease. Wait for
    # every non-zombie member of the verified process group, not just its leader.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:
            fields=(entry/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0]!='Z' and int(fields[2])==group:return True
        except (OSError,ValueError,IndexError):pass
    return False


class WorkspaceService:
    def __init__(self,root=None,runtimes=None):
        self.repo=Repository(root or ROOT/'recordings/workspaces');self.runtime=runtimes or Runtimes();self.processes={};self.capability=None;self.capability_time=0
        self.lock=threading.RLock()
        from .areas import WorkAreas
        self.areas=WorkAreas(self)

    def capabilities(self):
        if self.capability is None or time.monotonic()-self.capability_time>30:
            self.capability=self.runtime.check();self.capability_time=time.monotonic()
        return self.capability

    def owned(self,key,run):
        path=self.repo.folder(key)/'runs'/run/'owner.json'
        if not path.is_file():return False
        owner=read_json(path);actual=identity(owner.get('pid'))
        if not actual or actual['start']!=owner.get('start'):return False
        args=actual['args']
        try:
            return args[args.index('-m')+1]=='lookandpick.workspaces.worker' and args[args.index('--workspace')+1]==key and args[args.index('--run')+1]==run and Path(args[args.index('--root')+1]).resolve()==self.repo.root and os.getpgid(owner['pid'])==owner['pid']
        except (ValueError,IndexError,OSError):return False

    def job(self,key,run):
        if not ID.fullmatch(run):raise ValueError('Invalid run ID')
        directory=self.repo.folder(key)/'runs'/run;job=read_json(directory/'job.json')
        if job['state'] in ('queued','running') and not self.owned(key,run):
            owner=directory/'owner.json'
            # Brief startup window before the parent publishes process identity.
            if owner.exists() or time.time()-job['created_at']>5:
                job.update(state='failed',error='Setup was interrupted. Retry to reuse validated completed stages.',message='Setup interrupted',eta_seconds=None);atomic_json(directory/'job.json',job)
        if job.get('started_at') and job['state'] in ('queued','running'):job['elapsed_seconds']=round(time.time()-job['started_at'],1)
        if job['state']=='ready':
            with self.repo.lock:
                value=self.repo.load(key)
                if value['jobs'][-1]==run and value.get('applied_run')!=run:
                    draft=read_json(directory/'result.json');old=value['draft']
                    if old:
                        draft['robot']=old['robot'];draft['layers']=old['layers'];draft['calibration']=old['calibration']
                        if draft['frames']['canonical_from_map']==old['frames']['canonical_from_map']:
                            for field in ('working_area','marker_alignment'):
                                if field in old:draft[field]=old[field]
                    value['draft']=draft;value['applied_run']=run;self.repo.write(value)
                    if old:self.repo.patch(key,{})
        # Never expose managed filesystem paths or private logs.
        public=copy.deepcopy(job);public.pop('capture',None);public.pop('result',None);public.pop('pid',None)
        for artifact in public.get('artifacts',[]):artifact.pop('file',None)
        return public

    def locate_job(self,run):
        if not isinstance(run,str) or not ID.fullmatch(run):raise ValueError('Invalid job ID')
        paths=list(self.repo.root.glob(f'ws-*/runs/{run}/job.json'))
        if len(paths)!=1:raise FileNotFoundError('Setup job not found')
        return paths[0].parents[2].name

    def get(self,key):
        value=self.repo.load(key)
        if value['jobs']:value['job']=self.job(key,value['jobs'][-1]);value.update(self.repo.load(key))
        return value

    def list(self):
        active=self.repo.active()
        return {'workspaces':[self.get(v['id']) for v in self.repo.list()],'active':active}

    def patch(self,key,patch):
        with self.lock,self.repo.lock:
            value=self.repo.load(key)
            if 'surfaces' in patch and any(self.job(key,r)['state'] in ('queued','running') for r in value['jobs']):
                raise ValueError('Wait for setup to finish or cancel it before editing surfaces')
            return self.repo.patch(key,patch)

    def start(self,key,body):
        mode=body.get('mode','build')
        if mode not in ('prepare','build','fixture','previews','retry'):raise ValueError('Invalid setup action')
        with self.lock,self.repo.lock:
            value=self.repo.load(key)
            if any(self.job(key,r)['state'] in ('queued','running') for r in value['jobs']):raise ValueError('This workspace already has a setup job')
            if mode=='retry':
                if not value['jobs']:raise ValueError('No setup job to retry')
                if self.job(key,value['jobs'][-1])['state'] not in ('failed','cancelled','needs_input'):raise ValueError('Only incomplete jobs can be retried')
                previous=self.repo.folder(key)/'runs'/value['jobs'][-1]
                cfg=settings(read_json(previous/'settings.json'));mode=read_json(previous/'request.json')['mode']
            else:cfg=settings(body.get('settings',value['draft']['settings'] if value['draft'] else {}))
            sources=[s for s in value['sources'] if not s.get('excluded')]
            if not sources and mode!='fixture':raise ValueError('Import source photos or a video first')
            if mode=='build':
                capability=self.capabilities()
                if not capability['available']:raise ValueError(' '.join(capability['issues']))
            cache=[]
            for old in value['jobs']:
                path=self.repo.folder(key)/'runs'/old/'completed.json'
                if path.exists():cache.extend(read_json(path).values())
            if mode=='previews':mode='build' # Cache keys retain inference and fitting; only preview options change.
            run=fresh_id('run');directory=self.repo.folder(key)/'runs'/run;(directory/'logs').mkdir(parents=True)
            job={'id':run,'workspace_id':key,'state':'queued','stage':'ingest','progress':{},'elapsed_seconds':0,'eta_seconds':None,'warnings':[],'artifacts':[],'created_at':time.time(),'message':'Queued local workspace setup'}
            atomic_json(directory/'job.json',job);atomic_json(directory/'settings.json',cfg);atomic_json(directory/'runtimes.json',self.runtime.paths)
            atomic_json(directory/'request.json',{'mode':mode,'sources':sources,'cache':cache,'previous':value['draft']})
            value['jobs'].append(run);self.repo.write(value)
            with (directory/'logs/worker.log').open('ab') as log:
                try:
                    process=subprocess.Popen([sys.executable,'-u','-m','lookandpick.workspaces.worker','--root',str(self.repo.root),'--workspace',key,'--run',run],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
                    actual=identity(process.pid)
                    atomic_json(directory/'owner.json',{'pid':process.pid,'start':actual['start'] if actual else ''})
                    self.processes[run]=process
                except OSError:
                    job.update(state='failed',error='Could not start local setup worker');atomic_json(directory/'job.json',job);raise ValueError(job['error']) from None
            return job

    def cancel(self,run):
        with self.lock:
            key=self.locate_job(run)
            if not self.owned(key,run):raise ValueError('No owned setup worker is running')
            directory=self.repo.folder(key)/'runs'/run;owner=read_json(directory/'owner.json')
            os.killpg(owner['pid'],signal.SIGTERM);deadline=time.monotonic()+10
            while group_alive(owner['pid']) and time.monotonic()<deadline:time.sleep(.05)
            if group_alive(owner['pid']):
                os.killpg(owner['pid'],signal.SIGKILL)
                deadline=time.monotonic()+3
                while group_alive(owner['pid']) and time.monotonic()<deadline:time.sleep(.05)
            if group_alive(owner['pid']):raise ValueError('Worker is still stopping; GPU ownership has not been released yet')
            if run in self.processes:self.processes[run].wait(timeout=2)
            job=read_json(directory/'job.json');job.update(state='cancelled',message='Setup cancelled; active workspace preserved');atomic_json(directory/'job.json',job)
            return self.job(key,run)

    def asset(self,key,name,version=None,run=None):
        if run:
            raw=read_json(self.repo.folder(key)/'runs'/run/'job.json');entry=next((a for a in raw['artifacts'] if a['id']==name),None)
            if not entry:raise FileNotFoundError('Stage preview unavailable')
            return self.repo.resolve(key,entry['file'])
        value=self.get(key);draft=self.repo.version(key,version) if version else value['draft']
        if not draft:raise FileNotFoundError('Workspace has no result')
        if name not in draft['assets']:raise FileNotFoundError('Unknown workspace asset')
        return self.repo.resolve(key,draft['assets'][name])

    def frame(self,key,run,name):
        if not isinstance(name,str) or not __import__('re').fullmatch(r'\d{5}\.jpg',name):raise FileNotFoundError('Unknown frame')
        raw=read_json(self.repo.folder(key)/'runs'/run/'job.json')
        if 'capture' not in raw:raise FileNotFoundError('Frame selection is not ready')
        return self.repo.resolve(key,str(Path(raw['capture'])/'images'/name))

    def bundle(self,key,version):
        manifest=self.repo.version(key,version);target=self.repo.folder(key)/'versions'/version/'workspace.zip'
        with self.lock:
            if not target.exists():
                temp=target.with_suffix('.tmp')
                with zipfile.ZipFile(temp,'w',zipfile.ZIP_STORED) as out:
                    out.writestr('workspace.json',json.dumps(manifest,indent=2))
                    for relative in set(manifest['assets'].values()):out.write(self.repo.resolve(key,relative),relative)
                    urdf=ROOT/'simulation/assets/so101/so101_new_calib.urdf';out.write(urdf,'robot/so101_new_calib.urdf')
                    for p in (urdf.parent/'assets').glob('*.stl'):out.write(p,'robot/assets/'+p.name)
                temp.replace(target)
        return target

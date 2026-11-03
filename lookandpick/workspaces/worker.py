"""Owned setup worker. Model processes are isolated and share this process group."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from uuid import uuid4
from .repository import Repository,atomic_json,read_json
from .runtimes import Runtimes,ROOT
from .schema import STAGES,cache_keys
from lookandpick.gpu import gpu_lease

REQUIRED={'ingest':['capture.json'],'geometry':['predictions.npz','consistency.npz','report.json','depth-surfaces.glb'],
          'segmentation':['report.json'],'surfaces':['surfaces.npz','masks.npz','observed-objects.npz','workspace-surfaces.glb','report.json'],
          'appearance':['output/scene.json','output/scene.splat','output/scene.ply','output/gaussians.npz'],'previews':['geometry.mp4','surfaces.mp4','appearance.mp4','masks.mp4','stage-videos.zip']}


def valid_stage(path,stage):
    try:
        if not all((path/name).is_file() and (path/name).stat().st_size for name in REQUIRED[stage]):return False
        if stage=='ingest':
            images=read_json(path/'capture.json')['images']
            return len(images)>=2 and all(__import__('re').fullmatch(r'\d{5}\.jpg',i['name']) and (path/'images'/i['name']).is_file() for i in images)
        if (path/'report.json').exists():read_json(path/'report.json')
        arrays=list(path.glob('*.npz'))
        if stage=='segmentation':
            images=read_json(path/'report.json')['images']
            if not 2<=len(images)<=4:return False
            arrays=[path/(Path(i['name']).stem+'.npz') for i in images]
        if stage=='appearance':
            if read_json(path/'output/scene.json')['trained'] is not True:return False
            if (path/'output/scene.splat').stat().st_size%32:return False
            arrays=[path/'output/gaussians.npz']
        for array in arrays:
            with zipfile.ZipFile(array) as archive:
                if not archive.namelist() or archive.testzip() is not None:return False
                for name in archive.namelist():
                    with archive.open(name) as member:
                        if member.read(6)!=b'\x93NUMPY':return False
        if stage=='previews':
            with zipfile.ZipFile(path/'stage-videos.zip') as archive:
                if archive.testzip() is not None:return False
        return True
    except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile,EOFError):return False


class Worker:
    def __init__(self,root,key,run):
        self.repo=Repository(root);self.key=key;self.folder=self.repo.folder(key);self.run=run;self.directory=self.folder/'runs'/run
        self.config=read_json(self.directory/'settings.json');self.request=read_json(self.directory/'request.json');self.paths={};self.started=time.time()
        self.status=read_json(self.directory/'job.json');self.status['started_at']=self.started;self.status['pid']=os.getpid();self.status['warnings']=[]
        self.runtime=Runtimes(read_json(self.directory/'runtimes.json'));self.completed={};self.log=None

    def update(self,state=None,stage=None,progress=None,message=None,**extra):
        if state:self.status['state']=state
        if stage:self.status['stage']=stage
        if message:self.status['message']=message[:300]
        if progress is not None:self.status['progress']=progress
        self.status.update(extra,elapsed_seconds=round(time.time()-self.started,1),eta_seconds=None,updated_at=time.time())
        atomic_json(self.directory/'job.json',self.status)

    def execute(self,command,stage):
        env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_DATASETS_OFFLINE='1',PYTHONUNBUFFERED='1',PATHFINDER_WORKSPACE_GPU='1')
        env['PYTHONPATH']=str(ROOT)
        with (self.directory/'logs'/f'{stage}-{uuid4().hex[:6]}.log').open('w') as log:
            process=subprocess.Popen([str(x) for x in command],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace')
            try:
                for line in process.stdout:
                    log.write(line);log.flush()
                    if len(line)>8192:continue
                    try:event=json.loads(line)
                    except (ValueError,TypeError):continue
                    if not isinstance(event,dict):continue
                    if isinstance(event.get('step'),int) and isinstance(event.get('steps'),int):
                        self.update(progress={'completed':event['step'],'total':event['steps'],'unit':'steps'})
                    elif stage=='segmentation' and isinstance(event.get('name'),str):
                        count=self.status.get('progress',{}).get('completed',0)+1
                        self.update(progress={'completed':count,'total':self.view_count,'unit':'images'})
                if process.wait()!=0:
                    details=Path(log.name).read_text()[-6000:].lower()
                    if 'out of memory' in details:raise RuntimeError('GPU memory exhausted. Keep 2–4 views, lower resolution or close other GPU workloads; completed stages are retained.')
                    if any(s in details for s in ('not enough points','too little support','insufficient covered','table estimates disagree')):
                        raise ValueError('Surface support is insufficient. Review table/wall masks, choose overlapping sharp views and retry this stage.')
                    raise RuntimeError(f'{stage.capitalize()} could not complete. Check the local runtime and retry; private logs are retained.')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()

    def attempt(self,stage,action,key):
        for record in reversed(self.request.get('cache',[])):
            if record.get('stage')==stage and record.get('key')==key:
                cached=self.folder/record['path']
                if cached.resolve().is_relative_to(self.folder) and valid_stage(cached,stage):
                    self.paths[stage]=cached;self.completed[stage]=record
                    atomic_json(self.directory/'completed.json',self.completed)
                    self.update(stage=stage,progress={},message=f'Reusing validated {stage} stage');self.publish_artifacts(stage,cached);return cached
        path=self.directory/(stage+'-'+uuid4().hex[:8])
        self.update('running',stage,{},f'Processing {stage}')
        action(path)
        if not valid_stage(path,stage):raise RuntimeError(f'{stage.capitalize()} artifacts did not validate; previous completed stages are preserved')
        self.paths[stage]=path;self.completed[stage]={'stage':stage,'key':key,'path':str(path.relative_to(self.folder))}
        atomic_json(self.directory/'completed.json',self.completed)
        self.publish_artifacts(stage,path)
        return path

    def publish_artifacts(self,stage,path):
        extensions={'.jpg','.png','.mp4','.glb','.ply','.splat'};items=[]
        for p in sorted(path.glob('*')):
            if p.is_file() and p.suffix in extensions:items.append({'id':stage+'-'+p.name,'name':p.name,'file':str(p.relative_to(self.folder)),'kind':'video' if p.suffix=='.mp4' else 'image' if p.suffix in ('.jpg','.png') else 'download'})
        self.status['artifacts']=[x for x in self.status.get('artifacts',[]) if not x['id'].startswith(stage+'-')]+items
        self.update()

    def ingest(self,path):
        request=self.directory/'ingest-request.json'
        atomic_json(request,{'sources':[str(self.repo.resolve(self.key,s['file'])) for s in self.request['sources']], 'output':str(path),'settings':self.config})
        self.execute([self.runtime.paths['gaussian_python'],'-m','lookandpick.workspaces.ingestion',request],'ingest')

    def run_local(self):
        cfg=self.config;keys=cache_keys(cfg,[{'name':s['name'],'sha256':s['sha256']} for s in self.request['sources']])
        capture=self.attempt('ingest',self.ingest,keys['ingest'])
        manifest=read_json(capture/'capture.json');self.update(warnings=manifest.get('warnings',[]));names=[i['name'] for i in manifest['images']]
        if self.request['mode']=='prepare':
            self.update('needs_input','ingest',{},'Choose 2–4 normalised views, then reconstruct',capture=str(capture.relative_to(self.folder)),frames=manifest['images']);return
        self.update(capture=str(capture.relative_to(self.folder)),frames=manifest['images'])
        selected=cfg['view_ids']
        if not selected:
            count=min(len(names),cfg['max_views']);selected=[names[round(i*(len(names)-1)/(count-1))] for i in range(count)]
        if not 2<=len(selected)<=4 or set(selected)-set(names):raise ValueError('Choose 2–4 existing normalised views')
        self.view_count=len(selected)
        subset=self.directory/'selected';(subset/'images').mkdir(parents=True)
        for name in selected:shutil.copy2(capture/'images'/name,subset/'images'/name)
        manifest['images']=[i for n in selected for i in manifest['images'] if i['name']==n];atomic_json(subset/'capture.json',manifest)
        self.paths['capture']=subset
        def waiting():self.update('queued',message='Waiting for the shared local GPU queue')
        with gpu_lease(self.run,waiting=waiting):
            geometry=self.attempt('geometry',lambda p:self.execute([self.runtime.paths['geometry_python'],'-m','gaussian_studio.meta_reconstruct',subset,'--output',p,'--offline','--revision',self.runtime.map_checkpoint.name,'--source-revision',self.runtime.map_source.name.removeprefix('map-anything-'),'--resolution',str(cfg['resolution'])],'geometry'),keys['geometry'])
            def masks(p):
                self.update(progress={'completed':0,'total':len(selected),'unit':'images'})
                prompts=self.directory/'prompts.json';atomic_json(prompts,cfg['prompts'])
                self.execute([self.runtime.paths['sam_python'],'-I',ROOT/'gaussian_studio/meta_masks.py',subset,'--output',p,'--checkpoint',self.runtime.sam_checkpoint,'--prompts',prompts],'segmentation')
                if cfg['mask_edits']:
                    request=self.directory/'mask-edits.json';atomic_json(request,{'directory':str(p),'edits':cfg['mask_edits']})
                    self.execute([self.runtime.paths['gaussian_python'],'-m','lookandpick.workspaces.ingestion',request,'--mask-edits'],'segmentation')
                request=self.directory/'mask-preview.json';atomic_json(request,{'capture':str(subset),'masks':str(p)})
                self.execute([self.runtime.paths['gaussian_python'],'-m','lookandpick.workspaces.ingestion',request,'--overlays'],'segmentation')
            segmentation=self.attempt('segmentation',masks,keys['segmentation'])
            def surfaces(p):
                command=[self.runtime.paths['geometry_python'],'-m','gaussian_studio.workspace_surfaces','--geometry',geometry,'--masks',segmentation,'--output',p,'--reference-view',cfg['reference_view'] or 'auto']
                if cfg['surface_overrides']:
                    override=self.directory/'surface-overrides.json';atomic_json(override,{'canonical_from_map':self.request['previous']['frames']['canonical_from_map'],'surfaces':cfg['surface_overrides']});command+=['--overrides',override]
                self.execute(command,'surfaces')
            surface=self.attempt('surfaces',surfaces,keys['surfaces'])
            self.attempt('appearance',lambda p:self.execute([self.runtime.paths['gaussian_python'],'-m','gaussian_studio.surface_splat',subset,'--geometry',geometry,'--surfaces',surface,'--output',p,'--steps',str(cfg['steps']),'--max-edge',str(cfg['max_edge'])],'appearance'),keys['appearance'])
            self.attempt('previews',self.previews,keys['previews'])
        self.finish()

    def previews(self,path):
        path.mkdir();python=self.runtime.paths['gaussian_python'];geometry=self.paths['geometry'];order=','.join(map(str,[*range(self.view_count),0]))
        common=[python,'-m','gaussian_studio.stage_video','--geometry',geometry,'--seconds',str(self.config['preview_seconds']),'--fps','12','--camera-order',order]
        for name,args in [('geometry',[]),('surfaces',['--cloud',self.paths['surfaces']/'surfaces.npz']),('appearance',['--trained',self.paths['appearance']])]:
            self.execute([*common,*args,'--output',path/(name+'.mp4'),'--title',name.capitalize()+' | selected-camera sweep'],'previews')
            self.publish_artifacts('previews',path)
        self.execute([python,'-m','gaussian_studio.mask_video',self.paths['capture'],'--masks',self.paths['segmentation'],'--output',path/'masks.mp4'],'previews')
        if self.config['preview_orbit']:self.execute([*common,'--trained',self.paths['appearance'],'--orbit','--output',path/'orbit.mp4','--title','Full orbit diagnostic | unseen areas uncertain'],'previews')
        with zipfile.ZipFile(path/'stage-videos.zip','w',zipfile.ZIP_STORED) as bundle:
            for p in path.glob('*.mp4'):bundle.write(p,p.name)

    def fixture(self):
        stage=ROOT/'gaussian_studio/exports/meta-workspace-20261101';capture=ROOT/'gaussian_studio/data/capture-20261101-121326-e1e223';appearance=ROOT/'gaussian_studio/data/meta-surfaces-textured-20261101'
        self.update('running','ingest',{},'Loading completed local experiment; no inference is running')
        for name,source in [('capture',capture),('geometry',stage/'01-mapanything'),('segmentation',stage/'02-sam-masks-refined'),('surfaces',stage/'03-surfaces-textured'),('appearance',appearance),('robot',stage/'09-urdf-layer')]:
            target=self.directory/name;target.mkdir()
            if name=='capture':shutil.copy2(source/'capture.json',target/'capture.json');shutil.copytree(source/'images',target/'images')
            elif name=='appearance':shutil.copytree(source/'output',target/'output')
            else:shutil.copytree(source,target,dirs_exist_ok=True)
            self.paths[name]=target
        previews=self.directory/'previews';previews.mkdir();self.paths['previews']=previews
        for p in stage.iterdir():
            if p.suffix in ('.mp4','.jpg','.zip'):shutil.copy2(p,previews/p.name)
        # Preserve the original uploaded media too, rather than just normalised frames.
        value=self.repo.load(self.key)
        for p in (capture/'uploads').iterdir():
            if p.is_file():
                import hashlib
                destination=self.folder/'sources'/p.name;shutil.copy2(p,destination)
                if any(s['name']==p.name for s in value['sources']):continue
                value['sources'].append({'id':'source-'+uuid4().hex[:16],'name':p.name,'file':str(destination.relative_to(self.folder)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
        self.repo.write(value)
        self.config['view_ids']=[i['name'] for i in read_json(self.paths['capture']/'capture.json')['images']]
        sam=read_json(self.paths['segmentation']/'report.json')
        if sam.get('images') and 'categories' in sam['images'][0]:
            self.config['prompts']={key:[a['prompt'] for a in entry['attempts']] for key,entry in sam['images'][0]['categories'].items()}
        keys=cache_keys(self.config,[{'name':s['name'],'sha256':s['sha256']} for s in value['sources']])
        for name,alias in [('ingest','capture'),('geometry','geometry'),('segmentation','segmentation'),('surfaces','surfaces'),('appearance','appearance'),('previews','previews')]:
            self.completed[name]={'stage':name,'key':keys[name],'path':str(self.paths[alias].relative_to(self.folder))}
        atomic_json(self.directory/'completed.json',self.completed)
        self.finish()

    def finish(self):
        self.update('running','previews',{},'Preparing editable layers, coordinate frames and mask overlays')
        cfg=copy.deepcopy(self.config)
        if cfg['surface_overrides']:cfg['canonical_from_map']=self.request['previous']['frames']['canonical_from_map']
        request=self.directory/'compose-request.json';atomic_json(request,{'folder':str(self.folder),'run':self.run,'paths':{k:str(v) for k,v in self.paths.items()},'config':cfg,'previous':self.request.get('previous')})
        self.execute([self.runtime.paths['gaussian_python'],'-m','lookandpick.workspaces.compose',request],'previews')
        result=read_json(self.directory/'result.json')
        self.update('ready','ready',{},'Workspace ready to review and save',result=str((self.directory/'result.json').relative_to(self.folder)))

    def main(self):
        def cancelled(*_):raise InterruptedError('Setup cancelled')
        signal.signal(signal.SIGTERM,cancelled)
        try:
            if self.request['mode']=='fixture':self.fixture()
            else:self.run_local()
        except InterruptedError:self.update('cancelled',message='Setup cancelled; validated stages and active workspace were preserved')
        except BaseException as exc:
            message=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else 'Local setup failed. Check disk space and the isolated runtimes, then retry; private logs are retained.'
            self.update('needs_input' if isinstance(exc,ValueError) else 'failed',message=message[:300],error=message[:300])
            raise

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--workspace',required=True);parser.add_argument('--run',required=True);a=parser.parse_args()
    Worker(a.root,a.workspace,a.run).main()

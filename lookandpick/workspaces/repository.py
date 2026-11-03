"""Atomic, managed workspace drafts, immutable versions and explicit activation."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
from uuid import uuid4
from .schema import ID,IDENTITY,validate_draft,number,multiply,robot_transform


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temp.replace(path)

def read_json(path):return json.loads(Path(path).read_text())
def fresh_id(prefix):return prefix+'-'+uuid4().hex[:16]


class Repository:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock()

    def folder(self,key):
        if not isinstance(key,str) or not ID.fullmatch(key):raise ValueError('Invalid managed ID')
        path=self.root/key
        if not path.resolve().is_relative_to(self.root):raise ValueError('Invalid workspace path')
        return path

    def load(self,key):return read_json(self.folder(key)/'workspace.json')
    def write(self,value):atomic_json(self.folder(value['id'])/'workspace.json',value)
    def list(self):
        with self.lock:
            return [self.load(p.name) for p in sorted(self.root.glob('ws-*'),key=lambda p:p.stat().st_mtime,reverse=True) if (p/'workspace.json').is_file()]

    def create(self,name):
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:raise ValueError('Workspace name needs 1–100 characters')
        key=fresh_id('ws');folder=self.folder(key);(folder/'sources').mkdir(parents=True)
        value={'schema_version':1,'id':key,'name':name.strip(),'revision':0,'sources':[],'jobs':[],'draft':None,'active_version':None,'created_at':time.time()}
        self.write(value);return value

    def resolve(self,key,relative):
        path=(self.folder(key)/relative).resolve()
        if not path.is_relative_to(self.folder(key)) or not path.is_file():raise FileNotFoundError('Workspace asset unavailable')
        return path

    def upload(self,key,name,stream,length):
        from urllib.parse import unquote
        name=unquote(name)
        if not name or Path(name).name!=name or Path(name).suffix.lower() not in {'.jpg','.jpeg','.png','.webp','.tif','.tiff','.bmp','.mp4','.mov','.mkv','.avi','.webm','.m4v','.json'}:raise ValueError('Use a simple image/video/sidecar filename')
        if type(length) is not int or not 0<length<=2*1024**3:raise ValueError('Source must be 1 byte to 2 GiB')
        with self.lock:
            value=self.load(key)
            if len(value['sources'])>=200:raise ValueError('Import at most 200 source files per workspace')
            if any(s['name']==name for s in value['sources']):raise ValueError('A source with this filename is already imported')
            if sum(s['bytes'] for s in value['sources'])+length>4*1024**3:raise ValueError('Workspace source budget is 4 GiB')
            source_id=fresh_id('source');relative='sources/'+name;path=self.folder(key)/relative;temp=path.with_name(source_id+'.upload')
            digest=hashlib.sha256();remaining=length
            try:
                with temp.open('xb') as out:
                    while remaining:
                        chunk=stream.read(min(1024**2,remaining))
                        if not chunk:raise ValueError('Source upload was interrupted')
                        out.write(chunk);digest.update(chunk);remaining-=len(chunk)
                temp.replace(path)
                value['sources'].append({'id':source_id,'name':name,'file':relative,'bytes':length,'sha256':digest.hexdigest()})
                self.write(value)
            finally:temp.unlink(missing_ok=True)
            return value

    def remove_source(self,key,source_id):
        # Preserve originals for provenance/retries; remove only from the next input selection.
        with self.lock:
            value=self.load(key);found=False
            for source in value['sources']:
                if source['id']==source_id:source['excluded']=True;found=True
            if not found:raise FileNotFoundError('Source not found')
            self.write(value);return value

    def patch(self,key,patch):
        if not isinstance(patch,dict) or set(patch)-{'name','surfaces','robot','layers','physical_width_m','working_area'}:raise ValueError('Unknown draft field')
        with self.lock:
            value=self.load(key);draft=copy.deepcopy(value['draft'])
            if draft is None:raise ValueError('Build or load a fixture before editing')
            for field in ('name','surfaces','layers','working_area'):
                if field in patch:draft[field]=patch[field]
            if 'robot' in patch:
                if not isinstance(patch['robot'],dict) or set(patch['robot'])-{'translation','rotation','scale','joint_positions','visible'}:raise ValueError('Invalid robot correction')
                draft['robot'].update(patch['robot'])
                if set(patch['robot']) & {'translation','rotation','scale'}:draft['robot']['placement_status']='illustrative'
            if 'physical_width_m' in patch:
                width=patch['physical_width_m']
                draft['calibration']['meters_per_map_unit']=None if width is None else number(width,.01,100)/draft['surfaces'][0]['width']
            if 'surfaces' in patch and patch['surfaces']!=value['draft']['surfaces']:
                draft['marker_alignment']=None;draft['robot']['placement_status']='illustrative'
            if draft.get('working_area') and 'surfaces' in patch:draft['working_area']['base']=draft['surfaces'][0]['height']
            draft=validate_draft(draft)
            scale=draft['calibration']['meters_per_map_unit'];draft['units']='relative' if scale is None else 'metres'
            draft['calibration']['scale_status']='relative' if scale is None else 'user_dimension'
            draft['calibration']['robot_alignment_status']='touch_calibrated' if draft['robot'].get('placement_status')=='touch_calibrated' else 'approximate'
            frame=copy.deepcopy(draft['frames']['canonical_from_map'])
            for i in range(3):frame[i]=[v*(scale or 1) for v in frame[i]]
            draft['frames']['workspace_from_map']=frame
            draft['robot']['workspace_from_robot_base']=robot_transform(draft['robot'],scale or 1.)
            draft['needs_rebuild']=draft['surfaces']!=draft['fitted_surfaces']
            draft['draft_revision']=draft.get('draft_revision',0)+1
            if 'working_area' in patch or 'surfaces' in patch:
                from .voxel_layout import carry_policy
                carry_policy(self,key,value['draft'],draft)
            value['draft']=draft;value['name']=draft['name'];self.write(value);return value

    def save(self,key):
        with self.lock:
            value=self.load(key);draft=value['draft']
            if not draft:raise ValueError('No workspace result to save')
            if draft.get('needs_rebuild'):raise ValueError('Rebuild the edited surfaces and appearance before saving')
            validate_draft(draft)
            for asset in draft['assets'].values():self.resolve(key,asset)
            value['revision']+=1;version=f"v{value['revision']:04d}";manifest=copy.deepcopy(draft)
            if draft.get('working_area'):
                from .voxel_layout import load_policy
                manifest['voxel_layout']=load_policy(self,key,draft)
            manifest.update(revision=value['revision'],version_id=version,saved_at=time.time())
            path=self.folder(key)/'versions'/version
            path.mkdir(parents=True,exist_ok=False);atomic_json(path/'workspace.json',manifest)
            value['latest_version']=version;self.write(value);return manifest

    def version(self,key,version):
        if not isinstance(version,str) or not ID.fullmatch(version):raise ValueError('Invalid version ID')
        return read_json(self.folder(key)/'versions'/version/'workspace.json')

    def activate(self,key,version):
        with self.lock:
            manifest=self.version(key,version)
            for asset in manifest['assets'].values():self.resolve(key,asset)
            value=self.load(key);value['active_version']=version;self.write(value)
            atomic_json(self.root/'active.json',{'workspace_id':key,'version_id':version})
            return manifest

    def active(self):
        path=self.root/'active.json'
        if not path.exists():return None
        entry=read_json(path);return self.version(entry['workspace_id'],entry['version_id'])

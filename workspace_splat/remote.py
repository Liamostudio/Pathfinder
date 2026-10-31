"""Meta SAM 3D Objects through fal's documented hosted endpoint.

No direct Meta SAM 3D API is assumed. Credentials never enter scene/status files.
"""
from __future__ import annotations
import base64
import io
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request
import numpy as np
from PIL import Image
from .formats import SH_C0,export,validate

MODEL='fal-ai/sam-3/3d-objects'
QUEUE='https://queue.fal.run/'+MODEL
MAX_ASSET_BYTES=256*1024**2


class RemoteError(RuntimeError):pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise RemoteError('Unexpected provider redirect refused')


def queue_url(value,request_id=None,action=''):
    if not isinstance(value,str):raise RemoteError('Provider returned an invalid queue URL')
    parsed=urlsplit(value)
    if parsed.scheme!='https' or parsed.netloc!='queue.fal.run' or not parsed.path.startswith('/fal-ai/sam-3/') or '/requests/' not in parsed.path or parsed.query or parsed.fragment:
        raise RemoteError('Provider returned an unexpected queue URL')
    if request_id is not None and parsed.path not in {
        f'/fal-ai/sam-3/requests/{request_id}{action}',
        f'/fal-ai/sam-3/3d-objects/requests/{request_id}{action}',
    }:raise RemoteError('Provider queue URL does not match the submitted request')
    return value


def asset_url(value):
    if not isinstance(value,str):raise RemoteError('Provider did not return a Gaussian PLY URL')
    try:
        parsed=urlsplit(value);host=parsed.hostname or '';port=parsed.port
    except ValueError:raise RemoteError('Invalid Gaussian artifact URL') from None
    if parsed.scheme!='https' or (host!='fal.media' and not host.endswith('.fal.media')) or parsed.username or parsed.password or port not in (None,443):
        raise RemoteError('Gaussian artifact must use the documented HTTPS fal.media CDN')
    return value


class FalClient:
    def __init__(self,key,*,opener=None,clock=time.monotonic,sleep=time.sleep,timeout=900):
        if not isinstance(key,str) or not key.strip() or len(key)>4096 or any(ord(c)<33 or ord(c)>126 for c in key.strip()):
            raise ValueError('Enter a valid fal API key')
        self._key=key.strip();self.opener=opener or urllib.request.build_opener(NoRedirect())
        self.clock,self.sleep,self.timeout=clock,sleep,timeout
        self.cancel_url=None

    def json(self,url,body=None,method=None,timeout=30):
        if url!=QUEUE:queue_url(url)
        data=None if body is None else json.dumps(body,allow_nan=False).encode()
        request=urllib.request.Request(url,data=data,headers={'Authorization':'Key '+self._key,'Content-Type':'application/json'},method=method)
        try:
            with self.opener.open(request,timeout=timeout) as response:
                raw=response.read(2*1024**2+1)
                if len(raw)>2*1024**2:raise RemoteError('Provider JSON response too large')
                value=json.loads(raw)
                if not isinstance(value,dict):raise RemoteError('Unexpected provider response')
                return value
        except urllib.error.HTTPError as exc:
            hints={401:'Key rejected',403:'Model access refused',429:'Rate limit reached',422:'Input rejected'}
            raise RemoteError(f'fal API: {hints.get(exc.code,"request failed")} (HTTP {exc.code})') from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise RemoteError('fal API connection failed; submission is not automatically retried to avoid duplicate charges') from None
        except (ValueError,UnicodeError):raise RemoteError('Provider returned invalid JSON') from None

    def generate(self,image,prompt,progress):
        self.cancel_url=None
        content=Path(image).read_bytes()
        if len(content)>16*1024**2:raise ValueError('Selected normalised image exceeds 16 MiB')
        payload={'image_url':'data:image/jpeg;base64,'+base64.b64encode(content).decode(),
                 'prompt':prompt,'enable_safety_checker':True,'seed':42}
        submitted=self.json(QUEUE,payload)
        request_id=submitted.get('request_id')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',request_id):raise RemoteError('Invalid provider request ID')
        self.cancel_url=queue_url(submitted.get('cancel_url'),request_id,'/cancel')
        deadline=self.clock()+self.timeout
        try:
            status_url=queue_url(submitted.get('status_url'),request_id,'/status')
            result_url=queue_url(submitted.get('response_url'),request_id)
            while self.clock()<deadline:
                status=self.json(status_url).get('status')
                if status=='COMPLETED':
                    result=self.json(result_url)
                    artifact=result.get('gaussian_splat')
                    if not isinstance(artifact,dict):raise RemoteError('Provider result has no Gaussian splat')
                    return self.download(artifact.get('url')),request_id
                if status not in ('IN_QUEUE','IN_PROGRESS'):raise RemoteError('Provider job returned an unexpected status')
                progress('remote_queued' if status=='IN_QUEUE' else 'remote_inference',{'message':'fal: queued' if status=='IN_QUEUE' else 'Meta SAM 3D Objects is generating the selected object on fal'})
                self.sleep(2)
            raise RemoteError('SAM 3D request timed out; cancellation requested')
        except BaseException:
            self.cancel()
            raise

    def cancel(self):
        if self.cancel_url:
            try:self.json(self.cancel_url,method='PUT',timeout=2)
            except Exception:pass

    def download(self,url):
        # No API key on artifact requests; never follow redirects to an arbitrary host.
        request=urllib.request.Request(asset_url(url))
        try:
            with self.opener.open(request,timeout=60) as response:
                length=response.headers.get('Content-Length')
                if length and int(length)>MAX_ASSET_BYTES:raise RemoteError('Gaussian artifact exceeds 256 MiB')
                chunks=[];total=0
                while chunk:=response.read(1024**2):
                    total+=len(chunk)
                    if total>MAX_ASSET_BYTES:raise RemoteError('Gaussian artifact exceeds 256 MiB')
                    chunks.append(chunk)
                return b''.join(chunks)
        except (urllib.error.URLError,TimeoutError,OSError,ValueError):
            raise RemoteError('Could not download the generated Gaussian artifact') from None


def read_gaussian_ply(content):
    from plyfile import PlyData
    if len(content)>MAX_ASSET_BYTES:raise RemoteError('Gaussian PLY exceeds import budget')
    end=content.find(b'end_header\n',0,1024**2)
    if end<0:end=content.find(b'end_header\r\n',0,1024**2)
    if end<0:raise RemoteError('Invalid Gaussian PLY header')
    header=content[:end].decode('ascii',errors='replace')
    if any(line.startswith('property list ') or (line.startswith('element ') and not line.startswith('element vertex ') and not line.endswith(' 0')) for line in header.splitlines()):
        raise RemoteError('Only Gaussian vertex PLY files are supported')
    declarations=[line.split() for line in header.splitlines() if line.startswith('element vertex ')]
    try:
        count=int(declarations[0][2])
        if not 1<=count<=2_000_000:raise ValueError()
        vertex=PlyData.read(io.BytesIO(content),mmap=False)['vertex'].data
        required=['x','y','z','f_dc_0','f_dc_1','f_dc_2','opacity','scale_0','scale_1','scale_2','rot_0','rot_1','rot_2','rot_3']
        if not all(name in vertex.dtype.names for name in required):raise ValueError()
        field=lambda names:np.column_stack([vertex[n] for n in names]).astype(np.float32)
        means=field(['x','y','z']);raw_scales=field(['scale_0','scale_1','scale_2'])
        raw_opacity=np.asarray(vertex['opacity'],dtype=np.float32)
        if not np.isfinite(raw_scales).all() or not np.isfinite(raw_opacity).all():raise ValueError()
        scales=np.exp(np.clip(raw_scales,-30,20))
        opacity=1/(1+np.exp(-np.clip(raw_opacity,-30,30)))
        quats=field(['rot_0','rot_1','rot_2','rot_3'])
        colors=field(['f_dc_0','f_dc_1','f_dc_2'])*SH_C0+.5
        arrays=validate(means,scales,quats,colors,opacity)
        if count>500000:
            keep=np.argpartition(arrays[4],-500000)[-500000:]
            arrays=[a[keep] for a in arrays]
        return dict(zip(('means','scales','quats','colors','opacities'),arrays))
    except (ValueError,IndexError,KeyError,TypeError,OverflowError):
        raise RemoteError('Returned PLY is not a supported finite 3D Gaussian splat') from None


def generate(capture,key,*,image_name='',prompt='table',progress=lambda stage,info:None,client=None):
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>200:raise ValueError('Enter an object prompt of 1–200 characters')
    if not isinstance(image_name,str) or (image_name and Path(image_name).name!=image_name):raise ValueError('Invalid source image name')
    capture=Path(capture)
    manifest=json.loads((capture/'capture.json').read_text())
    images=manifest['images']
    # Normalised GUI IDs take precedence over potentially colliding source names.
    image=(next((i for i in images if image_name==i['name']),None)
           or next((i for i in images if image_name==i['source']),None)) if image_name else images[0]
    if image is None:raise ValueError('Selected image is not in this capture')
    source=capture/'images'/image['name']
    progress('remote_upload',{'message':'Sending one selected image to fal.ai for Meta SAM 3D Objects','backend':'sam3d-fal','selected_image':image['name']})
    content,request_id=(client or FalClient(key)).generate(source,prompt.strip(),progress)
    progress('exporting',{'message':'Importing returned Gaussian PLY for the local viewer','backend':'sam3d-fal'})
    arrays=read_gaussian_ply(content)
    centre=np.median(arrays['means'],axis=0)
    radius=float(np.max(np.quantile(arrays['means'],.98,axis=0)-np.quantile(arrays['means'],.02,axis=0))*.5)
    if not np.isfinite(radius) or radius<1e-8:raise RemoteError('Generated geometry has degenerate bounds')
    arrays['means']=(arrays['means']-centre)/radius;arrays['scales']/=radius
    output=capture/'output';output.mkdir(exist_ok=True)
    files=export(output,**arrays)
    (output/'source-sam3d.ply').write_bytes(content)
    bounds=np.quantile(arrays['means'],[.02,.98],axis=0).tolist()
    camera={'name':'SAM 3D inspection view','kind':'inspection','width':640,'height':480,
            'K':[[600.,0.,320.],[0.,600.,240.],[0.,0.,1.]],
            'world_to_camera':[[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,3.5],[0.,0.,0.,1.]]}
    scene={'version':1,'type':'gaussian-splat','name':capture.name,'file':'scene.splat','format':'splat32',**files,
           'trained':False,'generated':True,'backend':'sam3d-fal','provider':'fal.ai','model':'Meta SAM 3D Objects',
           'model_id':MODEL,'request_id':request_id,'prompt':prompt.strip(),'selected_image':image['name'],
           'metric':False,'frame':'normalised SAM 3D prediction; arbitrary scale','normalization':{'centre':centre.tolist(),'radius':radius},
           'bounds':bounds,'cameras':[camera],'sh_degree':0,
           'warnings':['Single-image model prediction; input photos are not fused. Unseen geometry is inferred.',
                       'Inspection camera is synthetic, not a recovered input camera. Scale is not calibrated for robot control.',
                       'Meta model hosted by fal.ai; this run used fal billing, not Meta Model API credits.']}
    temp=output/'scene.json.tmp';temp.write_text(json.dumps(scene,indent=2,allow_nan=False)+'\n');temp.replace(output/'scene.json')
    progress('complete',{'message':'SAM 3D Gaussian object ready','backend':'sam3d-fal','gaussians':files['count']})
    return scene

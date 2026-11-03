"""Bring-your-own Gaussian provider using the Splat Studio HTTP v1 contract.

POST a ZIP containing capture.json and images/*.jpg with Bearer authentication.
Return a standard 3DGS PLY, or 202 {status_url: <same-origin HTTPS URL>}.
Poll returns {status: queued|processing|completed|failed, ply_url?: <same-origin>}.
No provider response body, signed URL or credential is persisted in logs.
"""
from __future__ import annotations
import ipaddress
import json
from pathlib import Path
import signal
import socket
import tempfile
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request
import zipfile
import numpy as np
from .formats import export
from .remote import NoRedirect, RemoteError, MAX_ASSET_BYTES, read_gaussian_ply


def validate_endpoint(url, *, origin=None, resolve=False):
    try:
        p=urlsplit(url)
        if (p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment
                or p.port not in (None,443) or len(url)>2048 or any(ord(c)<33 for c in url)):
            raise ValueError()
        if p.hostname.lower()=='localhost' or '.' not in p.hostname:raise ValueError()
        try:address=ipaddress.ip_address(p.hostname)
        except ValueError:address=None
        if address is not None and not address.is_global:raise ValueError()
        if origin and (p.scheme,p.hostname,p.port or 443)!=(urlsplit(origin).scheme,urlsplit(origin).hostname,urlsplit(origin).port or 443):
            raise ValueError()
        if resolve:
            addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError()
    except (TypeError,ValueError,OSError):
        raise ValueError('Use a public HTTPS endpoint on port 443; job and artifact URLs must remain on its origin') from None
    return url


class CustomClient:
    def __init__(self,config, *, opener=None,sleep=time.sleep,clock=time.monotonic,timeout=1800):
        self.endpoint=validate_endpoint(config.get('endpoint'))
        self.key=config.get('api_key','')
        if not isinstance(self.key,str) or not self.key or len(self.key)>4096 or any(ord(c)<33 or ord(c)>126 for c in self.key):
            raise ValueError('Enter a valid provider API key')
        self.model=config.get('model','')
        self.opener=opener or urllib.request.build_opener(NoRedirect())
        self.check_dns=opener is None
        self.sleep,self.clock,self.timeout=sleep,clock,timeout
        self.status_url=None

    def request(self,url, *, data=None,length=None,method=None):
        try:
            validate_endpoint(url,origin=self.endpoint,resolve=self.check_dns)
            headers={'Authorization':'Bearer '+self.key,'Accept':'application/json, application/octet-stream'}
            if data is not None:headers.update({'Content-Type':'application/zip','Content-Length':str(length)})
            request=urllib.request.Request(url,data=data,headers=headers,method=method)
            with self.opener.open(request,timeout=120) as response:
                kind=response.headers.get('Content-Type','').split(';')[0]
                limit=2*1024**2 if kind=='application/json' else MAX_ASSET_BYTES
                content=response.read(limit+1)
                if len(content)>limit:raise RemoteError('Provider response exceeds the import budget')
                if kind=='application/json':
                    result=json.loads(content)
                    if not isinstance(result,dict):raise ValueError()
                    return result
                if not content.startswith(b'ply'):raise RemoteError('Provider must return a standard Gaussian PLY or JSON job status')
                return content
        except urllib.error.HTTPError as exc:
            raise RemoteError(f'Custom provider request failed (HTTP {exc.code}); check the endpoint, model and key') from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise RemoteError('Provider connection failed. Submission was not retried; check your provider before submitting again') from None
        except (ValueError,UnicodeError):raise RemoteError('Invalid provider response or unsafe job URL') from None

    def cancel(self):
        if self.status_url:
            try:self.request(self.status_url,method='DELETE')
            except Exception:pass

    def generate(self,capture,progress):
        capture=Path(capture)
        manifest=json.loads((capture/'capture.json').read_text())
        # Stream the request from disk: a large input set never becomes one RAM buffer.
        with tempfile.TemporaryFile() as archive:
            with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr('capture.json',json.dumps({'version':1,'model':self.model,'images':manifest['images']}))
                for image in manifest['images']:bundle.write(capture/'images'/image['name'],'images/'+image['name'])
            length=archive.tell();archive.seek(0)
            progress('remote_upload',{'message':f'Sending {len(manifest["images"])} normalised frames to the configured provider'})
            result=self.request(self.endpoint,data=archive,length=length)
        if isinstance(result,bytes):return result
        try:
            self.status_url=validate_endpoint(result.get('status_url'),origin=self.endpoint)
            deadline=self.clock()+self.timeout
            while self.clock()<deadline:
                result=self.request(self.status_url)
                if isinstance(result,bytes):return result
                status=result.get('status')
                if status=='completed':
                    content=self.request(validate_endpoint(result.get('ply_url'),origin=self.endpoint))
                    if not isinstance(content,bytes):raise RemoteError('Completed job did not return a Gaussian PLY')
                    return content
                if status not in ('queued','processing'):raise RemoteError('Provider reported a failed or unknown job state')
                progress('remote_inference',{'message':f'Custom provider: {status}'})
                self.sleep(2)
            raise RemoteError('Provider timed out; cancellation requested')
        except BaseException:
            self.cancel();raise


def generate(capture,config, *, progress=lambda stage,info:None,client=None):
    client=client or CustomClient(config)
    previous=signal.getsignal(signal.SIGTERM)
    def terminate(*_):raise SystemExit(143)
    signal.signal(signal.SIGTERM,terminate)
    try:content=client.generate(capture,progress)
    finally:signal.signal(signal.SIGTERM,previous)
    arrays=read_gaussian_ply(content)
    centre=np.median(arrays['means'],axis=0)
    radius=float(np.max(np.quantile(arrays['means'],.98,axis=0)-np.quantile(arrays['means'],.02,axis=0))*.5)
    if not np.isfinite(radius) or radius<1e-8:raise RemoteError('Provider returned degenerate geometry')
    arrays['means']=(arrays['means']-centre)/radius;arrays['scales']/=radius
    capture=Path(capture);output=capture/'output';output.mkdir(exist_ok=True)
    progress('exporting',{'message':'Validating and exporting provider Gaussians'})
    products=export(output,**arrays)
    (output/'source-provider.ply').write_bytes(content)
    scene={'version':1,'type':'gaussian-splat','name':capture.name,'format':'splat32','file':'scene.splat',**products,
           'trained':False,'generated':True,'backend':'custom-http','provider':'Custom HTTP provider','model':config.get('model',''),
           'metric':False,'frame':'normalised provider output; arbitrary scale','sh_degree':0,
           'normalization':{'centre':centre.tolist(),'radius':radius},
           'bounds':np.quantile(arrays['means'],[.02,.98],axis=0).tolist(),
           'cameras':[{'name':'Inspection view','kind':'inspection','width':640,'height':480,
                       'K':[[600.,0.,320.],[0.,600.,240.],[0.,0.,1.]],
                       'world_to_camera':[[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,3.5],[0.,0.,0.,1.]]}],
           'warnings':['External provider output. Camera poses and metric scale were not recovered by this app.',
                       'Viewer exports retain degree-0 colour and up to 500,000 Gaussians.']}
    temp=output/'scene.json.tmp';temp.write_text(json.dumps(scene,indent=2,allow_nan=False));temp.replace(output/'scene.json')
    progress('complete',{'message':'Gaussian splat ready','gaussians':products['count']})
    return scene

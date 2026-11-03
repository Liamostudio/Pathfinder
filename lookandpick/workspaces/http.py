"""Workspace routes on the existing mission-control HTTP server."""
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import urlsplit,unquote
import xml.etree.ElementTree as ET
from .runtimes import ROOT


def robot_description():
    root=ET.parse(ROOT/'simulation/assets/so101/so101_new_calib.urdf').getroot()
    def origin(e):return {'xyz':[float(v) for v in e.get('xyz','0 0 0').split()],'rpy':[float(v) for v in e.get('rpy','0 0 0').split()]} if e is not None else {'xyz':[0,0,0],'rpy':[0,0,0]}
    joints=[];visuals=[]
    for j in root.findall('joint'):
        a=j.find('axis');limit=j.find('limit')
        joints.append({'name':j.get('name'),'type':j.get('type'),'parent':j.find('parent').get('link'),'child':j.find('child').get('link'),'origin':origin(j.find('origin')),
                       'axis':[float(v) for v in a.get('xyz').split()] if a is not None else [0,0,1],
                       'limit':[float(limit.get('lower')),float(limit.get('upper'))] if limit is not None and limit.get('lower') else [0,0]})
    for link in root.findall('link'):
        for visual in link.findall('visual'):
            mesh=visual.find('geometry/mesh')
            if mesh is None:continue
            visuals.append({'link':link.get('name'),'file':Path(mesh.get('filename')).name,'origin':origin(visual.find('origin')),'scale':[float(v) for v in mesh.get('scale','1 1 1').split()],
                            'color':[.835294,.992157,.317647] if visual.find('material').get('name')=='3d_printed' else [.1,.1,.1]})
    return {'base_link':'base_link','joints':joints,'visuals':visuals}


def send_file(handler,path):
    size=path.stat().st_size;start=0;end=size-1;status=200
    raw=handler.headers.get('Range')
    if raw:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',raw)
        if not match or not any(match.groups()):start=size
        elif not match[1]:start=max(0,size-int(match[2]))
        else:start=int(match[1]);end=min(size-1,int(match[2])) if match[2] else size-1
        if start>=size or start>end:
            handler.send_response(416);handler.send_header('Content-Range',f'bytes */{size}');handler.send_header('Content-Length','0');handler.end_headers();return
        status=206
    handler.send_response(status);handler.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    handler.send_header('Content-Length',str(end-start+1));handler.send_header('Accept-Ranges','bytes');handler.send_header('Cache-Control','no-store');handler.send_header('X-Content-Type-Options','nosniff')
    handler.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'self'")
    if status==206:handler.send_header('Content-Range',f'bytes {start}-{end}/{size}')
    handler.end_headers()
    with path.open('rb') as source:
        source.seek(start);remaining=end-start+1
        try:
            while remaining:
                chunk=source.read(min(65536,remaining))
                if not chunk:break
                handler.wfile.write(chunk);remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError):pass


def body(handler):
    length=int(handler.headers.get('Content-Length',0))
    if not 0<length<=128*1024:raise ValueError('Invalid workspace request size')
    if handler.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Expected application/json')
    value=json.loads(handler.rfile.read(length))
    if not isinstance(value,dict):raise ValueError('Expected JSON object')
    return value


def dispatch(handler,service,method):
    path=unquote(urlsplit(handler.path).path);parts=path.strip('/').split('/')
    if not path.startswith(('/api/workspace','/api/robot-')) and path!='/workspace-viewer.js':return False
    try:
        if method=='GET' and path=='/workspace-viewer.js':
            handler._send(200,(ROOT/'workspace_splat/web/viewer.js').read_bytes(),'text/javascript; charset=utf-8');return True
        if method=='GET' and path=='/api/workspace-measurements':
            from .placement import MARKER_MEASUREMENT
            handler._json(200,MARKER_MEASUREMENT);return True
        if method=='GET' and path=='/api/robot-description':handler._json(200,robot_description());return True
        if method=='GET' and len(parts)==3 and parts[:2]==['api','robot-assets']:
            allowed={v['file'] for v in robot_description()['visuals']}
            if parts[2] not in allowed:raise FileNotFoundError('Unknown robot visual')
            send_file(handler,ROOT/'simulation/assets/so101/assets'/parts[2]);return True
        if method=='GET' and path=='/api/workspace-capabilities':handler._json(200,service.capabilities());return True
        if path=='/api/workspaces':
            if method=='GET':handler._json(200,service.list());return True
            if method=='POST':
                payload=body(handler);value=service.repo.create(payload.get('name','My workspace'))
                if payload.get('fixture') is True:value['job']=service.start(value['id'],{'mode':'fixture'})
                handler._json(201,value);return True
        if len(parts)>=3 and parts[:2]==['api','workspace-jobs']:
            run=parts[2];key=service.locate_job(run)
            if len(parts)==3 and method=='GET':handler._json(200,service.job(key,run));return True
            if len(parts)==4 and parts[3]=='cancel' and method=='POST':body(handler);handler._json(200,service.cancel(run));return True
            if len(parts)==5 and parts[3]=='assets' and method=='GET':send_file(handler,service.asset(key,parts[4],run=run));return True
        if len(parts)>=3 and parts[:2]==['api','workspaces']:
            key=parts[2];service.repo.folder(key)
            if len(parts)==3 and method=='GET':handler._json(200,service.get(key));return True
            if len(parts)==4:
                action=parts[3]
                if action=='robot-placement' and method=='POST':
                    body(handler)
                    from .placement import measured_placement
                    with service.repo.lock:
                        value=service.repo.load(key);value['draft']=measured_placement(value['draft']);value['draft']['draft_revision']=value['draft'].get('draft_revision',0)+1;service.repo.write(value)
                    handler._json(200,service.get(key));return True
                if action=='voxels' and method=='GET':handler._json(200,service.areas.snapshot(key));return True
                if action=='voxel-observations' and method=='POST':handler._json(200,service.areas.observe(key,body(handler)));return True
                if action=='voxel-edits' and method=='POST':handler._json(200,service.areas.edit(key,body(handler)));return True
                if action=='voxel-reset' and method=='POST':body(handler);handler._json(200,service.areas.reset(key));return True
                if action=='marker-alignment' and method=='POST':handler._json(200,service.areas.align(key,body(handler)));return True
                if action=='voxel-camera' and method=='POST':handler._json(200,service.areas.register_camera(key,body(handler)));return True
                if action=='voxel-detect' and method=='POST':handler._json(200,service.areas.detect(key,body(handler)));return True
                if action=='voxel-rgb' and method=='POST':handler._json(200,service.areas.rgb(key,body(handler)));return True
                if action=='voxel-path' and method=='POST':
                    handler._json(200,service.areas.path(key,body(handler)));return True
                if action=='sources' and method=='POST':
                    handler._json(201,service.repo.upload(key,handler.headers.get('X-Filename',''),handler.rfile,int(handler.headers.get('Content-Length',0))));return True
                if action=='jobs' and method=='POST':handler._json(202,service.start(key,body(handler)));return True
                if action=='draft' and method=='PATCH':handler._json(200,service.patch(key,body(handler)));return True
                if action=='versions' and method=='POST':body(handler);handler._json(201,service.repo.save(key));return True
                if action=='activate' and method=='POST':handler._json(200,service.repo.activate(key,body(handler).get('version_id')));return True
                if action=='previews' and method=='POST':payload=body(handler);payload['mode']='previews';handler._json(202,service.start(key,payload));return True
            if len(parts)==5 and parts[3]=='sources' and method=='DELETE':handler._json(200,service.repo.remove_source(key,parts[4]));return True
            if len(parts)==5 and parts[3]=='assets' and method=='GET':send_file(handler,service.asset(key,parts[4]));return True
            if len(parts)==7 and parts[3]=='jobs' and parts[5]=='frames' and method=='GET':send_file(handler,service.frame(key,parts[4],parts[6]));return True
            if len(parts)>=5 and parts[3]=='versions':
                version=parts[4]
                if len(parts)==5 and method=='GET':handler._json(200,service.repo.version(key,version));return True
                if len(parts)==6 and parts[5] in ('voxels','voxel-observations','voxel-reset','voxel-camera','voxel-rgb','voxel-detect','voxel-path','voxel-edits'):
                    action=parts[5]
                    if action=='voxels' and method=='GET':handler._json(200,service.areas.snapshot(key,version));return True
                    if method=='POST':
                        payload=body(handler);fn={'voxel-observations':service.areas.observe,'voxel-reset':lambda k,p,v:service.areas.reset(k,v),'voxel-camera':service.areas.register_camera,'voxel-rgb':service.areas.rgb,'voxel-detect':service.areas.detect,'voxel-path':service.areas.path,'voxel-edits':service.areas.edit}.get(action)
                        if fn:handler._json(200,fn(key,payload,version));return True
                if len(parts)==6 and parts[5]=='bundle.zip' and method=='GET':send_file(handler,service.bundle(key,version));return True
                if len(parts)==7 and parts[5]=='assets' and method=='GET':send_file(handler,service.asset(key,parts[6],version=version));return True
        handler._json(404,{'error':'Unknown workspace route'})
    except FileNotFoundError:handler._json(404,{'error':'Workspace or asset not found'})
    except (ValueError,KeyError,TypeError) as exc:handler._json(400,{'error':str(exc)[:400]})
    except OSError:handler._json(500,{'error':'Workspace storage is unavailable. Check local disk space and permissions.'})
    return True

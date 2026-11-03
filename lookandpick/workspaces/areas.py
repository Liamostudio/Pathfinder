"""Workspace-owned working areas, CPU marker registration and live occupancy API."""
import base64
import copy
import json
import math
from pathlib import Path
import subprocess
import threading
import time
from uuid import uuid4
from .repository import atomic_json,read_json
from .schema import number,transform,vector
from .voxels import Grid,Occupancy,signature


class WorkAreas:
    def __init__(self,service):
        self.service=service;self.maps={};self.cameras={};self.lock=threading.RLock()
        from .detector_client import DetectorClient
        self.detector=DetectorClient()

    def manifest(self,key,version=None):
        value=self.service.repo.version(key,version) if version else self.service.get(key)['draft']
        if value is None:raise ValueError('Load or build a workspace first')
        return value

    def occupancy(self,key,version=None):
        manifest=self.manifest(key,version);area=manifest.get('working_area');anchor=manifest.get('marker_alignment')
        if not area or not area['enabled']:raise ValueError('Draw and enable a working area first')
        revision=signature(area,anchor);identity=(key,version)
        with self.lock:
            if identity not in self.maps and len(self.maps)>=16:self.maps.pop(next(iter(self.maps)))
            if identity not in self.maps or self.maps[identity].config_revision!=revision:self.maps[identity]=Occupancy(Grid(area),revision)
            from .voxel_layout import load_policy
            policy=load_policy(self.service.repo,key,manifest);mapping=self.maps[identity]
            with mapping.lock:
                mapping.manual_blocked=set(policy['blocked']);mapping.policy_revision=policy['revision'];mapping.geometry_id=policy['geometry_id']
            return mapping,anchor

    def snapshot(self,key,version=None):
        manifest=self.manifest(key,version)
        if not manifest.get('working_area') or not manifest['working_area']['enabled']:return {'enabled':False,'states':[],'counts':{'free':0,'occupied':0,'unknown':0}}
        mapping,anchor=self.occupancy(key,version);return {'enabled':True,'alignment':anchor['status'] if anchor else 'unanchored',**mapping.snapshot()}

    def observe(self,key,payload,version=None):
        mapping,anchor=self.occupancy(key,version)
        result=mapping.observe(payload,anchor)
        active=self.service.repo.active()
        if version is None and active and active['id']==key and signature(active.get('working_area'),active.get('marker_alignment'))==mapping.config_revision:
            other,_=self.occupancy(key,active['version_id']);other.observe({**payload,'revision':other.revision},anchor)
        return result

    def edit(self,key,payload,version=None):
        from .voxel_layout import load_policy,write_policy
        with self.service.repo.lock:
            manifest=self.manifest(key,version);mapping,_=self.occupancy(key,version)
            if payload.get('revision')!=mapping.revision:raise ValueError('Working area changed; select the voxel again')
            policy=load_policy(self.service.repo,key,manifest)
            if type(payload.get('policy_revision')) is not int or payload['policy_revision']!=policy['revision']:raise ValueError('Voxel policy changed; refresh and retry the edit')
            indices=payload.get('indices');blocked=payload.get('blocked')
            if type(blocked) is not bool:raise ValueError('Choose blocked true or false')
            indices=mapping._indices(indices);result=set(policy['blocked'])
            result=result|indices if blocked else result-indices
            if result!=set(policy['blocked']):
                policy.update(blocked=sorted(result),revision=policy['revision']+1)
                write_policy(self.service.repo,key,manifest,policy)
        return self.snapshot(key,version)

    def reset(self,key,version=None):
        with self.lock:
            self.maps.pop((key,version),None)
            active=self.service.repo.active()
            if version is None and active and active['id']==key:self.maps.pop((key,active['version_id']),None)
        return self.snapshot(key,version)


    def path(self,key,payload,version=None):
        mapping,anchor=self.occupancy(key,version);frame=payload.get('frame')
        start,end=vector(payload.get('start')),vector(payload.get('end'));radius=number(payload.get('radius',0),0,1)
        if frame=='marker_metres':
            if not anchor or payload.get('calibration_id')!=anchor['id']:raise ValueError('Path must reference this workspace’s ArUco calibration')
            start=transform(anchor['canonical_from_marker'],start);end=transform(anchor['canonical_from_marker'],end);radius*=anchor['canonical_units_per_metre']
        elif frame!='canonical':raise ValueError('Specify path frame canonical or marker_metres explicitly')
        return {**mapping.check_path(start,end,radius),'input_frame':frame}

    def run_cpu(self,key,request):
        directory=self.service.repo.folder(key)/'alignment-attempts'/uuid4().hex;directory.mkdir(parents=True)
        output=directory/'result.json';request['output']=str(output);path=directory/'request.json';atomic_json(path,request)
        try:
            process=subprocess.run([self.service.runtime.paths['gaussian_python'],'-m','lookandpick.workspaces.marker_alignment',str(path)],capture_output=True,text=True,timeout=15)
        except (OSError,subprocess.TimeoutExpired):raise ValueError('Marker processing could not run. Check the local image-processing runtime') from None
        if process.returncode or not output.exists():raise ValueError('Marker processing failed. Check the local OpenCV runtime')
        result=read_json(output)
        if 'error' in result:raise ValueError(result['error'])
        return result['result']

    def align(self,key,payload):
        manifest=self.manifest(key);view=payload.get('view_id');marker=payload.get('marker_id',0)
        if view not in [i['name'] for i in manifest['sources']]:raise ValueError('Choose an original workspace source view')
        if type(marker) is not int or not 0<=marker<50:raise ValueError('Use a DICT_4X4_50 marker ID between 0 and 49')
        size=number(payload.get('marker_size_m'),.01,1)
        if type(payload.get('marker_size_measured',False)) is not bool:raise ValueError('Invalid measured-size flag')
        intrinsics=payload.get('intrinsics')
        self.validate_intrinsics(intrinsics)
        if intrinsics is not None and (not isinstance(intrinsics,dict) or 'camera_matrix' not in intrinsics):raise ValueError('Intrinsics must contain camera_matrix and optional dist for this original image resolution')
        cfg={'view_id':view,'marker_id':marker,'dictionary':'DICT_4X4_50','marker_size_m':size,'marker_size_measured':payload.get('marker_size_measured',False),'intrinsics':intrinsics}
        image=self.service.asset(key,'source-'+view);frames=self.service.asset(key,'camera-frames.json')
        revision=manifest['run_id'];anchor=self.run_cpu(key,{'image':str(image),'frames':str(frames),'manifest':manifest,'config':cfg})
        with self.service.repo.lock:
            value=self.service.repo.load(key)
            if value['draft']['run_id']!=revision:raise ValueError('Workspace changed during alignment; try again')
            value['draft']['marker_alignment']=anchor;value['draft']['draft_revision']=value['draft'].get('draft_revision',0)+1;self.service.repo.write(value)
        self.reset(key);return self.service.get(key)

    def register_camera(self,key,payload,version=None):
        manifest=self.manifest(key,version);anchor=manifest.get('marker_alignment')
        if not anchor:raise ValueError('Anchor the workspace to an original-image ArUco marker first')
        data=payload.get('jpeg')
        if not isinstance(data,str) or len(data)>180000:raise ValueError('Use a JPEG frame below 130 KiB')
        try:raw=base64.b64decode(data,validate=True)
        except ValueError:raise ValueError('Invalid JPEG encoding') from None
        if not raw.startswith(b'\xff\xd8'):raise ValueError('Expected JPEG image')
        folder=self.service.repo.folder(key)/'camera-captures';folder.mkdir(exist_ok=True);path=folder/(uuid4().hex+'.jpg');path.write_bytes(raw)
        self.validate_intrinsics(payload.get('intrinsics'))
        pose=self.run_cpu(key,{'mode':'camera','image':str(path),'alignment':anchor,'intrinsics':payload.get('intrinsics')})
        camera='camera-'+uuid4().hex[:16]
        with self.lock:
            if len(self.cameras)>=16:self.cameras.pop(next(iter(self.cameras)))
            self.cameras[camera]={'key':key,'version':version,'pose':pose,'alignment':anchor['id'],'recorded':payload.get('recorded',False)}
        return {'camera_id':camera,'calibration_id':anchor['id'],'reprojection_px':pose['reprojection_px'],'width':pose['width'],'height':pose['height'],'recorded':payload.get('recorded',False)}

    def rgb(self,key,payload,version=None):
        mapping,anchor=self.occupancy(key,version);camera=self.cameras.get(payload.get('camera_id'))
        if not anchor or not camera or (camera['key'],camera['version'],camera['alignment'])!=(key,version,anchor['id']):raise ValueError('Register this fixed camera against the workspace marker first')
        regions=payload.get('detections',[])
        if not isinstance(regions,list) or len(regions)>64:raise ValueError('Use at most 64 foreground regions')
        boxes=[]
        for detection in regions:
            if not isinstance(detection,dict) or detection.get('kind') not in ('hand','person'):continue
            region=detection.get('box')
            if not isinstance(region,list) or len(region)!=4:raise ValueError('Use normalised [left, top, right, bottom] regions')
            box=[number(v,0,1) for v in region]
            if box[0]>=box[2] or box[1]>=box[3]:raise ValueError('Invalid foreground region bounds')
            padding=.02;boxes.append([max(0,box[0]-padding),max(0,box[1]-padding),min(1,box[2]+padding),min(1,box[3]+padding)])
        pose=camera['pose'];K=pose['camera_matrix'];occupied=[]
        bounds_cache=camera.setdefault('projected',{})
        cached=bounds_cache.get(mapping.revision)
        if cached is not None:
            occupied=[i for i,bounds in enumerate(cached) if bounds and any(bounds[0]<=b[2] and bounds[2]>=b[0] and bounds[1]<=b[3] and bounds[3]>=b[1] for b in boxes)]
            return self.observe(key,{'revision':payload.get('revision'),'sequence':payload.get('sequence'),'source':payload.get('source'),'mode':'rgb_regions','recorded':camera.get('recorded',False),'captured_at_ms':payload.get('captured_at_ms'),'calibration_id':anchor['id'],'occupied':occupied},version)
        projected=[]
        for index in range(mapping.grid.count):
            pixels=[]
            for corner in mapping.grid.corners(index):
                p=transform(anchor['marker_from_canonical'],corner);c=[sum(a*b for a,b in zip(row,p))+pose['translation'][i] for i,row in enumerate(pose['rotation'])]
                if c[2]<=.001:continue
                x,y=c[0]/c[2],c[1]/c[2];d=pose['dist']+[0]*14;k1,k2,p1,p2,k3,k4,k5,k6=d[:8];r=x*x+y*y
                denominator=1+k4*r+k5*r*r+k6*r*r*r
                if abs(denominator)<1e-9:continue
                factor=(1+k1*r+k2*r*r+k3*r*r*r)/denominator
                xx=x*factor+2*p1*x*y+p2*(r+2*x*x);yy=y*factor+p1*(r+2*y*y)+2*p2*x*y
                pixels.append(((K[0][0]*xx+K[0][1]*yy+K[0][2])/pose['width'],(K[1][1]*yy+K[1][2])/pose['height']))
            if len(pixels)!=8:projected.append(None);continue
            bounds=[min(p[0] for p in pixels),min(p[1] for p in pixels),max(p[0] for p in pixels),max(p[1] for p in pixels)]
            projected.append(bounds)
            if any(bounds[0]<=b[2] and bounds[2]>=b[0] and bounds[1]<=b[3] and bounds[3]>=b[1] for b in boxes):occupied.append(index)
        bounds_cache.clear();bounds_cache[mapping.revision]=projected
        return self.observe(key,{'revision':payload.get('revision'),'sequence':payload.get('sequence'),'source':payload.get('source'),'mode':'rgb_regions','recorded':camera.get('recorded',False),'captured_at_ms':payload.get('captured_at_ms'),'calibration_id':anchor['id'],'occupied':occupied},version)

    def detect(self,key,payload,version=None):
        mapping,anchor=self.occupancy(key,version);camera=self.cameras.get(payload.get('camera_id'))
        if not camera or (camera['key'],camera['version'])!=(key,version):raise ValueError('Register this camera against the marker first')
        jpeg=payload.get('jpeg')
        if not isinstance(jpeg,str) or len(jpeg)>120000:raise ValueError('Use a JPEG frame below 90 KiB')
        started=time.monotonic();result=self.detector.detect(jpeg,source=payload['camera_id'])
        if (result['width'],result['height'])!=(camera['pose']['width'],camera['pose']['height']):raise ValueError('Camera resolution changed; register the camera again')
        if time.monotonic()-started>mapping.grid.area['ttl_ms']/1000:raise ValueError('Detector warming up or frame stale; waiting for a fresh frame')
        snapshot=self.rgb(key,{**payload,'detections':result['detections']},version)
        return {**snapshot,**result}

    @staticmethod
    def validate_intrinsics(value):
        if value is None:return
        if not isinstance(value,dict) or set(value)-{'camera_matrix','dist'}:raise ValueError('Expected camera_matrix and optional OpenCV dist coefficients')
        matrix=value.get('camera_matrix')
        if not isinstance(matrix,list) or len(matrix)!=3 or any(not isinstance(row,list) or len(row)!=3 for row in matrix):raise ValueError('camera_matrix must be 3 × 3')
        for row in matrix:
            for item in row:number(item,-100000,100000)
        if matrix[0][0]<=0 or matrix[1][1]<=0 or matrix[2]!=[0,0,1]:raise ValueError('Invalid camera focal lengths or final matrix row')
        dist=value.get('dist',[])
        if not isinstance(dist,list) or len(dist) not in (0,4,5,8):raise ValueError('Use 0, 4, 5 or 8 OpenCV distortion coefficients')
        for item in dist:number(item,-100,100)

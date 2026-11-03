"""Bounded table volumes and fail-closed occupancy. Coordinates are explicit.

This is a visual/development map, not physical robot safety acceptance. Free
space must be observed explicitly by a calibrated 3D adapter; RGB regions only
add conservative occupied cells. A missing observation never means free space.
"""
import copy
import hashlib
import json
import math
import threading
import time
from uuid import uuid4
from .schema import number,vector,transform

MAX_CELLS=4096


def validate_area(area):
    if area is None:return None
    fields={'enabled','centre','width','depth','height','base','yaw','cell_size','ttl_ms','hold_ms','margin'}
    if not isinstance(area,dict) or set(area)!=fields:raise ValueError('Invalid working-area settings')
    a=copy.deepcopy(area)
    if type(a['enabled']) is not bool:raise ValueError('Invalid working-area visibility')
    a['centre']=vector(a['centre'],2)
    for key in ('width','depth','height'):number(a[key],.005,10)
    number(a['base'],-100,100);number(a['yaw'],-360,360);number(a['cell_size'],.005,1)
    number(a['ttl_ms'],100,2000);number(a['hold_ms'],0,5000);number(a['margin'],0,.25)
    dimensions=[max(1,math.ceil(a[k]/a['cell_size']-1e-6)) for k in ('width','depth','height')]
    if math.prod(dimensions)>MAX_CELLS:raise ValueError(f'Working area exceeds {MAX_CELLS:,} voxels; increase cube size or reduce the volume')
    # Preserve the chosen workspace boundary; edge cells are clipped to it.
    return a


def signature(area,alignment=None):
    return hashlib.sha256(json.dumps([area,alignment],sort_keys=True,separators=(',',':')).encode()).hexdigest()[:24]


class Grid:
    def __init__(self,area):
        self.area=validate_area(area);a=self.area;self.size=a['cell_size']
        self.shape=tuple(max(1,math.ceil(a[k]/self.size-1e-6)) for k in ('width','depth','height'));self.count=math.prod(self.shape)
        angle=math.radians(a['yaw']);self.c=math.cos(angle);self.s=math.sin(angle)

    def local(self,p):
        dx,dy=p[0]-self.area['centre'][0],p[1]-self.area['centre'][1]
        return [self.c*dx+self.s*dy+self.area['width']/2,-self.s*dx+self.c*dy+self.area['depth']/2,p[2]-self.area['base']]

    def world(self,p):
        x,y=p[0]-self.area['width']/2,p[1]-self.area['depth']/2
        return [self.area['centre'][0]+self.c*x-self.s*y,self.area['centre'][1]+self.s*x+self.c*y,self.area['base']+p[2]]

    def index(self,ix,iy,iz):return (iz*self.shape[1]+iy)*self.shape[0]+ix
    def coordinates(self,index):return index%self.shape[0],index//self.shape[0]%self.shape[1],index//(self.shape[0]*self.shape[1])
    def centre(self,index):
        corners=self.corners(index);return [(a+b)/2 for a,b in zip(corners[0],corners[6])]
    def corners(self,index):
        base=self.coordinates(index)
        return [self.world([min(self.area[('width','depth','height')[j]],(base[j]+v[j])*self.size) for j in range(3)]) for v in ((0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1))]

    def cells_at(self,point,radius=0):
        p=self.local(point);ranges=[]
        for i,n in enumerate(self.shape):
            # Expand by a tiny cell-relative epsilon so rotations cannot drop
            # a touching cube through floating-point round-off.
            lo,hi=math.floor((p[i]-radius)/self.size-1e-9),math.floor((p[i]+radius)/self.size+1e-9)
            if hi<0 or lo>=n or p[i]-radius>self.area[('width','depth','height')[i]]+1e-9:return set()
            ranges.append(range(max(0,lo),min(n-1,hi)+1))
        return {self.index(x,y,z) for z in ranges[2] for y in ranges[1] for x in ranges[0]}

    def path_cells(self,start,end,radius=0):
        """Conservative swept sphere against each cube using slab intersections."""
        a,b=self.local(start),self.local(end);result=[]
        if any(min(a[i],b[i])-radius<0 or max(a[i],b[i])+radius>self.area[('width','depth','height')[i]] for i in range(3)):return None
        for index in range(self.count):
            cell=self.coordinates(index);lo,hi=0.,1.
            for i in range(3):
                low=cell[i]*self.size-radius;high=min(self.area[('width','depth','height')[i]],(cell[i]+1)*self.size)+radius;d=b[i]-a[i]
                if abs(d)<1e-12:
                    if not low<=a[i]<=high:lo=2;break
                else:
                    x,y=(low-a[i])/d,(high-a[i])/d;lo=max(lo,min(x,y));hi=min(hi,max(x,y))
            if lo<=hi:result.append(index)
        return result


class Occupancy:
    def __init__(self,grid,revision,clock=time.monotonic):
        self.grid=grid;self.config_revision=revision;self.revision=revision+'-'+uuid4().hex[:8];self.clock=clock;self.observations={};self.sources={};self.sequence=-1;self.manual_blocked=set();self.policy_revision=0;self.geometry_id=None;self.lock=threading.RLock()

    def observe(self,payload,alignment):
        with self.lock:
            if not isinstance(payload,dict) or payload.get('revision')!=self.revision:raise ValueError('Working area changed; fetch the current voxel revision')
            seq=payload.get('sequence');source=payload.get('source')
            if type(seq) is not int or seq<0 or not isinstance(source,str) or not 1<=len(source)<=80:raise ValueError('Use a source name and increasing sequence number')
            if seq<=self.sources.get(source,{}).get('sequence',-1):raise ValueError('Out-of-order occupancy observation')
            mode=payload.get('mode')
            if mode not in ('points','rgb_regions','simulation'):raise ValueError('Expected points, rgb_regions or simulation occupancy')
            if mode!='simulation':
                captured=number(payload.get('captured_at_ms'),0,1e15);age=time.time()*1000-captured
                if age>self.grid.area['ttl_ms'] or age< -50:raise ValueError('Observation is stale or its capture clock is invalid')
            if mode!='simulation' and (not alignment or payload.get('calibration_id')!=alignment['id']):raise ValueError('Observation must reference this workspace’s ArUco alignment')
            if source not in self.sources and len(self.sources)>=16:raise ValueError('Use at most 16 occupancy sources')
            if self.sources and any((s['mode']=='simulation')!=(mode=='simulation') for s in self.sources.values()):raise ValueError('Reset observations before switching between simulation and live input')
            if type(payload.get('recorded',False)) is not bool:raise ValueError('Invalid recorded-source flag')
            now=self.clock();free=set();occupied=set();g=self.grid
            if mode=='rgb_regions':
                # Projected regions are pre-expanded conservatively by the camera adapter.
                occupied=self._indices(payload.get('occupied',[]))
                if payload.get('free'):raise ValueError('RGB regions cannot certify empty 3D cells')
            else:
                points=payload.get('points',[])
                if not isinstance(points,list) or len(points)>20000:raise ValueError('Use at most 20,000 bounded 3D points per observation')
                if mode=='points' and payload.get('kind') not in ('hand','person'):raise ValueError('Only person/hand observations are enabled')
                if mode=='points' and payload.get('frame')!='marker_metres':raise ValueError('3D observations must use marker_metres')
                radius=number(payload.get('radius',0),0,.25)
                for point in points:
                    p=vector(point)
                    if mode=='points':p=transform(alignment['canonical_from_marker'],p)
                    scale=alignment['canonical_units_per_metre'] if mode=='points' else 1
                    occupied.update(g.cells_at(p,(radius*scale)+g.area['margin']))
                free=self._indices(payload.get('free',[]))
                if mode=='points' and free and not alignment.get('metric_inputs_complete'):raise ValueError('Free-space evidence requires measured marker size and calibrated camera intrinsics')
            for i in free-occupied:
                old=self.observations.get((source,i))
                if not old or old[0]!='occupied' or now>=old[2]:self.observations[source,i]=('free',now,now,mode,source)
            for i in occupied:self.observations[source,i]=('occupied',now,now+g.area['hold_ms']/1000,mode,source)
            self.sources[source]={'sequence':seq,'received':now,'mode':mode,'recorded':payload.get('recorded',False)};self.sequence+=1
            return self.snapshot()

    def _indices(self,values):
        if not isinstance(values,list) or len(values)>MAX_CELLS or any(type(i) is not int or not 0<=i<self.grid.count for i in values):raise ValueError('Invalid voxel IDs')
        return set(values)

    def snapshot(self):
        with self.lock:
            now=self.clock();ttl=self.grid.area['ttl_ms']/1000;valid_for=ttl;states=['unknown']*self.grid.count;modes=set()
            for (_,index),(state,observed,hold,mode,source) in self.observations.items():
                if now-observed<=ttl:
                    if states[index]!='occupied':states[index]=state
                    modes.add(mode)
                    if state=='free':valid_for=min(valid_for,max(0,observed+ttl-now))
                elif state=='occupied' and now<hold:states[index]='occupied';modes.add(mode)
            observed_states=list(states)
            for index in self.manual_blocked:states[index]='occupied'
            counts={s:states.count(s) for s in ('free','occupied','unknown')}
            return {'revision':self.revision,'shape':self.grid.shape,'cell_size':self.grid.size,'states':states,'observed_states':observed_states,'manual_blocked':sorted(self.manual_blocked),'policy_revision':self.policy_revision,'geometry_id':self.geometry_id,'counts':counts,'sequence':self.sequence,
                    'simulation':bool(modes) and modes=={'simulation'},'has_simulated_evidence':'simulation' in modes,'has_recorded_evidence':any(s.get('recorded') and now-s['received']<=max(ttl,self.grid.area['hold_ms']/1000) for s in self.sources.values()),
                    'fresh':any(now-v['received']<=ttl for v in self.sources.values()),'sources':[{k:v for k,v in s.items() if k!='received'}|{'name':name,'age_ms':round((now-s['received'])*1000)} for name,s in self.sources.items()],
                    'motion_ready':False,'generated_at_ms':int(time.time()*1000),'valid_for_ms':int(valid_for*1000),'note':'Visual development map. Unknown cells are unsafe. Robot-base alignment and full-arm collision checks remain required.'}

    def check_path(self,start,end,radius=0):
        start,end=vector(start),vector(end);radius=number(radius,0,1)
        cells=self.grid.path_cells(start,end,radius);snap=self.snapshot()
        if cells is None:return {'allowed':False,'reason':'outside_working_area','blocked_cells':[],'physical_motion_authorised':False}
        blocked=[i for i in cells if snap['states'][i]!='free']
        # Simulation evidence must never authorise a live adapter.
        return {'allowed':not blocked,'reason':'unsafe_or_unknown_voxels' if blocked else 'observed_free_swept_sphere', 'blocked_cells':blocked,'simulation':snap['has_simulated_evidence'],'physical_motion_authorised':False}

"""Durable operator restrictions, separate from sensor evidence and scene assets."""
import hashlib
import json
import math
from .voxels import Grid

GEOMETRY=('centre','width','depth','height','base','yaw','cell_size')


def geometry_id(manifest):
    area=manifest['working_area']
    value=[{k:area[k] for k in GEOMETRY},manifest['frames']['canonical_from_map']]
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:24]


def policy_path(repo,key,manifest):return repo.folder(key)/'voxel-layouts'/(geometry_id(manifest)+'.json')


def load_policy(repo,key,manifest):
    from .repository import read_json
    identity=geometry_id(manifest);path=policy_path(repo,key,manifest)
    fallback=manifest.get('voxel_layout',{})
    value=read_json(path) if path.exists() else fallback if fallback.get('geometry_id')==identity else {}
    blocked=value.get('blocked',[]);count=Grid(manifest['working_area']).count
    if not isinstance(blocked,list) or any(type(i) is not int or not 0<=i<count for i in blocked):raise ValueError('Stored voxel restrictions do not match this volume')
    return {'geometry_id':identity,'revision':value.get('revision',0),'blocked':sorted(set(blocked))}


def write_policy(repo,key,manifest,value):
    from .repository import atomic_json
    atomic_json(policy_path(repo,key,manifest),value)


def remap_blocked(old_area,new_area,blocked):
    """Conservative coverage: every new cube intersecting an old blocked cube."""
    old,new=Grid(old_area),Grid(new_area);result=set()
    for index in blocked:
        corners=[new.local(p) for p in old.corners(index)];ranges=[]
        for axis,n in enumerate(new.shape):
            lo=math.floor(min(p[axis] for p in corners)/new.size+1e-7)
            hi=math.ceil(max(p[axis] for p in corners)/new.size-1e-7)-1
            ranges.append(range(max(0,lo),min(n-1,hi)+1))
        result.update(new.index(x,y,z) for z in ranges[2] for y in ranges[1] for x in ranges[0])
    return result


def carry_policy(repo,key,previous,current):
    if not previous.get('working_area') or not current.get('working_area'):return
    if geometry_id(previous)==geometry_id(current):return
    before=load_policy(repo,key,previous);after=load_policy(repo,key,current)
    carried=remap_blocked(previous['working_area'],current['working_area'],before['blocked'])
    # Existing restrictions on a previously used resolution are retained too.
    blocked=sorted(carried|set(after['blocked']))
    if blocked:write_policy(repo,key,current,{**after,'revision':after['revision']+1,'blocked':blocked})

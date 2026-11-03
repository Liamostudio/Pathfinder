"""Versioned visual workspace schema; no live calibration or arm commands."""
import copy
import hashlib
import json
import math
import re

PIPELINE = 'meta-planar-workspace-v1'
MAP_REVISION = '00f9c245bbcb60522d1ed7f9e9d88462c6e3f38a'
MAP_SOURCE = '3d10cf7a3016fc0f9bb13a071ee66c47b10be0d9'
SAM_REVISION = 'daa63191845a41281374e725f4c9e51c7a824460'
STAGES = ('ingest','geometry','segmentation','surfaces','appearance','previews')
IDENTITY = [[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,0.],[0.,0.,0.,1.]]
ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
DEFAULT_PROMPTS = {'table':['table'],'wall':['wall'],'robot':['robot arm','robotic arm','robot'],'tripod':['tripod'],'chair':['chair']}

def number(value, low=-10000, high=10000):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(f'Expected a finite number between {low} and {high}')
    return float(value)

def vector(value, size=3):
    if not isinstance(value,list) or len(value)!=size:raise ValueError(f'Expected {size} coordinates')
    return [number(v) for v in value]

def multiply(a,b):return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
def transform(matrix,point):return [sum(matrix[i][j]*point[j] for j in range(3))+matrix[i][3] for i in range(3)]
def dot(a,b):return sum(x*y for x,y in zip(a,b))
def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
def normal(a):return [v/math.sqrt(dot(a,a)) for v in a]

def canonical_frame(table):
    z=normal(table['normal']);origin=table['origin']
    corners=[[origin[j]+p[0]*table['axis_u'][j]+p[1]*table['axis_v'][j] for j in range(3)] for p in table['footprint']]
    edges=[[b-a for a,b in zip(corners[i],corners[(i+1)%len(corners)])] for i in range(len(corners))]
    x=normal(max(edges,key=lambda e:dot(e,e)));y=normal(cross(z,x));x=normal(cross(y,z))
    centre=[sum(p[i] for p in corners)/len(corners) for i in range(3)]
    matrix=[[ *axis,-dot(axis,centre)] for axis in (x,y,z)]+[[0.,0.,0.,1.]]
    points=[transform(matrix,p) for p in corners]
    width=max(p[0] for p in points)-min(p[0] for p in points);depth=max(p[1] for p in points)-min(p[1] for p in points)
    return matrix,{'name':'table','enabled':True,'centre':[0.,0.,0.],'width':width,'depth':depth,'yaw':0.,'height':0.}

def settings(value=None):
    value=value or {};result={'pipeline_version':PIPELINE,'video_fps':2.,'video_frames':80,'view_ids':[], 'max_views':4,'max_edge':1280,'resolution':518,'steps':8000,'seed':0,'prompts':copy.deepcopy(DEFAULT_PROMPTS),'reference_view':None,'mask_edits':{},'surface_overrides':None,'preview_seconds':4,'preview_orbit':False}
    if not isinstance(value,dict) or set(value)-set(result):raise ValueError('Unknown reconstruction setting')
    result.update(copy.deepcopy(value))
    result['video_fps']=number(result['video_fps'],.1,30)
    for key,low,high in [('video_frames',2,200),('max_views',2,4),('steps',100,12000),('max_edge',256,1280),('resolution',256,518),('preview_seconds',1,12)]:
        if type(result[key]) is not int or not low<=result[key]<=high:raise ValueError(f'{key} must be an integer in [{low}, {high}]')
    if result['seed']!=0:raise ValueError('This pipeline currently uses seed 0')
    if not isinstance(result['view_ids'],list) or len(set(result['view_ids']))!=len(result['view_ids']) or any(not isinstance(s,str) or not re.fullmatch(r'\d{5}\.jpg',s) for s in result['view_ids']):raise ValueError('Invalid selected view IDs')
    if result['view_ids'] and not 2<=len(result['view_ids'])<=4:raise ValueError('Select 2–4 views; larger joint inference has not been profiled')
    if result['reference_view'] is not None and not re.fullmatch(r'\d{5}\.jpg',str(result['reference_view'])):raise ValueError('Invalid reference view')
    if not isinstance(result['prompts'],dict) or set(result['prompts'])!=set(DEFAULT_PROMPTS):raise ValueError('Configure table, wall, robot, tripod and chair prompts')
    for prompts in result['prompts'].values():
        if not isinstance(prompts,list) or not 1<=len(prompts)<=4 or any(not isinstance(p,str) or not 1<=len(p)<=100 for p in prompts):raise ValueError('Use 1–4 text prompts per category')
    if not isinstance(result['mask_edits'],dict) or len(result['mask_edits'])>4:raise ValueError('Invalid mask edits')
    for name,categories in result['mask_edits'].items():
        if not re.fullmatch(r'\d{5}\.jpg',name) or not isinstance(categories,dict) or set(categories)-set(DEFAULT_PROMPTS):raise ValueError('Invalid mask edit view/category')
        for polygons in categories.values():
            if not isinstance(polygons,list) or len(polygons)>8:raise ValueError('Use up to 8 mask polygons')
            for polygon in polygons:
                if not isinstance(polygon,list) or not 3<=len(polygon)<=64:raise ValueError('A mask polygon needs 3–64 points')
                for point in polygon:
                    if not isinstance(point,list) or len(point)!=2:raise ValueError('Mask points use normalised x/y coordinates')
                    for v in point:number(v,0,1)
    if result['surface_overrides'] is not None:validate_surfaces(result['surface_overrides'])
    if type(result['preview_orbit']) is not bool:raise ValueError('Invalid preview orbit flag')
    return result

def validate_surfaces(surfaces):
    if not isinstance(surfaces,list) or not 1<=len(surfaces)<=4:raise ValueError('Expected a table and up to three walls')
    for i,s in enumerate(surfaces):
        if s.get('name')!=('table' if i==0 else f'wall-{i}'):raise ValueError('Invalid surface order')
        vector(s['centre']);number(s['width'],.01,100);number(s['depth'],.01,100);number(s['yaw'],-360,360);number(s['height'])
        if type(s['enabled']) is not bool:raise ValueError('Invalid surface enabled flag')
    if not surfaces[0]['enabled']:raise ValueError('The tabletop must be enabled')

def cache_keys(config,sources):
    def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    previous=digest({'sources':sources,'pipeline':PIPELINE});keys={}
    deps={'ingest':['video_fps','video_frames','max_edge'], 'geometry':['view_ids','max_views','resolution'], 'segmentation':['prompts','mask_edits'], 'surfaces':['reference_view','surface_overrides'], 'appearance':['steps','seed'], 'previews':['preview_seconds','preview_orbit']}
    # Segmentation depends on selected image pixels, not geometry estimation settings.
    for stage,fields in deps.items():
        parent=keys['ingest'] if stage=='segmentation' else previous
        if stage=='surfaces':parent=[keys['geometry'],keys['segmentation']]
        previous=digest({'parent':parent,'settings':{k:config[k] for k in fields},'models':[MAP_REVISION,MAP_SOURCE,SAM_REVISION]})
        keys[stage]=previous
    keys['segmentation']=digest({'ingest':keys['ingest'],'views':config['view_ids'],'max_views':config['max_views'],'prompts':config['prompts'],'edits':config['mask_edits'],'sam':SAM_REVISION})
    keys['surfaces']=digest([keys['geometry'],keys['segmentation'],config['reference_view'],config['surface_overrides']])
    keys['appearance']=digest([keys['surfaces'],config['steps'],config['seed']])
    keys['previews']=digest([keys['appearance'],config['preview_seconds'],config['preview_orbit']])
    return keys

def robot_transform(robot,scale=1.):
    x,y,z=[math.radians(v) for v in robot['rotation']];cx,cy,cz=map(math.cos,(x,y,z));sx,sy,sz=map(math.sin,(x,y,z))
    matrix=[[cz*cy,cz*sy*sx-sz*cx,cz*sy*cx+sz*sx,robot['translation'][0]],
            [sz*cy,sz*sy*sx+cz*cx,sz*sy*cx-cz*sx,robot['translation'][1]],[-sy,cy*sx,cy*cx,robot['translation'][2]],[0.,0.,0.,1.]]
    for i in range(3):
        for j in range(3):matrix[i][j]*=robot['scale']*scale
        matrix[i][3]*=scale
    return matrix

def validate_draft(value):
    result=copy.deepcopy(value)
    if 'working_area' in result:
        from .voxels import validate_area
        result['working_area']=validate_area(result['working_area'])
    if result.get('schema_version')!=1:raise ValueError('Unsupported workspace schema')
    if not isinstance(result['name'],str) or not 1<=len(result['name'].strip())<=100:raise ValueError('Workspace name needs 1–100 characters')
    validate_surfaces(result['surfaces'])
    for k in ['translation','rotation']:vector(result['robot'][k])
    number(result['robot']['scale'],.01,100)
    from .http import robot_description
    limits={j['name']:j['limit'] for j in robot_description()['joints'] if j['type'] in ('revolute','continuous')}
    if not isinstance(result['robot']['joint_positions'],dict):raise ValueError('Expected named SO-101 visual joints')
    for name,angle in result['robot']['joint_positions'].items():
        if name not in limits:raise ValueError('Unknown SO-101 visual joint')
        number(angle,*limits[name])
    scale=result['calibration']['meters_per_map_unit']
    if scale is not None:number(scale,.001,1000)
    result['robot']['workspace_from_robot_base']=robot_transform(result['robot'],scale or 1.)
    for key in ['background','surfaces','observed','robot']:
        if type(result['layers'][key]) is not bool:raise ValueError('Invalid visibility flag')
    return result

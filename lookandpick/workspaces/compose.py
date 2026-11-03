"""CPU composition/export adapter. The browser retains the URDF hierarchy."""
import argparse
import copy
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation
from .repository import atomic_json,read_json
from .schema import PIPELINE,MAP_REVISION,MAP_SOURCE,SAM_REVISION,IDENTITY,transform,robot_transform
from .geometry import review_assets
from .runtimes import ROOT


def robot_description():
    root=ET.parse(ROOT/'simulation/assets/so101/so101_new_calib.urdf').getroot()
    def origin(element):
        return {'xyz':[float(v) for v in element.get('xyz','0 0 0').split()], 'rpy':[float(v) for v in element.get('rpy','0 0 0').split()]} if element is not None else {'xyz':[0,0,0],'rpy':[0,0,0]}
    joints=[];visuals=[]
    for j in root.findall('joint'):
        limit=j.find('limit');axis=j.find('axis')
        joints.append({'name':j.get('name'),'type':j.get('type'),'parent':j.find('parent').get('link'),'child':j.find('child').get('link'),
                       'origin':origin(j.find('origin')),'axis':[float(v) for v in axis.get('xyz').split()] if axis is not None else [0,0,1],
                       'limit':[float(limit.get('lower')),float(limit.get('upper'))] if limit is not None and limit.get('lower') is not None else [0,0]})
    for link in root.findall('link'):
        for v in link.findall('visual'):
            mesh=v.find('geometry/mesh')
            if mesh is None:continue
            visuals.append({'link':link.get('name'),'file':Path(mesh.get('filename')).name,'origin':origin(v.find('origin')),
                            'scale':[float(x) for x in mesh.get('scale','1 1 1').split()],
                            'color':[.835294,.992157,.317647] if v.find('material').get('name')=='3d_printed' else [.1,.1,.1]})
    return {'asset_id':'so101','base_link':'base_link','joints':joints,'visuals':visuals,'placement_status':'illustrative'}


def compose(folder,run,paths,config,previous=None):
    output=folder/'runs'/run/'review';review_assets(paths['capture'],paths['geometry'],paths['segmentation'],paths['surfaces'],output)
    layout=read_json(output/'layout.json')
    if config.get('surface_overrides'):
        layout={'canonical_from_map':config['canonical_from_map'],'surfaces':config['surface_overrides']};atomic_json(output/'layout.json',layout)
    scene=read_json(paths['appearance']/'output/scene.json');surface=read_json(paths['surfaces']/'report.json')
    base=np.array(layout['canonical_from_map']);robot_folder=paths.get('robot')
    angles={'shoulder_pan':0.,'shoulder_lift':-.35,'elbow_flex':.65,'wrist_flex':.35,'wrist_roll':0.,'gripper':.8}
    robot={'asset_id':'so101','translation':[layout['surfaces'][0]['width']*.35,0.,0.],'rotation':[0.,0.,180.],'scale':1.,
           'joint_positions':angles,'placement_status':'illustrative','workspace_from_robot_base':copy.deepcopy(IDENTITY)}
    if robot_folder and (robot_folder/'report.json').is_file():
        r=read_json(robot_folder/'report.json');m=base@np.array(r['robot_to_map_transform']);s=np.linalg.norm(m[:3,0])
        robot.update(translation=m[:3,3].tolist(),rotation=Rotation.from_matrix(m[:3,:3]/s).as_euler('xyz',degrees=True).tolist(),scale=float(s),joint_positions=r['joint_angles_rad'])
    if previous:robot=previous['robot']
    robot['workspace_from_robot_base']=robot_transform(robot)
    def rel(path):return str(path.relative_to(folder))
    assets={ 'scene.json':rel(paths['appearance']/'output/scene.json'),'scene.splat':rel(paths['appearance']/'output/scene.splat'),
             'scene.ply':rel(paths['appearance']/'output/scene.ply'),'surfaces.glb':rel(paths['surfaces']/'workspace-surfaces.glb'),
             'observed.ply':rel(paths['surfaces']/'observed-objects.ply'),'observed.json':rel(output/'observed.json')}
    previews=[]
    for p in sorted(paths['previews'].glob('*')):
        if p.suffix in ('.mp4','.jpg','.zip'):
            assets[p.name]=rel(p);previews.append({'id':p.name,'name':p.stem.replace('-',' '),'kind':'video' if p.suffix=='.mp4' else 'image' if p.suffix=='.jpg' else 'bundle','sh_degree':1 if p.suffix=='.mp4' and 'appearance' in p.name else None})
    sources=[]
    for image in read_json(paths['capture']/'capture.json')['images']:
        assets['source-'+image['name']]=rel(paths['capture']/'images'/image['name'])
        assets['mask-'+image['name']]=rel(output/(Path(image['name']).stem+'-mask.jpg'))
        sources.append(image)
    with np.load(paths['geometry']/'predictions.npz',allow_pickle=False) as data:
        atomic_json(output/'camera-frames.json',{k:data[k].tolist() for k in ('extrinsics','intrinsics','full_intrinsics','image_transforms')})
    assets['camera-frames.json']=rel(output/'camera-frames.json')
    draft={'schema_version':1,'id':folder.name,'name':read_json(folder/'workspace.json')['name'],'revision':0,'units':'relative',
        'calibration':{'scale_status':'relative','robot_alignment_status':'approximate','meters_per_map_unit':None},
        'frames':{'canonical_from_map':layout['canonical_from_map'],'workspace_from_map':layout['canonical_from_map'],'gaussian_normalization':scene['normalization'],
                  'camera_convention':'OpenCV +X right, +Y down, +Z forward; world-to-camera extrinsics','image_transform':'K_model = image_transform @ K_full'},
        'assets':assets,'surfaces':layout['surfaces'],'fitted_surfaces':copy.deepcopy(layout['surfaces']),'robot':robot,
        'source_files':[{k:s[k] for k in ('name','sha256','bytes','excluded') if k in s} for s in read_json(folder/'workspace.json')['sources']],
        'layers':previous['layers'] if previous else {'background':True,'surfaces':True,'observed':False,'robot':True},'sources':sources,
        'models':{'geometry':{'id':'facebook/map-anything-apache','revision':MAP_REVISION,'source_revision':MAP_SOURCE},'segmentation':{'id':'facebook/sam3.1','revision':SAM_REVISION}},
        'settings':{k:v for k,v in config.items() if k!='canonical_from_map'},'previews':previews,'pipeline_version':PIPELINE,'run_id':run,'needs_rebuild':False,
        'warnings':surface['assumptions']+scene.get('warnings',[])+['CAD pose is illustrative; no live robot calibration or physical motion is enabled.','Photographic inspection uses degree-zero colour; full-SH videos use degree-one colour.','Observed object samples are partial and may contain duplicates.'],
        'evaluation':scene['training'].get('view_metrics',[])}

    if previous and previous.get('marker_alignment') and previous['frames']['canonical_from_map']==layout['canonical_from_map']:
        draft['marker_alignment']=copy.deepcopy(previous['marker_alignment'])
        from .placement import measured_placement
        try:draft=measured_placement(draft)
        except (ValueError,KeyError,FileNotFoundError):pass
    return draft


def main():
    parser=argparse.ArgumentParser();parser.add_argument('request',type=Path);args=parser.parse_args();request=read_json(args.request)
    folder=Path(request['folder']);draft=compose(folder,request['run'],{k:Path(v) for k,v in request['paths'].items()},request['config'],request.get('previous'))
    atomic_json(args.request.with_name('result.json'),draft)

if __name__=='__main__':main()

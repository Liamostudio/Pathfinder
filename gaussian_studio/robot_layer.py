"""Compose the existing SO-101 CAD asset as an explicitly uncalibrated layer.

This exports geometry only. It never imports arm drivers or commands hardware.
"""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation
from .depth_estimate import unproject


def origin_matrix(element):
    matrix=np.eye(4)
    if element is not None:
        matrix[:3,3]=np.fromstring(element.get('xyz','0 0 0'),sep=' ')
        matrix[:3,:3]=Rotation.from_euler('xyz',np.fromstring(element.get('rpy','0 0 0'),sep=' ')).as_matrix()
    return matrix


def load_robot_parts(source,angles):
    import trimesh
    root=ET.parse(source).getroot();joints=root.findall('joint');transforms={'base_link':np.eye(4)}
    pending=list(joints)
    while pending:
        progressed=False
        for joint in pending[:]:
            parent=joint.find('parent').get('link');child=joint.find('child').get('link')
            if parent not in transforms:continue
            motion=np.eye(4);angle=angles.get(joint.get('name'),0.)
            if joint.get('type') in ('revolute','continuous'):
                axis=np.fromstring(joint.find('axis').get('xyz'),sep=' ')
                limit=joint.find('limit')
                if limit is not None and not float(limit.get('lower'))<=angle<=float(limit.get('upper')):raise ValueError('Illustrative pose violates URDF limits')
                motion[:3,:3]=Rotation.from_rotvec(axis/np.linalg.norm(axis)*angle).as_matrix()
            transforms[child]=transforms[parent]@origin_matrix(joint.find('origin'))@motion
            pending.remove(joint);progressed=True
        if not progressed:raise ValueError('URDF joint graph is not connected')
    materials={m.get('name'):np.fromstring(m.find('color').get('rgba'),sep=' ') for m in root.findall('material')}
    materials['3d_printed']=np.array([.835294,.992157,.317647,1])
    result=[]
    for link in root.findall('link'):
        for index,visual in enumerate(link.findall('visual')):
            mesh_element=visual.find('geometry/mesh')
            if mesh_element is None:continue
            mesh=trimesh.load(source.parent/mesh_element.get('filename'),force='mesh')
            mesh.apply_scale(np.fromstring(mesh_element.get('scale','1 1 1'),sep=' '))
            mesh.apply_transform(transforms[link.get('name')]@origin_matrix(visual.find('origin')))
            material=visual.find('material');colour=materials.get(material.get('name'),np.array([.4,.4,.4,1]))
            mesh.visual.vertex_colors=np.uint8(np.tile(colour,(len(mesh.vertices),1))*255)
            result.append((f'{link.get("name")}-{index}',mesh,colour))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--geometry',required=True,type=Path);parser.add_argument('--surfaces',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output directory')
    import trimesh
    source=Path(__file__).resolve().parents[1]/'simulation/assets/so101/so101_new_calib.urdf'
    angles=dict(zip(('shoulder_pan','shoulder_lift','elbow_flex','wrist_flex','wrist_roll','gripper'),(0,-.35,.65,.35,0,.8)))
    parts=load_robot_parts(source,angles)
    with np.load(args.geometry/'predictions.npz',allow_pickle=False) as saved:data={k:saved[k] for k in saved.files}
    with np.load(args.surfaces/'masks.npz',allow_pickle=False) as saved:mask=saved['robot']
    valid=mask&data['model_mask']&np.isfinite(data['depth'])&(data['depth']>0)
    observed=np.concatenate([unproject(d,k,e)[m] for d,k,e,m in zip(data['depth'],data['intrinsics'],data['extrinsics'],valid)])
    layout=json.loads((args.surfaces/'report.json').read_text());table=layout['planes'][0]
    normal=np.array(table['normal']);offset=table['offset'];heights=observed@normal+offset
    low=np.percentile(heights,30);base=np.median(observed[heights<=low],axis=0);base-=normal*(base@normal+offset)
    x=np.array(table['origin'])-base;x-=normal*(x@normal);x/=np.linalg.norm(x);y=np.cross(normal,x)
    rotation=np.stack([x,y,normal],axis=1)
    vertices=np.concatenate([mesh.vertices for _,mesh,_ in parts]);cad_height=np.ptp(vertices[:,2])
    scale=float(np.clip((np.percentile(heights,95)-np.percentile(heights,5))/cad_height,.5,2.))
    transform=np.eye(4);transform[:3,:3]=rotation*scale;transform[:3,3]=base
    args.output.mkdir(parents=True);scene=trimesh.Scene();points=[];colours=[]
    total_area=sum(mesh.area for _,mesh,_ in parts)
    for index,(name,mesh,colour) in enumerate(parts):
        count=max(200,int(140000*mesh.area/total_area))
        samples,face_index=trimesh.sample.sample_surface(mesh,count,seed=index)
        surface_normals=mesh.face_normals[face_index]@rotation.T
        light=np.array([.25,-.35,.9]);light/=np.linalg.norm(light)
        shade=.65+.35*np.abs(surface_normals@light)
        points.append(samples@transform[:3,:3].T+base);colours.append(colour[:3]*shade[:,None])
        mesh.apply_transform(transform);scene.add_geometry(mesh,node_name=name,geom_name=name)
    scene.export(args.output/'robot-layer.glb')
    background=trimesh.load(args.surfaces/'workspace-surfaces.glb',force='scene')
    for name,mesh in background.geometry.items():scene.add_geometry(mesh,node_name='workspace-'+name,geom_name='workspace-'+name)
    scene.export(args.output/'workspace-with-robot.glb')
    np.savez_compressed(args.output/'robot-points.npz',points=np.concatenate(points),colors=np.concatenate(colours))
    metadata={'source_urdf':str(source),'joint_angles_rad':angles,'robot_to_map_transform':transform.tolist(),
        'scale_factor':scale,'base_position':base.tolist(),'metric':False,
        'notes':['Illustrative URDF pose and approximate placement; pose was NOT recovered from the photographs.',
                 'Relative scale inferred from noisy robot depth extent, not physical calibration.',
                 'Robot is a separate known CAD asset; no SAM 3D completion or hardware motion was used.']}
    (args.output/'report.json').write_text(json.dumps(metadata,indent=2))
    print(json.dumps({'stage':'complete','robot_points':sum(map(len,points)),'scale':scale,'base':base.tolist()}),flush=True)


if __name__=='__main__':main()

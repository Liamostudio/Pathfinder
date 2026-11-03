"""CPU ArUco adapter operating on original source pixels, never splat textures."""
import argparse
import json
from pathlib import Path
import uuid
import numpy as np
import cv2
from lookandpick.calibration import TableCalibration,detect_marker
from .repository import atomic_json,read_json


def estimate(image,frames,manifest,config):
    source=config['view_id'];index=next(i for i,s in enumerate(manifest['sources']) if s['name']==source)
    corners=detect_marker(image,config['marker_id'],config['dictionary'])
    if corners is None:raise ValueError('ArUco marker not found in this original image. Choose a sharper source view and check marker ID/dictionary.')
    supplied=config.get('intrinsics')
    K=np.asarray(supplied['camera_matrix'] if supplied else frames['full_intrinsics'][index],dtype=float)
    dist=np.asarray(supplied.get('dist',[]),dtype=float) if supplied else None
    if dist is not None and not dist.size:dist=None
    calibration=TableCalibration.from_marker(corners,config['marker_size_m'],K,dist)
    if calibration.reprojection_px>3:raise ValueError('Marker reprojection error exceeds 3 px; check the marker, lens calibration and source view')
    world_to_camera=np.asarray(frames['extrinsics'][index]);canonical_from_map=np.asarray(manifest['frames']['canonical_from_map'])
    if world_to_camera.shape==(3,4):world_to_camera=np.vstack([world_to_camera,[0,0,0,1]])
    canonical_from_camera=canonical_from_map@np.linalg.inv(world_to_camera)
    rotation=canonical_from_camera[:3,:3]@calibration._rotation()
    up=float(rotation[2,2])
    if up<np.cos(np.deg2rad(20)):raise ValueError('Marker plane and reconstructed tabletop differ by more than 20°. Check table fitting and marker placement.')
    camera=canonical_from_camera[:3,3];marker_camera=calibration.camera_position();direction=rotation@marker_camera
    base=manifest['surfaces'][0]['height']
    if abs(direction[2])<.02:raise ValueError('Marker is too close to an edge-on view for reliable table anchoring')
    scale=(camera[2]-base)/direction[2]
    if not np.isfinite(scale) or not .001<scale<1000:raise ValueError('Marker scale disagrees with the reconstructed tabletop')
    matrix=np.eye(4);matrix[:3,:3]=rotation*scale;matrix[:3,3]=camera-rotation@marker_camera*scale
    # This maps the marker pose into the visual reconstruction. It is not a
    # measured robot-base transform or an independent physical accuracy check.
    return {'id':'aruco-'+uuid.uuid4().hex[:16],'marker_id':config['marker_id'],'dictionary':config['dictionary'],'marker_size_m':config['marker_size_m'],
            'marker_size_measured':config['marker_size_measured'],'source_view':source,'source_sha256':manifest['sources'][index]['sha256'],
            'canonical_from_marker':matrix.tolist(),'marker_from_canonical':np.linalg.inv(matrix).tolist(),'canonical_units_per_metre':float(scale),
            'status':'marker_observed_visual_alignment','intrinsics_source':'user_calibrated' if supplied else 'reconstruction_estimate',
            'metric_inputs_complete':bool(supplied and config['marker_size_measured']),'robot_alignment_status':'unverified',
            'camera':{'camera_matrix':K.tolist(),'rvec':calibration.rvec.tolist(),'tvec':calibration.tvec.tolist(),'dist':dist.tolist() if dist is not None else None,'width':image.shape[1],'height':image.shape[0]},
            'corners_px':corners.tolist(),'reprojection_px':calibration.reprojection_px,'normal_agreement':up,
            'note':'Original-image ArUco observation. Visual map alignment inherits reconstruction error; independently validate marker/table and robot-base transforms before physical use.'}


def camera_pose(image,alignment,intrinsics=None):
    corners=detect_marker(image,alignment['marker_id'],alignment['dictionary'])
    if corners is None:raise ValueError('Marker is not visible in this camera frame')
    # Intrinsics belong to this camera and resolution, not the reconstruction camera.
    if not intrinsics:raise ValueError('Supply calibrated intrinsics for this live camera and image resolution')
    K=np.asarray(intrinsics['camera_matrix'],float);dist=intrinsics.get('dist')
    c=TableCalibration.from_marker(corners,alignment['marker_size_m'],K,dist)
    if c.reprojection_px>3:raise ValueError('Live marker reprojection error exceeds 3 px')
    return {'camera_matrix':K.tolist(),'rotation':c._rotation().tolist(),'translation':c.tvec.tolist(),'dist':c.dist.tolist() if c.dist is not None else [],'width':image.shape[1],'height':image.shape[0],'reprojection_px':c.reprojection_px}


def main():
    p=argparse.ArgumentParser();p.add_argument('request',type=Path);a=p.parse_args();r=read_json(a.request)
    try:
        image=cv2.imread(r['image'])
        if image is None:raise ValueError('Cannot decode the original camera image')
        if r.get('mode')=='camera':result=camera_pose(image,r['alignment'],r.get('intrinsics'))
        else:result=estimate(image,read_json(r['frames']),r['manifest'],r['config'])
        atomic_json(r['output'],{'result':result})
    except (ValueError,KeyError,TypeError,cv2.error) as e:atomic_json(r['output'],{'error':str(e)[:300]})

if __name__=='__main__':main()

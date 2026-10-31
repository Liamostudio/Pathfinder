"""CPU COLMAP camera/structure recovery; no guessed camera poses on failure."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pycolmap


def reconstruct(capture, *, seed=0, progress=lambda stage,info:None):
    capture=Path(capture)
    manifest=json.loads((capture/'capture.json').read_text())
    database=capture/'colmap.db'
    sparse=capture/'sparse';sparse.mkdir(exist_ok=True)
    progress('features',{'message':'Extracting CPU SIFT features'})
    extraction=pycolmap.FeatureExtractionOptions()
    extraction.num_threads=8;extraction.max_image_size=1600
    extraction.sift.max_num_features=12000
    extraction.sift.estimate_affine_shape=True
    extraction.sift.domain_size_pooling=True
    reader=pycolmap.ImageReaderOptions();reader.camera_model='SIMPLE_RADIAL'
    pycolmap.extract_features(database,capture/'images',camera_mode=pycolmap.CameraMode.AUTO,
                             reader_options=reader,extraction_options=extraction,device=pycolmap.Device.cpu)
    progress('matching',{'message':'Matching overlapping views and rejecting geometric outliers'})
    matching=pycolmap.FeatureMatchingOptions();matching.num_threads=8;matching.guided_matching=True
    if len(manifest['images'])<=80:
        pycolmap.match_exhaustive(database,matching_options=matching,device=pycolmap.Device.cpu)
    else:
        pairing=pycolmap.SequentialPairingOptions();pairing.overlap=15
        pycolmap.match_sequential(database,matching_options=matching,pairing_options=pairing,device=pycolmap.Device.cpu)
    progress('reconstruction',{'message':'Solving camera poses and sparse 3D structure'})
    options=pycolmap.IncrementalPipelineOptions();options.num_threads=8;options.random_seed=seed
    options.min_model_size=2;options.max_num_models=3
    options.mapper.init_min_num_inliers=40
    options.mapper.init_min_tri_angle=4
    options.triangulation.ignore_two_view_tracks=False
    options.mapper.abs_pose_min_num_inliers=20
    options.max_runtime_seconds=180
    maps=pycolmap.incremental_mapping(database,capture/'images',sparse,options=options)
    if not maps:
        raise ValueError('COLMAP could not reconstruct a connected scene. Capture more overlapping views, keep the robot/props still and avoid blank walls filling the frame.')
    best=max(maps.values(),key=lambda m:(m.num_reg_images(),m.num_points3D()))
    registered=best.num_reg_images();total=len(manifest['images'])
    if registered<2 or best.num_points3D()<30:
        raise ValueError('Insufficient reliable geometry: need at least two registered cameras and 30 triangulated points.')
    model_path=capture/'reconstruction';model_path.mkdir(exist_ok=True);best.write(model_path)
    progress('undistorting',{'message':f'{registered}/{total} cameras registered; undistorting training images'})
    undistorted=capture/'undistorted'
    pycolmap.undistort_images(undistorted,model_path,capture/'images',num_threads=8)
    model=pycolmap.Reconstruction(undistorted/'sparse')
    points=np.array([p.xyz for p in model.points3D.values()],dtype=np.float32)
    colors=np.array([p.color for p in model.points3D.values()],dtype=np.float32)/255
    errors=np.array([p.error for p in model.points3D.values()])
    tracks=np.array([p.track.length() for p in model.points3D.values()])
    keep=np.isfinite(points).all(1)&(errors<4)&(tracks>=2)
    points,colors=points[keep],colors[keep]
    centre=np.median(points,axis=0)
    radii=np.linalg.norm(points-centre,axis=1)
    keep=radii<=np.quantile(radii,.98)
    points,colors=points[keep],colors[keep]
    if len(points)<30:raise ValueError('Geometry filtering left too few reliable points')
    cameras=[]
    for image in sorted(model.images.values(),key=lambda im:im.name):
        camera=model.cameras[image.camera_id]
        pose=np.eye(4);pose[:3]=image.cam_from_world().matrix()
        cameras.append({'name':image.name,'width':camera.width,'height':camera.height,'K':camera.calibration_matrix().tolist(),
                        'world_to_camera':pose.tolist()})
    np.savez_compressed(capture/'initial.npz',means=points,colors=colors)
    report={'registered':registered,'total':total,'points':len(points),'mean_reprojection_error_px':float(best.compute_mean_reprojection_error()),
            'cameras':cameras,'frame':'COLMAP arbitrary scale','metric':False,'seed':seed,
            'warnings':manifest['warnings']+[f'{total-registered} input views were not registered.'] if registered<total else manifest['warnings']}
    (capture/'reconstruction.json').write_text(json.dumps(report,indent=2)+'\n')
    progress('reconstructed',{'message':f'{len(points):,} points, {registered}/{total} registered views'})
    return report

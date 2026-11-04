import copy
import json
import math
import cv2
import numpy as np
import pytest
from lookandpick.workspaces.table_appearance import registration,build
from lookandpick.workspaces.placement import measured_placement,calibrated_joints
from lookandpick.workspaces.schema import robot_transform,IDENTITY
from lookandpick.kinematics import BaseFrame
from lookandpick.workspace_bridge import robot_placement
from lookandpick.calibration import detect_marker


def manifest():
    return {'surfaces':[{'name':'table','enabled':True,'centre':[.1,-.1,0],'width':.8,'depth':.5,'height':0,'yaw':23}],
            'marker_alignment':{'canonical_from_marker':IDENTITY,'marker_size_m':.12},'robot':{'joint_positions':{}},'calibration':{},'warnings':[]}


def test_marker_plane_uvs_land_on_rotated_quad_without_shear():
    m=manifest();r=registration(m);x0,x1,y0,y1=r['extent']
    for p,(u,v) in zip(r['corners'],r['uv']):assert [x0+u*(x1-x0),y1-v*(y1-y0)]==pytest.approx(p[:2])
    for a,b,expected in [(0,1,.8),(1,2,.5)]:assert np.linalg.norm(np.array(r['corners'][a])-r['corners'][b])==pytest.approx(expected)


def test_synthetic_marker_rectifies_into_registered_quad(tmp_path):
    plane=np.full((600,900,3),180,np.uint8);marker=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),0,120);plane[240:360,390:510]=cv2.cvtColor(marker,cv2.COLOR_GRAY2BGR)
    src=np.float32([[0,0],[900,0],[900,600],[0,600]]);dst=np.float32([[180,40],[850,140],[890,580],[20,500]])
    photo=cv2.warpPerspective(plane,cv2.getPerspectiveTransform(src,dst),(950,650));path=tmp_path/'photo.png';cv2.imwrite(str(path),photo)
    m=manifest();m['surfaces'][0].update(centre=[0,0,0],yaw=0,width=1.5,depth=1.2);before=copy.deepcopy(m)
    out=tmp_path/'out.png';r=build({'manifest':m,'sources':[str(path)],'output':str(out)});image=cv2.imread(str(out));p=np.asarray(detect_marker(image)).reshape(4,2)
    assert np.linalg.norm(p-np.roll(p,-1,axis=0),axis=1)*r['mm_per_px']==pytest.approx([120]*4,abs=2)
    assert cv2.imread(str(out),cv2.IMREAD_UNCHANGED)[0,0,3]==0 # unseen is wood fallback, never stretched
    assert m==before and r['frame']=='marker_metres'


def test_visual_foot_and_pan_preserve_calibrated_upper_arm_without_mutating_motion_frame():
    path='calibration/so101-touch-2026-11-03.json';data=json.load(open(path));v=data['visual_alignment'];assert v['foot_yaw_marker_deg']==180
    assert v['pan_draw_offset_deg']==pytest.approx(data['base_frame']['yaw_deg']-180)
    m=manifest();before=copy.deepcopy(m);placed=measured_placement(m,path);assert m==before
    assert placed['robot']['rotation'][2]==pytest.approx(180)
    joints=calibrated_joints([{'joint':'shoulder_pan','reading':0}],path)
    assert math.degrees(joints['shoulder_pan'])==pytest.approx(v['pan_draw_offset_deg'])
    assert placed['robot']['rotation'][2]+math.degrees(joints['shoulder_pan'])==pytest.approx(BaseFrame.from_calibration(path).yaw_deg)


def test_cache_and_http_are_read_only_and_bound_to_workspace(tmp_path):
    import sys
    from lookandpick.workspaces.service import WorkspaceService
    from lookandpick.workspaces.runtimes import Runtimes
    from lookandpick.workspaces.repository import atomic_json
    from tests.workspaces.test_workspace_http import request
    service=WorkspaceService(tmp_path,Runtimes({'gaussian_python':sys.executable}));w=service.repo.create('Appearance');folder=service.repo.folder(w['id'])
    image=np.full((600,800,3),180,np.uint8);marker=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),0,120);image[240:360,340:460]=cv2.cvtColor(marker,cv2.COLOR_GRAY2BGR);cv2.imwrite(str(folder/'source.png'),image)
    m=manifest();m.update(assets={'source-frame.png':'source.png'},id=w['id']);w['draft']=m;service.repo.write(w)
    before=(folder/'workspace.json').read_bytes();root=f'/api/workspaces/{w["id"]}'
    metadata=request(service,root+'/table-texture.json');assert metadata.status==200
    png=request(service,root+'/table-texture.png');assert png.status==200 and png.wfile.getvalue().startswith(b'\x89PNG')
    assert (folder/'workspace.json').read_bytes()==before
    (folder/'versions/v0001').mkdir(parents=True);atomic_json(folder/'versions/v0001/workspace.json',m)
    assert request(service,root+'/versions/v0001/table-texture.json').status==200
    assert request(service,root+'/versions/../../table-texture.png').status in (400,404)
    assert request(service,root+'/table-texture.json',headers={'Origin':'https://bad.example'}).status==403
    m['marker_alignment']=None;w['draft']=m;service.repo.write(w)
    assert request(service,root+'/table-texture.json').status==400


def test_table_mask_preserves_printed_marker_but_removes_raised_objects(tmp_path):
    image=np.full((600,800,3),180,np.uint8);marker=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),0,120);image[240:360,340:460]=cv2.cvtColor(marker,cv2.COLOR_GRAY2BGR)
    source=tmp_path/'source.png';cv2.imwrite(str(source),image)
    table=np.ones((600,800),np.uint8);table[220:380,320:480]=0 # SAM may exclude paper; this is still the same flat plane.
    robot=np.zeros_like(table);robot[50:100,50:100]=1;mask=tmp_path/'source.npz';np.savez(mask,table=table,robot=robot)
    m=manifest();m['surfaces'][0].update(centre=[0,0,0],yaw=0,width=.8,depth=.6)
    out=tmp_path/'texture.png';build({'manifest':m,'sources':[str(source)],'masks':{'source.png':str(mask)},'output':str(out)})
    rgba=cv2.imread(str(out),cv2.IMREAD_UNCHANGED)
    assert detect_marker(cv2.cvtColor(rgba,cv2.COLOR_BGRA2BGR)) is not None
    assert rgba[100,100,3]==0 and rgba[rgba.shape[0]//2,rgba.shape[1]//2,3]==255


def test_texture_uses_the_registered_marker_id(tmp_path):
    image=np.full((400,400,3),180,np.uint8);marker=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),7,120);image[140:260,140:260]=cv2.cvtColor(marker,cv2.COLOR_GRAY2BGR)
    source=tmp_path/'seven.png';cv2.imwrite(str(source),image);m=manifest();m['marker_alignment']['marker_id']=7
    out=tmp_path/'out.png';build({'manifest':m,'sources':[str(source)],'output':str(out)})
    assert detect_marker(cv2.imread(str(out)),7) is not None

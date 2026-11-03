import numpy as np
from lookandpick.workspaces.geometry import edited_rectangle,apply_masks
from lookandpick.workspaces.schema import IDENTITY
from gaussian_studio.stage_video import camera_path
from lookandpick.workspaces.repository import atomic_json,read_json


def test_edited_table_plane_respects_exact_width_depth_yaw_and_height():
    surface={'name':'table','centre':[.2,.3,.4],'height':.4,'width':1.2,'depth':.8,'yaw':35.,'enabled':True}
    points,faces,info,normal,offset=edited_rectangle(surface,IDENTITY,.05)
    assert np.allclose(points[:,2],.4) and np.allclose(points@normal+offset,0,atol=1e-7)
    u=np.array(info['axis_u']);v=np.array(info['axis_v'])
    assert np.isclose(np.ptp(points@u),1.2) and np.isclose(np.ptp(points@v),.8)
    assert faces.min()>=0 and faces.max()<len(points)


def test_camera_path_is_explicit_and_identical_between_comparison_stages():
    poses=np.tile(np.eye(4,dtype=np.float32),(4,1,1));poses[:,0,3]=[-1,-2,-3,-4]
    data={'extrinsics':poses,'intrinsics':np.tile([[100.,0,50],[0,100,40],[0,0,1]],(4,1,1)),'depth':np.ones((4,80,100))}
    a=camera_path(data,16,200,160,order=[0,1,2,3,0]);b=camera_path(data,16,200,160,order=[0,1,2,3,0])
    assert np.allclose(a[0][0],poses[0])
    for (p,k),(q,l) in zip(a,b):assert np.allclose(p,q) and np.allclose(k,l)


def test_model_crop_transform_preserves_camera_rays():
    full=np.array([[600.,0,640],[0,600,480],[0,0,1]])
    crop_resize=np.array([[.4,0,-30],[0,.4,-20],[0,0,1]])
    model=crop_resize@full;pixel=np.array([700,300,1.])
    assert np.allclose(np.linalg.solve(model,crop_resize@pixel),np.linalg.solve(full,pixel))


def test_manual_mask_replacement_is_bounded_and_recorded(tmp_path):
    values={k:np.ones((20,30),dtype=bool) for k in ['table','wall','robot','tripod','chair']}
    np.savez_compressed(tmp_path/'00000.npz',**values);atomic_json(tmp_path/'report.json',{'notes':['No manual corrections applied.']})
    edits={'00000.jpg':{'robot':[[[0,0],[.5,0],[.5,.5],[0,.5]]]}}
    apply_masks(tmp_path,edits)
    with np.load(tmp_path/'00000.npz') as data:
        assert 0<data['robot'].sum()<20*30 and data['table'].all()
    report=read_json(tmp_path/'report.json');assert report['mask_edits']==edits
    assert 'No manual corrections applied.' not in report['notes']

import json
import numpy as np
import pytest
from workspace_splat.depth_guidance import sampling_grid,load_guidance,depth_loss


def test_sampling_grid_accounts_for_crop_and_resize():
    depth_K=np.array([[2,0,1],[0,2,1],[0,0,1]],dtype=np.float32)
    image_K=np.array([[4,0,4],[0,4,3],[0,0,1]],dtype=np.float32)
    grid,inside=sampling_grid(depth_K,image_K,(3,3),(7,9))
    xy=(grid+1)*[8,6]/2
    assert np.allclose(xy[1,1],[4,3])
    assert np.allclose(xy[0,0],[2,1]) and inside.all()


def test_log_depth_loss_has_correct_gradient_and_ignores_masked_pixels():
    import torch
    depth=torch.tensor([[2.,3.],[100.,2.]],requires_grad=True)
    alpha=torch.ones_like(depth);target=torch.full_like(depth,2.)
    mask=torch.tensor([[True,True],[False,True]])
    grid,_=sampling_grid(np.eye(3),np.eye(3),(2,2),(2,2))
    loss,coverage=depth_loss(depth,alpha,target,mask,torch.tensor(grid))
    loss.backward()
    assert loss.item()>0 and coverage.item()==0
    assert depth.grad[0,1]>0 and depth.grad[1,0]==0 and depth.grad[0,0]==0


def test_log_depth_loss_is_invariant_to_scene_unit_scale():
    import torch
    depth=torch.full((3,3),3.);target=torch.full((3,3),2.)
    alpha=torch.ones_like(depth);mask=torch.ones_like(depth,dtype=torch.bool)
    grid,_=sampling_grid(np.eye(3),np.eye(3),(3,3),(3,3))
    a,_=depth_loss(depth,alpha,target,mask,torch.tensor(grid))
    b,_=depth_loss(depth*5,alpha,target*5,mask,torch.tensor(grid))
    assert torch.allclose(a,b,atol=1e-6)


def test_empty_render_has_finite_loss_and_coverage_gradient():
    import torch
    depth=torch.zeros((2,2),requires_grad=True);alpha=torch.zeros_like(depth,requires_grad=True)
    grid,_=sampling_grid(np.eye(3),np.eye(3),(2,2),(2,2))
    loss,coverage=depth_loss(depth,alpha,torch.ones_like(depth),torch.ones_like(depth,dtype=torch.bool),torch.tensor(grid))
    (loss+coverage).backward()
    assert loss.item()==0 and coverage.item()==1 and torch.isfinite(alpha.grad).all() and (alpha.grad<0).all()


def test_guidance_rejects_joint_predictions_in_an_unrelated_frame(tmp_path):
    (tmp_path/'report.json').write_text(json.dumps({'mode':'joint'}))
    with pytest.raises(ValueError,match='aligned'):
        load_guidance(tmp_path,[],1.)


def test_posed_guidance_checks_camera_frame_and_normalizes_depth_units(tmp_path):
    pose=np.eye(4,dtype=np.float32)
    (tmp_path/'report.json').write_text(json.dumps({'mode':'posed','image_names':['a.jpg'],
        'model':'test','input_capture':'capture','frame':'COLMAP arbitrary scale'}))
    np.savez(tmp_path/'predictions.npz',depth=np.full((1,2,2),6.),intrinsics=np.eye(3)[None],extrinsics=pose[None])
    np.savez(tmp_path/'consistency.npz',keep=np.ones((1,2,2),dtype=bool))
    camera={'name':'a.jpg','world_to_camera':pose.tolist()}
    guidance,_=load_guidance(tmp_path,[camera],3.)
    assert np.all(guidance['a.jpg']['depth']==2.)
    camera['world_to_camera'][0][3]=1.
    with pytest.raises(ValueError,match='camera frame'):
        load_guidance(tmp_path,[camera],3.)

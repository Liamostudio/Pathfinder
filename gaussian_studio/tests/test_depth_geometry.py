import numpy as np
import pytest
from gaussian_studio.depth_estimate import unproject,view_consistency,export_cloud


def test_depth_unprojection_round_trips_rotated_camera():
    depth=np.full((7,9),2.5,dtype=np.float32)
    K=np.array([[120,0,4],[0,115,3],[0,0,1]],dtype=np.float32)
    angle=.4;pose=np.eye(4,dtype=np.float32)
    pose[:3,:3]=[[np.cos(angle),0,np.sin(angle)],[0,1,0],[-np.sin(angle),0,np.cos(angle)]]
    pose[:3,3]=[.4,-.2,.7]
    world=unproject(depth,K,pose)
    camera=world@pose[:3,:3].T+pose[:3,3]
    v,u=np.indices(depth.shape)
    assert np.allclose(camera[...,2],depth,atol=1e-6)
    assert np.allclose(camera[...,0]/camera[...,2]*K[0,0]+K[0,2],u,atol=1e-5)
    assert np.allclose(camera[...,1]/camera[...,2]*K[1,1]+K[1,2],v,atol=1e-5)


def test_consistent_planes_have_cross_view_support():
    depth=np.full((2,8,8),2.,dtype=np.float32);confidence=np.ones_like(depth)
    K=np.tile(np.array([[4,0,4],[0,4,4],[0,0,1]],dtype=np.float32),(2,1,1))
    poses=np.tile(np.eye(4,dtype=np.float32),(2,1,1))
    keep,support,report=view_consistency(depth,confidence,K,poses)
    assert keep.all() and (support==1).all() and report['supported_fraction']==1


def test_disagreeing_depths_are_not_counted_as_supported():
    depth=np.ones((2,8,8),dtype=np.float32);depth[0]*=2
    K=np.tile(np.array([[4,0,4],[0,4,4],[0,0,1]],dtype=np.float32),(2,1,1))
    poses=np.tile(np.eye(4,dtype=np.float32),(2,1,1))
    keep,_,report=view_consistency(depth,np.ones_like(depth),K,poses)
    assert not keep.any() and report['supported_pixels']==0
    assert report['pairs'][0]['visible_comparisons']==0  # hidden behind the nearer plane


def test_invalid_depth_does_not_produce_invented_geometry():
    depth=np.full((2,4,4),np.nan)
    with pytest.raises(ValueError,match='No finite positive depths'):
        view_consistency(depth,np.ones_like(depth),np.tile(np.eye(3),(2,1,1)),np.tile(np.eye(4),(2,1,1)))


def test_depth_cloud_is_explicitly_a_point_cloud(tmp_path):
    points=np.array([[1,2,3],[4,5,6]],dtype=np.float32)
    colors=np.array([[255,0,0],[0,255,0]],dtype=np.uint8)
    path=tmp_path/'depth.ply';export_cloud(path,points,colors)
    header,body=path.read_bytes().split(b'end_header\n')
    assert b'not a Gaussian splat' in header and b'element vertex 2' in header
    assert b'property float opacity' not in header and len(body)==2*15

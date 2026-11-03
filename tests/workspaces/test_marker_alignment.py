import cv2
import numpy as np
import pytest
from lookandpick.workspaces import marker_alignment as marker


def test_original_marker_pose_establishes_explicit_similarity_not_robot_alignment(monkeypatch):
    K=np.array([[800.,0,640],[0,800,360],[0,0,1]])
    centre=np.array([0.,-.55,.45]);z=-centre/np.linalg.norm(centre);x=np.cross(z,[0,0,1]);x/=np.linalg.norm(x);y=np.cross(z,x);R=np.vstack([x,y,z]);t=-R@centre
    object_points=np.array([[-.06,.06,0],[.06,.06,0],[.06,-.06,0],[-.06,-.06,0]])
    corners=cv2.projectPoints(object_points,cv2.Rodrigues(R)[0],t,K,None)[0].reshape(4,2)
    monkeypatch.setattr(marker,'detect_marker',lambda *a:corners)
    angle=.3;rotation=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
    scale=2.5;translation=np.array([.3,-.2,.1]);map_R=R@rotation.T;map_t=t*scale-map_R@translation
    E=np.eye(4);E[:3,:3]=map_R;E[:3,3]=map_t
    manifest={'sources':[{'name':'00000.jpg','sha256':'source-hash'}],'frames':{'canonical_from_map':np.eye(4).tolist()},'surfaces':[{'height':.1}]}
    config={'view_id':'00000.jpg','marker_id':0,'dictionary':'DICT_4X4_50','marker_size_m':.12,'marker_size_measured':False}
    result=marker.estimate(np.zeros((720,1280,3),np.uint8),{'full_intrinsics':[K.tolist()],'extrinsics':[E.tolist()]},manifest,config)
    expected=np.eye(4);expected[:3,:3]=rotation*scale;expected[:3,3]=translation
    np.testing.assert_allclose(result['canonical_from_marker'],expected,atol=1e-5)
    np.testing.assert_allclose(np.array(result['marker_from_canonical'])@expected,np.eye(4),atol=1e-5)
    assert not result['metric_inputs_complete'] and result['robot_alignment_status']=='unverified'
    assert result['source_sha256']=='source-hash' and result['intrinsics_source']=='reconstruction_estimate'

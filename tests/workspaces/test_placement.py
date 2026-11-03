import copy
import pytest
from lookandpick.workspaces.placement import measured_placement, calibrated_joints, MARKER_MEASUREMENT
from lookandpick.workspaces.schema import IDENTITY
from lookandpick.workspace_bridge import robot_placement
from lookandpick.kinematics import BaseFrame

def test_measured_robot_is_visual_and_manual_patch_can_revert_status(tmp_path):
    import json
    path=tmp_path/'touch.json';path.write_text(json.dumps({'base_frame':{'offset_m':[.2,-.03],'yaw_deg':135},'offsets_deg':{'shoulder_lift':-3}}))
    draft={'robot':{'joint_positions':{}},'marker_alignment':{'marker_size_m':.12,'canonical_from_marker':IDENTITY,'intrinsics_source':'reconstruction_estimate'},'calibration':{'meters_per_map_unit':None},'warnings':[]}
    placed=measured_placement(draft,path)
    assert placed['robot']['translation']==[.2,-.03,0]
    assert placed['robot']['placement_status']=='touch_calibrated'
    assert placed['marker_alignment']['marker_size_measured']
    assert not placed['marker_alignment']['metric_inputs_complete']
    assert 'placement_status' not in draft['robot']
    assert calibrated_joints([{'joint':'shoulder_lift','reading':3}],path)['shoulder_lift']==0
    assert MARKER_MEASUREMENT['plate_size_m']==.16
    with pytest.raises(ValueError):measured_placement({**draft,'marker_alignment':None},path)

"""Visual placement from the measured base frame; never a motion or safety input."""
import copy
import json
from pathlib import Path
from ..kinematics import BaseFrame
from ..workspace_bridge import robot_placement, urdf_joint_radians
from .schema import robot_transform

ROOT=Path(__file__).resolve().parents[2]
MARKER_MEASUREMENT={'marker_size_m':.120,'plate_size_m':.160,'marker_size_measured':True,'measured_by':'Liam','confirmed_on':'2026-11-03'}


def measured_placement(draft,path=None):
    result=copy.deepcopy(draft); anchor=result.get('marker_alignment')
    if not anchor:raise ValueError('Anchor an original source image to the ArUco marker first')
    if abs(anchor['marker_size_m']-MARKER_MEASUREMENT['marker_size_m'])>1e-9:
        raise ValueError('Re-anchor with the measured 120 mm black square first')
    if path is None:
        paths=sorted((ROOT/'calibration').glob('so101-touch-*.json'))
        if not paths:raise ValueError('No tip-touch base calibration is available')
        path=paths[-1]
    data=json.loads(Path(path).read_text());frame=BaseFrame.from_calibration(str(path))
    visual=data.get('visual_alignment',{})
    foot=BaseFrame(frame.offset,visual.get('foot_yaw_marker_deg',frame.yaw_deg))
    placement=robot_placement(anchor['canonical_from_marker'],foot)
    placement['pan_draw_offset_deg']=visual.get('pan_draw_offset_deg',0.)
    result['robot'].update(placement); result['robot']['placement_calibration']=Path(path).name
    result['robot']['workspace_from_robot_base']=robot_transform(result['robot'],result['calibration'].get('meters_per_map_unit') or 1.)
    result['calibration']['robot_alignment_status']='touch_calibrated'
    result['marker_alignment']['marker_size_measured']=True
    result['marker_alignment']['metric_inputs_complete']=result['marker_alignment'].get('intrinsics_source')=='user_calibrated'
    result['marker_alignment']['robot_alignment_status']='touch_calibrated'
    result['marker_alignment']['measurement']=dict(MARKER_MEASUREMENT)
    result['warnings']=[w for w in result.get('warnings',[]) if 'CAD pose is illustrative' not in w]
    note='Tip-touch calibrated visual robot placement; not a safety input or physical motion authorisation.'
    if note not in result['warnings']:result['warnings'].append(note)
    return result


def calibrated_joints(rows,path=None):
    paths=sorted((ROOT/'calibration').glob('so101-touch-*.json'))
    if path is None and not paths:raise ValueError('No joint-offset calibration available')
    path=Path(path or paths[-1]); data=json.loads(path.read_text())
    readings={r['joint']:r['reading'] for r in rows if r.get('reading') is not None}
    return urdf_joint_radians(readings,data['offsets_deg'],pan_draw_offset_deg=data.get('visual_alignment',{}).get('pan_draw_offset_deg',0.))

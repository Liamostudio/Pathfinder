"""Map the recorded LeRobot Rest reading into the simulator's URDF radians.

This reads calibration data only. It does not construct, connect or power hardware.
"""
import json
import math
import numpy as np
from lookandpick.adapters.so101_profile import REST_READING, TOUCH_CALIBRATION
from lookandpick.workspace_bridge import urdf_joint_radians
from .model import JOINTS

REST_MARGIN_DEG = 3.0

def rest_joint_targets(limits):
    record=json.loads(TOUCH_CALIBRATION.read_text())
    reading=dict(REST_READING)
    # The recorded shoulder/elbow readings sit on the measured hard stops.
    # Back off in reading space as well as respecting the URDF's own limits.
    reading['shoulder_lift']+=REST_MARGIN_DEG
    reading['elbow_flex']-=REST_MARGIN_DEG
    mapped=urdf_joint_radians(reading,record['offsets_deg'])
    values=np.array([mapped[name] for name in JOINTS],dtype=float)
    margin=math.radians(REST_MARGIN_DEG)
    # The five arm joints stay inside the simulator's limits. Rest never operates
    # the gripper: Simulation.rest substitutes its current aperture before moving.
    values[:5]=np.clip(values[:5],limits[:5,0]+margin,limits[:5,1]-margin)
    values[5]=np.clip(values[5],*limits[5])
    return values

"""Show what the real SO-101 profile would do for a target, in software only. Nothing is connected, powered or moved.

    python3 scripts/so101_plan.py 0.05 0.0 0.03          # marker-frame metres: x y z (z up, origin = marker centre)
    python3 scripts/so101_plan.py 0.0 0.1 0.05 --from-joints -30,-60,60,20,0,1.3

It builds the commissioned profile from the calibration files, starts a dry-run arm at the Rest pose (or the joints you give, in
LeRobot degrees: pan,lift,elbow,wrist_flex,wrist_roll,gripper) and plans a straight-line move in 1 cm waypoints. It reports
whether the whole plan was accepted (a refused plan has moved nothing), why not, how long it would take, and what each joint does.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lookandpick.adapters.so101 import JOINTS  # noqa: E402
from lookandpick.adapters.so101_profile import REST_READING, dry_run_arm  # noqa: E402


def plan(target, start=None, *, speed_scale: float = 1.0, log=print) -> dict:
    arm, inner = dry_run_arm(start)
    begin_joints, begin_tip = dict(inner.joints), arm.position
    log(f"Start tip (marker frame): ({begin_tip[0] * 1000:+.0f}, {begin_tip[1] * 1000:+.0f}, {begin_tip[2] * 1000:+.0f}) mm")
    log(f"Target (marker frame):    ({target[0] * 1000:+.0f}, {target[1] * 1000:+.0f}, {target[2] * 1000:+.0f}) mm, "
        f"{math.dist(begin_tip, target) * 1000:.0f} mm away")
    ok = arm.move_to(target, speed_scale)
    ticks = [e[1] for e in inner.log if e[0] == "dry_run"]
    refusal = next((e for e in inner.log if e[0] == "refused_cartesian"), None)
    if not ok:
        why = refusal[2] if refusal and len(refusal) > 2 else ("the target is outside the workspace envelope, below the minimum height or "
                                                              "within the minimum reach of the base" if refusal else "the move stopped part-way")
        log(f"REFUSED: {why}. " + ("Nothing moved." if not ticks else f"It stopped after {len(ticks)} ticks."))
        return {"ok": False, "reason": why, "ticks": len(ticks)}
    seconds = len(ticks) * inner.limits.period_s
    log(f"ACCEPTED: {len(ticks)} control ticks, about {seconds:.0f} s at the profile's speed limit "
        f"({inner.limits.max_velocity:g} deg/s), final tip error {math.dist(arm.position, target) * 1000:.1f} mm")
    log("Joint changes (LeRobot degrees):")
    for name in JOINTS:
        log(f"  {name:14s} {begin_joints[name]:+7.1f} -> {inner.joints[name]:+7.1f}   ({inner.joints[name] - begin_joints[name]:+6.1f})")
    return {"ok": True, "ticks": len(ticks), "seconds": seconds}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("x", type=float)
    p.add_argument("y", type=float)
    p.add_argument("z", type=float)
    p.add_argument("--from-joints", help="start pose: pan,lift,elbow,wrist_flex,wrist_roll,gripper in degrees (default: Rest)")
    a = p.parse_args(argv)
    start = None
    if a.from_joints:
        values = [float(v) for v in a.from_joints.split(",")]
        if len(values) != 6:
            raise SystemExit("--from-joints needs six numbers")
        start = dict(zip(JOINTS, values))
    plan((a.x, a.y, a.z), start)


if __name__ == "__main__":
    main()

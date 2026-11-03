"""Tip-touch calibration for the SO-101: find where the tip really is, with the arm moved BY HAND (torque off).

    ~/src/lerobot-venv/bin/python scripts/so101_touch.py capture --port /dev/ttyACM0 --id follower --out touches/2026-11-03.json
    python3 scripts/so101_touch.py analyse touches/2026-11-03.json

capture: you rest the gripper tip on the table at several spots and wrist angles, then on the ArUco marker's centre and four
corners, pressing Enter each time. It only READS the joint angles (the supply must be on so the motors answer, but torque stays
off and nothing is written), so nothing can move. Hold the arm still for half a second at each point.

analyse (no arm needed):
  1. the tip height the model gives at every touch (it should be 0 on the table, the plate thickness on the marker);
  2. a fit of small zero-offset errors in shoulder_lift, elbow_flex and wrist_flex, plus a tool-length correction (the URDF's
     tip point is not exactly the physical point you touch with), that makes every touch land on its plane, with a standard
     error for each (large means the touches were too alike: vary the reach and the wrist angle);
  3. from the marker points, the arm-base offset and yaw in the marker frame (`BaseFrame`), with residuals in mm.

Marker points use the frame in docs/marker-axes.png: origin at the centre of the black square, +X to the right, +Y up, with the
pattern oriented as in that picture. Corners are the corners of the BLACK square (120 mm), TL = (-60, +60) mm and so on.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from lookandpick.kinematics import CHAIN, SO101Kinematics  # noqa: E402

MARKER_HALF_M = 0.06
FIT_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex")
PLAN = ([(f"table-{i}", None, f"the TABLE (spot {i} of 6: spread them out, vary reach and wrist angle)") for i in range(1, 7)]
        + [("marker-centre", (0.0, 0.0), "the CENTRE of the marker's black square"),
           ("marker-TL", (-MARKER_HALF_M, MARKER_HALF_M), "the black square's TOP-LEFT corner (as in docs/marker-axes.png)"),
           ("marker-TR", (MARKER_HALF_M, MARKER_HALF_M), "the black square's TOP-RIGHT corner"),
           ("marker-BR", (MARKER_HALF_M, -MARKER_HALF_M), "the black square's BOTTOM-RIGHT corner"),
           ("marker-BL", (-MARKER_HALF_M, -MARKER_HALF_M), "the black square's BOTTOM-LEFT corner")])


# A second round that separates the wrist offset from the tool length: the SAME spot touched with the gripper tilted three ways
# (if the model is right the height stays 0 however it is tilted), at a near, a middle and a far spot.
VARIETY = ("straight down (tip pointing at the table)", "tilted about 30 degrees (tip towards you)", "tilted about 60 degrees (tip towards you)")
SPOTS = ("NEAR the base", "a MIDDLE distance from the base", "FAR from the base (near the edge of its reach)")
PLAN_VARIETY = [(f"spot{i + 1}-tilt{k}", None, f"the TABLE at spot {i + 1}, {spot}, gripper {tilt}"
                 + ("" if k == 0 else "; keep the SAME spot, only tilt the wrist"))
                for i, spot in enumerate(SPOTS) for k, tilt in enumerate(VARIETY)]
PLANS = {"full": PLAN, "variety": PLAN_VARIETY}


class MovedWhileReading(RuntimeError):
    pass


def sample_still(bus, *, reads: int = 10, gap_s: float = 0.05, tolerance_deg: float = 0.4, sleep=time.sleep) -> dict[str, float]:
    """Mean joint angles over `reads` reads; refuses if any joint moved more than `tolerance_deg` meanwhile."""
    samples = []
    for _ in range(reads):
        samples.append(bus.sync_read("Present_Position"))
        sleep(gap_s)
    out = {}
    for joint in CHAIN:
        values = [s[joint] for s in samples]
        if max(values) - min(values) > tolerance_deg:
            raise MovedWhileReading(f"{joint} moved {max(values) - min(values):.2f} deg while reading: hold the arm still")
        out[joint] = sum(values) / len(values)
    return out


def capture(bus, plan, out_path: str, *, ask=input, log=print, sleep=time.sleep, robot_id: str = "", existing=None) -> list[dict]:
    kin = SO101Kinematics()
    points = list(existing or [])
    for label, marker_xy, where in plan:
        while True:
            answer = ask(f"Rest the tip on {where}, hold still, then press Enter (s = skip this point, q = finish):\n> ").strip().lower()
            if answer == "q":
                return _save(points, out_path, robot_id)
            if answer == "s":
                break
            try:
                joints = sample_still(bus, sleep=sleep)
            except MovedWhileReading as exc:
                log(f"  {exc}; try again.")
                continue
            x, y, z = kin.forward(joints)
            points.append({"label": label, "marker_xy": list(marker_xy) if marker_xy else None, "joints": joints})
            log(f"  {label}: model tip ({x * 1000:+.0f}, {y * 1000:+.0f}, {z * 1000:+.0f}) mm in the base frame")
            _save(points, out_path, robot_id)
            break
    return _save(points, out_path, robot_id)


def _save(points: list[dict], path: str, robot_id: str) -> list[dict]:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as handle:
        json.dump({"created": time.strftime("%Y-%m-%d %H:%M:%S"), "robot_id": robot_id, "points": points}, handle, indent=1)
    return points


# --------------------------------------------------------------------------------------------------------------------
def _tip(kin: SO101Kinematics, joints: dict[str, float], params: list[float]):
    """Model tip with zero offsets (degrees on the three fit joints) and a tool-length shift (metres) applied."""
    shifted = dict(joints)
    for name, d in zip(FIT_JOINTS, params[:3]):
        shifted[name] += d
    return kin.forward(shifted, tool_shift=params[3])


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-18:
            raise ValueError("singular")
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit_offsets(kin: SO101Kinematics, planes: list[tuple[dict, float]]) -> dict:
    """Least squares for [d_lift, d_elbow, d_wrist_flex] (degrees) and a tool shift (m) so each tip lands on its plane height."""
    if len(planes) < 5:
        return {"fitted": False, "reason": f"need at least 5 touches with a known height, have {len(planes)}"}
    params = [0.0, 0.0, 0.0, 0.0]
    steps = [0.01, 0.01, 0.01, 0.0002]

    def residuals(p):
        return [_tip(kin, j, p)[2] - h for j, h in planes]

    def jacobian(p):
        base = residuals(p)
        cols = []
        for k, step in enumerate(steps):
            q = list(p)
            q[k] += step
            cols.append([(r2 - r1) / step for r1, r2 in zip(base, residuals(q))])
        return [[cols[k][i] for k in range(4)] for i in range(len(base))]

    before = math.sqrt(sum(r * r for r in residuals(params)) / len(planes))
    for _ in range(30):
        r, j = residuals(params), jacobian(params)
        # scale the columns so degrees and metres are comparable; Levenberg damping keeps it stable
        scale = [max(1e-9, math.sqrt(sum(row[k] ** 2 for row in j))) for k in range(4)]
        jn = [[row[k] / scale[k] for k in range(4)] for row in j]
        a = [[sum(row[p] * row[q] for row in jn) + (1e-6 if p == q else 0.0) for q in range(4)] for p in range(4)]
        g = [-sum(row[p] * ri for row, ri in zip(jn, r)) for p in range(4)]
        try:
            delta = _solve(a, g)
        except ValueError:
            return {"fitted": False, "reason": "the touches do not pin the offsets down (singular): vary reach and wrist angle"}
        params = [params[k] + delta[k] / scale[k] for k in range(4)]
        if max(abs(d) for d in delta) < 1e-10:
            break
    r, j = residuals(params), jacobian(params)
    after = math.sqrt(sum(x * x for x in r) / len(planes))
    scale = [max(1e-9, math.sqrt(sum(row[k] ** 2 for row in j))) for k in range(4)]
    jn = [[row[k] / scale[k] for k in range(4)] for row in j]
    try:
        inverse = [_solve([[sum(row[p] * row[q] for row in jn) for q in range(4)] for p in range(4)],
                          [1.0 if i == c else 0.0 for i in range(4)]) for c in range(4)]
        dof = max(1, len(planes) - 4)
        sigma = math.sqrt(sum(x * x for x in r) / dof)
        se = [sigma * math.sqrt(max(0.0, inverse[k][k])) / scale[k] for k in range(4)]
    except ValueError:
        se = [float("inf")] * 4
    return {"fitted": True, "offsets_deg": dict(zip(FIT_JOINTS, params[:3])), "tool_shift_m": params[3],
            "rms_before_mm": before * 1000, "rms_after_mm": after * 1000, "stderr": se, "params": params}


def fit_base_frame(kin: SO101Kinematics, points: list[dict], params: list[float]) -> dict:
    """Rigid 2D fit of the marker points: marker = R(yaw) * base + offset."""
    pairs = [(_tip(kin, p["joints"], params)[:2], tuple(p["marker_xy"])) for p in points if p.get("marker_xy")]
    if len(pairs) < 2:
        return {"fitted": False, "reason": f"need at least 2 marker points, have {len(pairs)}"}
    n = len(pairs)
    cb = (sum(b[0] for b, _ in pairs) / n, sum(b[1] for b, _ in pairs) / n)
    cm = (sum(m[0] for _, m in pairs) / n, sum(m[1] for _, m in pairs) / n)
    dot = sum((b[0] - cb[0]) * (m[0] - cm[0]) + (b[1] - cb[1]) * (m[1] - cm[1]) for b, m in pairs)
    cross = sum((b[0] - cb[0]) * (m[1] - cm[1]) - (b[1] - cb[1]) * (m[0] - cm[0]) for b, m in pairs)
    yaw = math.atan2(cross, dot)
    c, s = math.cos(yaw), math.sin(yaw)
    offset = (cm[0] - (c * cb[0] - s * cb[1]), cm[1] - (s * cb[0] + c * cb[1]))
    errors = [math.hypot(offset[0] + c * b[0] - s * b[1] - m[0], offset[1] + s * b[0] + c * b[1] - m[1]) * 1000 for b, m in pairs]
    return {"fitted": True, "offset_m": offset, "yaw_deg": math.degrees(yaw), "residuals_mm": errors, "points": n}


def analyse(data: dict, *, plate_mm: float = 3.0, log=print) -> dict:
    kin = SO101Kinematics()
    points = data["points"]
    planes = [(p["joints"], (plate_mm / 1000 if p.get("marker_xy") else 0.0)) for p in points]
    log(f"{len(points)} touches. Model tip height at each (mm; the table should read 0, the marker {plate_mm:g}):")
    for p, (j, h) in zip(points, planes):
        x, y, z = kin.forward(j)
        log(f"  {p['label']:14s} base-frame ({x * 1000:+6.0f}, {y * 1000:+6.0f}) mm   height {z * 1000:+6.1f} mm   error {(z - h) * 1000:+6.1f} mm")
    result = {"fit": fit_offsets(kin, planes)}
    fit = result["fit"]
    if fit["fitted"]:
        o = fit["offsets_deg"]
        log(f"\nFit: zero offsets (add to the reading) lift {o['shoulder_lift']:+.2f}, elbow {o['elbow_flex']:+.2f}, "
            f"wrist_flex {o['wrist_flex']:+.2f} deg; tool shift {fit['tool_shift_m'] * 1000:+.1f} mm")
        log(f"  height error rms {fit['rms_before_mm']:.1f} mm -> {fit['rms_after_mm']:.1f} mm; standard errors "
            f"{fit['stderr'][0]:.2f}, {fit['stderr'][1]:.2f}, {fit['stderr'][2]:.2f} deg and {fit['stderr'][3] * 1000:.1f} mm")
        if max(fit["stderr"][:3]) > 3.0 or fit["stderr"][3] > 0.02:
            log("  WARNING: the touches are too alike to pin these down; repeat with more variety in reach and wrist angle.")
        params = fit["params"]
    else:
        log(f"\nNo offset fit: {fit['reason']}")
        params = [0.0, 0.0, 0.0, 0.0]
    base = fit_base_frame(kin, points, params)
    result["base_frame"] = base
    if base["fitted"]:
        ox, oy = base["offset_m"]
        log(f"\nBase frame in the marker frame: offset ({ox * 1000:+.0f}, {oy * 1000:+.0f}) mm, yaw {base['yaw_deg']:+.1f} deg "
            f"(residuals mm: {', '.join(f'{r:.1f}' for r in base['residuals_mm'])})")
        log(f"  -> BaseFrame(offset=({ox:.3f}, {oy:.3f}, 0.0), yaw_deg={base['yaw_deg']:.1f})")
    else:
        log(f"\nNo base frame: {base['reason']}")
    return result


def main(argv=None, *, ask=input) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="read joint angles at each touch (read-only; torque stays off)")
    c.add_argument("--port", required=True)
    c.add_argument("--id", default="lookandpick", help="name of the calibration file (the one calibrate wrote)")
    c.add_argument("--out", required=True)
    c.add_argument("--plan", choices=sorted(PLANS), default="full", help="'variety' = the same spots touched at three wrist tilts")
    c.add_argument("--append", action="store_true", help="keep the touches already in --out and add these")
    a = sub.add_parser("analyse", help="fit offsets and the base frame from a capture file")
    a.add_argument("file")
    a.add_argument("--plate-mm", type=float, default=3.0, help="thickness of the marker plate (the marker points sit this high)")
    args = p.parse_args(argv)
    if args.cmd == "analyse":
        with open(args.file) as handle:
            analyse(json.load(handle), plate_mm=args.plate_mm)
        return
    from so101_setup import build_robot, find_ports  # noqa: PLC0415 (needs the LeRobot environment)
    if args.port not in find_ports():
        raise SystemExit(f"{args.port} does not exist. Ports now: {find_ports() or 'none'}")
    robot = build_robot(args.port, args.id)
    if not robot.calibration:
        raise SystemExit(f"No calibration file for id '{args.id}'. Run so101_setup.py calibrate first.")
    robot.bus.connect()                                  # handshake only: reads, never writes
    try:
        if not robot.bus.is_calibrated:
            raise SystemExit("The motors' calibration does not match the saved file; nothing was captured.")
        if any(robot.bus.sync_read("Torque_Enable", normalize=False).values()):
            raise SystemExit("A motor still has torque ON, so you could not move the arm by hand. Unplug the 5V supply for a "
                             "moment (that releases every motor), plug it back in and run this again. Nothing was changed.")
        print("Torque stays OFF: the arm is limp, so support it and move it by hand. Reading only; nothing is written.")
        existing = []
        if args.append and os.path.exists(args.out):
            with open(args.out) as handle:
                existing = json.load(handle)["points"]
            print(f"Keeping the {len(existing)} touches already in {args.out}.")
        points = capture(robot.bus, PLANS[args.plan], args.out, ask=ask, robot_id=args.id, existing=existing)
        print(f"Saved {len(points)} touches to {args.out}. Now run: python3 scripts/so101_touch.py analyse {args.out}")
    finally:
        robot.bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    main()

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
VARIETY = ("straight down (pointing at the table)", "leaning about 30 degrees from vertical (wrist nearer the base than the tip)",
           "leaning about 60 degrees from vertical (wrist nearer the base than the tip)")
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


def _lm(residuals, params, steps, *, iterations: int = 80):
    """Levenberg-Marquardt with finite-difference Jacobian. Returns (params, J, residual list); singular -> ValueError."""
    def jac(p):
        base = residuals(p)
        cols = []
        for k, step in enumerate(steps):
            q = list(p)
            q[k] += step
            cols.append([(r2 - r1) / step for r1, r2 in zip(base, residuals(q))])
        return [[cols[k][i] for k in range(len(p))] for i in range(len(base))]

    def cost(p):
        return sum(x * x for x in residuals(p))

    n, lam, current = len(params), 1e-3, cost(params)
    for _ in range(iterations):
        r, j = residuals(params), jac(params)
        scale = [max(1e-12, math.sqrt(sum(row[k] ** 2 for row in j))) for k in range(n)]
        jn = [[row[k] / scale[k] for k in range(n)] for row in j]
        g = [-sum(row[p] * ri for row, ri in zip(jn, r)) for p in range(n)]
        base = [[sum(row[p] * row[q] for row in jn) for q in range(n)] for p in range(n)]
        for _try in range(12):
            a = [[base[p][q] + (lam if p == q else 0.0) for q in range(n)] for p in range(n)]
            delta = _solve(a, g)
            trial = [params[k] + delta[k] / scale[k] for k in range(n)]
            trial_cost = cost(trial)
            if trial_cost < current:
                params, lam, improved = trial, max(lam / 3, 1e-9), current - trial_cost
                current = trial_cost
                break
            lam *= 5
        else:
            break
        if improved < 1e-14:
            break
    return params, jac(params), residuals(params)


def _standard_errors(j, r, count: int) -> list[float]:
    n = len(j[0])
    scale = [max(1e-12, math.sqrt(sum(row[k] ** 2 for row in j))) for k in range(n)]
    jn = [[row[k] / scale[k] for k in range(n)] for row in j]
    normal = [[sum(row[p] * row[q] for row in jn) for q in range(n)] for p in range(n)]
    sigma = math.sqrt(sum(x * x for x in r) / max(1, len(r) - n))
    out = []
    for c in range(count):
        try:
            column = _solve(normal, [1.0 if i == c else 0.0 for i in range(n)])
            out.append(sigma * math.sqrt(max(0.0, column[c])) / scale[c])
        except ValueError:
            out.append(float("inf"))
    return out


def _spot_groups(points: list[dict]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for i, p in enumerate(points):
        head, _, tail = p["label"].partition("-")
        if head.startswith("spot") and tail.startswith("tilt"):
            groups.setdefault(head, []).append(i)
    return {k: v for k, v in groups.items() if len(v) >= 2}


def fit_all(kin: SO101Kinematics, points: list[dict], plate_mm: float = 3.0, use_xy: bool = True) -> dict:
    """One least squares over everything known about the touches.

    Unknowns: zero offsets (degrees) on shoulder_lift, elbow_flex, wrist_flex, a tool-length shift (m), the position of each
    'spot' (the same table point touched at several wrist tilts) and, if marker points exist, the base frame in the marker
    frame (x, y, yaw). Equations: every tip is at its plane height; every touch of a spot lands on that spot's xy; every marker
    touch lands on its known marker xy."""
    heights = [(p["joints"], plate_mm / 1000 if p.get("marker_xy") else 0.0) for p in points]
    groups = _spot_groups(points) if use_xy else {}
    marker = [i for i, p in enumerate(points) if p.get("marker_xy")]
    use_frame = use_xy and len(marker) >= 2
    spot_names = list(groups)
    params = [0.0, 0.0, 0.0, 0.0]
    steps = [0.01, 0.01, 0.01, 0.0002]
    zero = [0.0, 0.0, 0.0, 0.0]
    for name in spot_names:
        xs = [_tip(kin, points[i]["joints"], zero)[:2] for i in groups[name]]
        params += [sum(x for x, _ in xs) / len(xs), sum(y for _, y in xs) / len(xs)]
        steps += [0.0005, 0.0005]
    frame_at = len(params)
    if use_frame:
        init = fit_base_frame(kin, points, zero)
        params += [init["offset_m"][0], init["offset_m"][1], init["yaw_deg"]]
        steps += [0.0005, 0.0005, 0.01]
    needed = len(params) + 1
    equations = len(points) + 2 * sum(len(v) for v in groups.values()) + (2 * len(marker) if use_frame else 0)
    if equations < needed or len(points) < 5:
        return {"fitted": False, "reason": f"need at least 5 touches and more equations than unknowns ({equations} vs {needed - 1}); have {len(points)} touches"}

    def tips(p):
        return [_tip(kin, pt["joints"], p[:4]) for pt in points]

    def residuals(p):
        t = tips(p)
        r = [t[i][2] - heights[i][1] for i in range(len(points))]
        for k, name in enumerate(spot_names):
            sx, sy = p[4 + 2 * k], p[5 + 2 * k]
            for i in groups[name]:
                r += [t[i][0] - sx, t[i][1] - sy]
        if use_frame:
            bx, by, yaw = p[frame_at], p[frame_at + 1], math.radians(p[frame_at + 2])
            c, s = math.cos(yaw), math.sin(yaw)
            for i in marker:
                mx, my = points[i]["marker_xy"]
                r += [bx + c * t[i][0] - s * t[i][1] - mx, by + s * t[i][0] + c * t[i][1] - my]
        return r

    height_before = math.sqrt(sum(x * x for x in residuals(params)[:len(points)]) / len(points))
    try:
        params, j, r = _lm(residuals, params, steps)
    except ValueError:
        return {"fitted": False, "reason": "the touches do not pin the offsets down (singular): vary reach and wrist angle"}
    se = _standard_errors(j, r, 4)
    out = {"fitted": True, "offsets_deg": dict(zip(FIT_JOINTS, params[:3])), "tool_shift_m": params[3],
           "rms_before_mm": height_before * 1000, "rms_after_mm": math.sqrt(sum(x * x for x in r[:len(points)]) / len(points)) * 1000,
           "all_rms_mm": math.sqrt(sum(x * x for x in r) / len(r)) * 1000, "stderr": se, "params": params[:4],
           "equations": len(r), "unknowns": len(params)}
    if use_frame:
        marker_res = r[-2 * len(marker):]
        out["base_frame"] = {"fitted": True, "offset_m": (params[frame_at], params[frame_at + 1]), "yaw_deg": params[frame_at + 2],
                             "residuals_mm": [math.hypot(marker_res[2 * k], marker_res[2 * k + 1]) * 1000 for k in range(len(marker))],
                             "points": len(marker)}
    return out


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


def cross_validate(points: list[dict], *, plate_mm: float = 3.0, repeats: int = 40, hold_out: int = 5, seed: int = 5) -> dict:
    """Held-out tip-height error (mm) of each correction model on touches it was not fitted to, over random splits."""
    import random
    kin, rng = SO101Kinematics(), random.Random(seed)
    zero = [0.0, 0.0, 0.0, 0.0]

    def err(params, held):
        h = [plate_mm / 1000 if p.get("marker_xy") else 0.0 for p in held]
        return math.sqrt(sum((_tip(kin, p["joints"], params)[2] - h[i]) ** 2 for i, p in enumerate(held)) / len(held)) * 1000

    def lift_only(train):
        best = min((sum((_tip(kin, p["joints"], [d, 0, 0, 0])[2] - (plate_mm / 1000 if p.get("marker_xy") else 0.0)) ** 2 for p in train), d)
                   for d in [x / 10 for x in range(-80, 81)])
        return [best[1], 0.0, 0.0, 0.0]

    out: dict[str, list[float]] = {"no correction": [], "lift offset only": [], "heights, 4 parameters": [], "heights + same-spot + marker": []}
    for _ in range(repeats):
        order = list(range(len(points)))
        rng.shuffle(order)
        held, train = [points[i] for i in order[:hold_out]], [points[i] for i in order[hold_out:]]
        out["no correction"].append(err(zero, held))
        out["lift offset only"].append(err(lift_only(train), held))
        for name, use_xy in (("heights, 4 parameters", False), ("heights + same-spot + marker", True)):
            fit = fit_all(kin, train, plate_mm, use_xy=use_xy)
            if fit["fitted"]:
                out[name].append(err(fit["params"], held))
    summary = {}
    for name, values in out.items():
        if values:
            values = sorted(values)
            summary[name] = {"median_mm": values[len(values) // 2], "p90_mm": values[int(len(values) * 0.9)], "worst_mm": values[-1], "n": len(values)}
    return summary


def analyse(data: dict, *, plate_mm: float = 3.0, log=print, validate: bool = False, save: str | None = None, source: str = "") -> dict:
    kin = SO101Kinematics()
    points = data["points"]
    log(f"{len(points)} touches. Model tip height with NO corrections (mm; the table should read 0, the marker {plate_mm:g}):")
    for p in points:
        x, y, z = kin.forward(p["joints"])
        h = plate_mm / 1000 if p.get("marker_xy") else 0.0
        log(f"  {p['label']:14s} base-frame ({x * 1000:+6.0f}, {y * 1000:+6.0f}) mm   height {z * 1000:+6.1f} mm   error {(z - h) * 1000:+6.1f} mm")
    fit = fit_all(kin, points, plate_mm, use_xy=False)
    cross = fit_all(kin, points, plate_mm, use_xy=True)
    result = {"fit": fit, "cross_check": cross}
    if fit["fitted"]:
        o = fit["offsets_deg"]
        log(f"\nRECOMMENDED fit (heights only: the model that predicts unseen touches best, see --validate):")
        log(f"  zero offsets (add to the reading): lift {o['shoulder_lift']:+.2f}, elbow {o['elbow_flex']:+.2f}, "
            f"wrist_flex {o['wrist_flex']:+.2f} deg; tool shift {fit['tool_shift_m'] * 1000:+.1f} mm")
        log(f"  height error rms {fit['rms_before_mm']:.1f} mm -> {fit['rms_after_mm']:.1f} mm; standard errors "
            f"{fit['stderr'][0]:.2f}, {fit['stderr'][1]:.2f}, {fit['stderr'][2]:.2f} deg and {fit['stderr'][3] * 1000:.1f} mm")
        if max(fit["stderr"][:3]) > 2.0 or fit["stderr"][3] > 0.015:
            log("  WARNING: these offsets are not individually well determined (they trade off against each other). Trust their combined")
            log("  effect on the tip (see --validate), only near the poses that were touched; more varied touches would pin them down.")
    else:
        log(f"\nNo offset fit: {fit['reason']}")
    if cross["fitted"]:
        c = cross["offsets_deg"]
        log(f"Cross-check (adds 'same spot' and marker constraints): lift {c['shoulder_lift']:+.2f}, elbow {c['elbow_flex']:+.2f}, "
            f"wrist_flex {c['wrist_flex']:+.2f} deg, tool {cross['tool_shift_m'] * 1000:+.1f} mm; all residuals rms {cross['all_rms_mm']:.1f} mm")
    params = fit["params"] if fit["fitted"] else [0.0, 0.0, 0.0, 0.0]
    base = fit_base_frame(kin, points, params)
    result["base_frame"] = base
    if base["fitted"]:
        ox, oy = base["offset_m"]
        log(f"\nBase frame in the marker frame: offset ({ox * 1000:+.0f}, {oy * 1000:+.0f}) mm, yaw {base['yaw_deg']:+.1f} deg "
            f"(residuals mm: {', '.join(f'{r:.1f}' for r in base['residuals_mm'])})")
        log(f"  -> BaseFrame(offset=({ox:.3f}, {oy:.3f}, 0.0), yaw_deg={base['yaw_deg']:.1f})")
    else:
        log(f"\nNo base frame: {base['reason']}")
    if validate:
        result["validation"] = cross_validate(points, plate_mm=plate_mm)
        log("\nHeld-out height error (5 touches left out, fitted on the rest, 40 random splits):")
        for name, v in result["validation"].items():
            log(f"  {name:30s} median {v['median_mm']:5.1f} mm   90th percentile {v['p90_mm']:5.1f} mm   worst {v['worst_mm']:5.1f} mm")
    if save and fit["fitted"]:
        record = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "source": source, "touches": len(points), "method": "heights-only least squares",
                  "offsets_deg": fit["offsets_deg"], "tool_shift_m": fit["tool_shift_m"], "stderr": fit["stderr"],
                  "base_frame": ({"offset_m": list(base["offset_m"]), "yaw_deg": base["yaw_deg"], "residuals_mm": base["residuals_mm"]}
                                 if base["fitted"] else None)}
        if "validation" in result:
            record["held_out"] = result["validation"]
        os.makedirs(os.path.dirname(os.path.abspath(save)), exist_ok=True)
        with open(save, "w") as handle:
            json.dump(record, handle, indent=1)
        log(f"\nSaved to {save}")
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
    a.add_argument("--validate", action="store_true", help="also score each correction model on touches it was not fitted to")
    a.add_argument("--save", help="write the recommended calibration (offsets, tool shift, base frame) to this JSON file")
    args = p.parse_args(argv)
    if args.cmd == "analyse":
        with open(args.file) as handle:
            analyse(json.load(handle), plate_mm=args.plate_mm, validate=args.validate, save=args.save, source=args.file)
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

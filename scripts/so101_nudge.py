"""First powered move of the SO-101 follower: nudge ONE joint a few degrees, slowly, and come back.

    ~/src/lerobot-venv/bin/python scripts/so101_nudge.py --port /dev/ttyACM0 --id follower
    ~/src/lerobot-venv/bin/python scripts/so101_nudge.py --port /dev/ttyACM0 --id follower --joint wrist_roll --degrees 3

This is a bring-up test, not a planner: no kinematics, no Cartesian targets. It exists because LeRobot's
`connect()` turns torque on for every joint while a joint's stored goal may still be where it was before you
moved the arm by hand, so the arm can jump. Here every goal is first set to the CURRENT position while torque
is still off, then torque goes on, then one joint moves.

SAFETY: powers the servos. Liam must be physically present at an interactive terminal with the 5V kill switch
in reach (refuses over SSH or piped input). Put the arm in its folded rest pose first and keep a hand on it.
At most 20 degrees per move, 10 degrees/s, one joint at a time, never past a joint's calibrated range. Aborts (and releases torque) on a stall, a voltage sag or Ctrl-C.
Torque is released motor by motor at the end (a motor that errors cannot leave the others on; if any release
cannot be confirmed the script says so and you cut the supply), so the arm goes limp: support it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from so101_setup import build_robot, find_ports, require_present  # noqa: E402

# Per-move ceiling for every joint. The move is also checked against the joint's calibrated range (see joint_limits),
# so a joint resting at one end of its range can only be sent the other way.
MAX_DEGREES = {name: 20.0 for name in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")}
RANGE_MARGIN_DEG = 2.0
STEP_DEG = 0.5
PERIOD_S = 0.05                 # 0.5 deg / 0.05 s = 10 deg/s
STALL_DEG = 2.0                 # abort when the joint lags its command by this much...
STALL_READS = 4                 # ...for this many reads in a row (0.2 s); a false abort is harmless
MIN_VOLTS = 4.5                 # abort below this at the motor (supply is ~5.5 V)
ACCELERATION = 20               # LeRobot's default is 254 (as fast as possible); gentle for bring-up
TORQUE_LIMIT = 500              # 50 % of max, the same ceiling LeRobot uses for the gripper
NUDGE_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")


class NudgeAbort(RuntimeError):
    """A safety condition stopped the move; torque has been or will be released."""


def release_all(bus, *, log=print) -> list[str]:
    """Release torque motor by motor, so one motor that errors (e.g. overload) cannot leave the others on.

    Returns the motors whose release could not be confirmed; the caller must then tell the operator to cut the supply.
    """
    failed = []
    for motor in list(bus.motors):
        for _ in range(3):
            try:
                bus.disable_torque(motor)
                break
            except Exception as exc:  # noqa: BLE001 - any failure must still let the loop reach the other motors
                last = exc
        else:
            failed.append(motor)
            log(f"COULD NOT CONFIRM torque released on {motor}: {last}")
    if failed:
        log(f"!!! {', '.join(failed)} may still be holding. CUT THE 5V SUPPLY NOW (unplug it); that releases every motor.")
    return failed


def volts(bus) -> dict[str, float]:
    return {name: raw / 10 for name, raw in bus.sync_read("Present_Voltage", normalize=False).items()}


SETTLE_S = 3.0                  # after torque comes on, watch the supply this long before any motion is allowed
SETTLE_FLOOR = 3.8              # a hard floor during settling: the brief torque-on dip is tolerated, a collapse is not
STAGGER_S = 0.25                # gap between switching each motor's torque on, so the supply sees six small surges


class SupplyWatch:
    """Abort on a real sag, not a glitch: MIN_VOLTS must be breached on LOW_STREAK readings in a row, or SETTLE_FLOOR once."""
    LOW_STREAK = 3

    def __init__(self):
        self.streak = 0
        self.glitches: dict[str, int] = {}

    def check(self, readings: dict[str, float], context: str = "") -> tuple[str, float]:
        motor, lowest = min(readings.items(), key=lambda kv: kv[1])
        if lowest < SETTLE_FLOOR:
            raise NudgeAbort(f"voltage collapsed to {lowest:.1f} V on {motor} (floor {SETTLE_FLOOR} V){context}")
        if lowest < MIN_VOLTS:
            self.streak += 1
            self.glitches[motor] = self.glitches.get(motor, 0) + 1
            if self.streak >= self.LOW_STREAK:
                raise NudgeAbort(f"voltage stayed low: {lowest:.1f} V on {motor} for {self.streak} readings (limit {MIN_VOLTS} V)"
                                 f"{context}; all motors: " + ", ".join(f"{m} {v:.1f}" for m, v in readings.items()))
        else:
            self.streak = 0
        return motor, lowest


def lowest_motor(readings: dict[str, float]) -> tuple[str, float]:
    return min(readings.items(), key=lambda kv: kv[1])


def arm_hold(bus, *, sleep=time.sleep, torque_limit: int = TORQUE_LIMIT) -> dict[str, float]:
    """Hold every joint where it is: goals = present while torque is off, gentle settings, then torque on one by one."""
    bus.disable_torque()
    present = bus.sync_read("Present_Position")
    bus.sync_write("Goal_Position", present)
    for motor in present:
        bus.write("Acceleration", motor, ACCELERATION)
        # The gripper is always capped at TORQUE_LIMIT (as LeRobot does); the other joints take `torque_limit`.
        bus.write("Max_Torque_Limit", motor, TORQUE_LIMIT if motor == "gripper" else torque_limit)
    present = bus.sync_read("Present_Position")
    bus.sync_write("Goal_Position", present)
    for motor in list(bus.motors):
        bus.enable_torque(motor)
        sleep(STAGGER_S)
    return present


def settle(bus, *, sleep=time.sleep, log=print) -> float:
    """Watch the supply after torque-on with nothing moving. Returns the lowest voltage seen.

    Tolerates the short dip down to SETTLE_FLOOR; refuses to let anything move unless the supply has recovered to
    MIN_VOLTS by the end.
    """
    first = volts(bus)
    lowest = now = min(first.values())
    per_motor = dict(first)
    for _ in range(int(SETTLE_S / 0.25)):
        sleep(0.25)
        reading = volts(bus)
        per_motor = {m: min(per_motor[m], v) for m, v in reading.items()}
        now = min(reading.values())
        lowest = min(lowest, now)
        if now < SETTLE_FLOOR:
            raise NudgeAbort(f"voltage collapsed to {now:.1f} V while torque came on (floor {SETTLE_FLOOR} V)")
    log(f"settled: lowest {lowest:.1f} V during torque-on, now {now:.1f} V")
    log("  lowest per motor: " + ", ".join(f"{m} {v:.1f}" for m, v in per_motor.items()))
    if now < MIN_VOLTS:
        raise NudgeAbort(f"voltage has not recovered after torque-on ({now:.1f} V, need {MIN_VOLTS} V)")
    return lowest


def hold_only(bus, seconds: float = 10.0, *, floor: float = 4.0, sleep=time.sleep, log=print) -> dict:
    """Hold the arm still and print the motor voltages twice a second; no joint moves. For supply diagnosis."""
    start = arm_hold(bus, sleep=sleep)
    lowest = min(volts(bus).values())
    for _ in range(int(seconds * 2)):
        sleep(0.5)
        v = volts(bus)
        lowest = min(lowest, min(v.values()))
        log(f"holding: lowest {min(v.values()):.1f} V, highest {max(v.values()):.1f} V")
        if min(v.values()) < floor:
            raise NudgeAbort(f"voltage fell to {min(v.values()):.1f} V (hold-only limit {floor} V)")
    return {"min_volts": lowest, "start": start}


def joint_limits(bus) -> dict[str, tuple[float, float]]:
    """Calibrated range of each joint in the degrees the bus reports (centred on the calibration midpoint).

    wrist_roll is a full-turn joint and has no limit. A bus without calibration data (a test fake) has none.
    """
    limits = {}
    for name, cal in getattr(bus, "calibration", {}).items():
        if name == "wrist_roll":
            continue
        half = (cal.range_max - cal.range_min) / 2 * 360 / 4095
        limits[name] = (-half, half)
    return limits


def check_moves(bus, moves) -> None:
    """Refuse a bad plan BEFORE torque comes on: unknown joint, too big, or past the calibrated range."""
    if not moves:
        raise ValueError("no moves given")
    present = bus.sync_read("Present_Position")
    limits = joint_limits(bus)
    for joint, degrees in moves:
        if joint not in NUDGE_JOINTS:
            raise ValueError(f"joint must be one of {NUDGE_JOINTS}")
        if not 0 < abs(degrees) <= MAX_DEGREES[joint]:
            raise ValueError(f"degrees must be non-zero and at most {MAX_DEGREES[joint]} either way for {joint}")
        if joint in limits:
            lo, hi = limits[joint]
            target = present[joint] + degrees
            if not lo - RANGE_MARGIN_DEG <= target <= hi + RANGE_MARGIN_DEG:
                raise ValueError(f"{joint} at {present[joint]:.1f} deg cannot move {degrees:+g}: target {target:.1f} is outside "
                                 f"its calibrated range {lo:.1f} to {hi:.1f}. Try {-degrees:+g}.")


def run_moves(bus, moves, *, sleep=time.sleep, log=print, torque_limit: int = TORQUE_LIMIT, trace: list | None = None) -> list[dict]:
    """Hold the arm once, then for each (joint, degrees): move, hold, return. One stats dict per completed move.

    If `trace` is a list, one row per control tick is appended: move, joint, command and observed degrees, and the voltage
    of every motor, so the voltage can be plotted against position afterwards (see write_trace).
    """
    check_moves(bus, moves)
    start = arm_hold(bus, sleep=sleep, torque_limit=torque_limit)
    lowest_hold = settle(bus, sleep=sleep, log=log)
    log(f"holding; lowest motor voltage {lowest_hold:.1f} V")
    results = []
    watch = SupplyWatch()
    t0 = time.monotonic()

    for joint, degrees in moves:
        stats = {"joint": joint, "degrees": degrees, "min_volts": lowest_hold, "max_lag_deg": 0.0, "start_deg": start[joint],
                 "move_min": float("inf"), "min_at_deg": None}
        lag_count = 0

        def go(target: float) -> None:
            nonlocal lag_count
            current = bus.sync_read("Present_Position")[joint]
            while abs(target - current) > 1e-9:
                command = current + max(-STEP_DEG, min(STEP_DEG, target - current))
                bus.sync_write("Goal_Position", {joint: command})
                sleep(PERIOD_S)
                observed = bus.sync_read("Present_Position")[joint]
                reading = volts(bus)
                if trace is not None:
                    trace.append({"t_s": round(time.monotonic() - t0, 3), "move": f"{joint}{degrees:+g}", "joint": joint,
                                  "command_deg": round(command, 2), "observed_deg": round(observed, 2),
                                  **{f"v_{m}": v for m, v in reading.items()}})
                motor_v, lowest = watch.check(reading, f" during {joint} {degrees:+g}")
                if lowest < stats["move_min"]:                      # the lowest during THIS move, and where the joint was
                    stats["move_min"], stats["min_at_deg"] = lowest, observed
                stats["min_volts"] = min(stats["min_volts"], lowest)
                stats["max_lag_deg"] = max(stats["max_lag_deg"], abs(command - observed))
                lag_count = lag_count + 1 if abs(command - observed) > STALL_DEG else 0
                if lag_count >= STALL_READS:
                    raise NudgeAbort(f"{joint} is not following its command (lag {abs(command - observed):.1f} deg)")
                current = command

        log(f"{joint}: {start[joint]:.1f} -> {start[joint] + degrees:.1f} deg")
        go(start[joint] + degrees)
        sleep(1.0)
        go(start[joint])
        sleep(1.0)
        where = f" (at {stats['min_at_deg']:.1f} deg)" if stats["min_at_deg"] is not None else ""
        log(f"{joint} {degrees:+g}: done, lowest {stats['min_volts']:.1f} V{where}, worst lag {stats['max_lag_deg']:.2f} deg")
        results.append(stats)
    return results


def write_trace(rows: list[dict], path: str) -> None:
    """Write the tick-by-tick trace as CSV (stdlib only)."""
    import csv
    if not rows:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def nudge(bus, joint: str, degrees: float, *, sleep=time.sleep, log=print) -> dict:
    """One move: `joint` by `degrees`, hold, return. Returns min voltage and worst lag seen."""
    return run_moves(bus, [(joint, degrees)], sleep=sleep, log=log)[0]


def parse_moves(text: str) -> list[tuple[str, float]]:
    """'wrist_roll:20,elbow_flex:-20' -> [('wrist_roll', 20.0), ('elbow_flex', -20.0)]"""
    moves = []
    for item in text.split(","):
        joint, _, degrees = item.strip().partition(":")
        try:
            moves.append((joint, float(degrees)))
        except ValueError:
            raise SystemExit(f"bad move '{item}': use joint:degrees, e.g. wrist_roll:20") from None
    return moves


def main(argv=None, *, ask=input) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", required=True)
    p.add_argument("--id", default="lookandpick", help="name of the calibration file (the one calibrate wrote)")
    p.add_argument("--joint", default="wrist_roll", choices=NUDGE_JOINTS)
    p.add_argument("--degrees", type=float, default=3.0)
    p.add_argument("--torque-limit", type=int, default=TORQUE_LIMIT, choices=range(100, 1001), metavar="100-1000",
                   help=f"Max_Torque_Limit for the arm joints (1000 = full); default {TORQUE_LIMIT}. The gripper stays at {TORQUE_LIMIT}.")
    p.add_argument("--trace", help="write a CSV of every tick (positions and all six motor voltages) to this path")
    p.add_argument("--moves", help="several moves in one held session, e.g. wrist_roll:20,wrist_flex:-20 (overrides --joint/--degrees)")
    p.add_argument("--hold-only", action="store_true", help="hold the arm still for 10 s and print voltages; moves nothing")
    a = p.parse_args(argv)
    moves = parse_moves(a.moves) if a.moves else [(a.joint, a.degrees)]
    for joint, degrees in moves:
        if joint not in NUDGE_JOINTS or not 0 < abs(degrees) <= MAX_DEGREES[joint]:
            raise SystemExit(f"each move must be one of {NUDGE_JOINTS} with 0 < |degrees| <= {MAX_DEGREES.get(joint)}; got {joint}:{degrees}")
    if a.port not in find_ports():
        raise SystemExit(f"{a.port} does not exist. Ports now: {find_ports() or 'none'}")
    require_present()
    robot = build_robot(a.port, a.id)
    if not robot.calibration:
        raise SystemExit(f"No calibration file for id '{a.id}'. Run so101_setup.py calibrate first.")
    robot.bus.connect()
    try:
        if not robot.bus.is_calibrated:
            raise SystemExit("The motors' calibration does not match the saved file; nothing was moved.")
        print("Positions (deg):", {k: round(v, 1) for k, v in robot.bus.sync_read("Present_Position").items()})
        print("Voltages (V):", volts(robot.bus))
        if a.hold_only:
            if ask("Arm folded at rest and supported by your hand? Type HOLD to turn torque on (nothing will move) "
                   "for 10 s:\n> ") != "HOLD":
                raise SystemExit("Not confirmed; torque stays off.")
            stats = hold_only(robot.bus)
            print(f"Done. Lowest voltage while holding {stats['min_volts']:.1f} V.")
            return
        try:
            check_moves(robot.bus, moves)
        except ValueError as exc:
            raise SystemExit(f"Refused before any torque: {exc}") from None
        plan = ", ".join(f"{j} {d:+g}" for j, d in moves)
        if ask(f"Arm folded at rest and supported by your hand? Type MOVE to turn torque on and run: {plan}\n> ") != "MOVE":
            raise SystemExit("Not confirmed; nothing was moved.")
        trace: list[dict] = []
        try:
            results = run_moves(robot.bus, moves, torque_limit=a.torque_limit, trace=trace)
        finally:
            if a.trace:
                write_trace(trace, a.trace)
                print(f"Trace written to {a.trace} ({len(trace)} rows).")
        print(f"Done: {len(results)} move(s). Lowest voltage {min(r['min_volts'] for r in results):.1f} V, "
              f"worst lag {max(r['max_lag_deg'] for r in results):.2f} deg.")
    except NudgeAbort as exc:
        print(f"ABORTED: {exc}")
    finally:
        try:
            failed = release_all(robot.bus)
        finally:
            robot.bus.disconnect(disable_torque=False)
        if not failed:
            print("Torque released on all six motors; the arm is limp. Support it.")


if __name__ == "__main__":
    main()

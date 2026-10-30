"""First powered move of the SO-101 follower: nudge ONE joint a few degrees, slowly, and come back.

    ~/src/lerobot-venv/bin/python scripts/so101_nudge.py --port /dev/ttyACM0 --id follower
    ~/src/lerobot-venv/bin/python scripts/so101_nudge.py --port /dev/ttyACM0 --id follower --joint wrist_roll --degrees 3

This is a bring-up test, not a planner: no kinematics, no Cartesian targets. It exists because LeRobot's
`connect()` turns torque on for every joint while a joint's stored goal may still be where it was before you
moved the arm by hand, so the arm can jump. Here every goal is first set to the CURRENT position while torque
is still off, then torque goes on, then one joint moves.

SAFETY: powers the servos. Liam must be physically present at an interactive terminal with the 5V kill switch
in reach (refuses over SSH or piped input). Put the arm in its folded rest pose first and keep a hand on it.
At most 5 degrees, 10 degrees/s, one joint. Aborts (and releases torque) on a stall, a voltage sag or Ctrl-C.
Torque is ALWAYS released at the end, so the arm goes limp: support it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from so101_setup import build_robot, find_ports, require_present  # noqa: E402

MAX_DEGREES = 5.0
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


def volts(bus) -> dict[str, float]:
    return {name: raw / 10 for name, raw in bus.sync_read("Present_Voltage", normalize=False).items()}


def arm_hold(bus) -> dict[str, float]:
    """Hold every joint where it is: goals = present while torque is off, gentle settings, then torque on."""
    bus.disable_torque()
    present = bus.sync_read("Present_Position")
    bus.sync_write("Goal_Position", present)
    for motor in present:
        bus.write("Acceleration", motor, ACCELERATION)
        bus.write("Max_Torque_Limit", motor, TORQUE_LIMIT)
    present = bus.sync_read("Present_Position")
    bus.sync_write("Goal_Position", present)
    bus.enable_torque()
    return present


def nudge(bus, joint: str, degrees: float, *, sleep=time.sleep, log=print) -> dict:
    """Move `joint` by `degrees` (|degrees| <= 5), hold, return. Returns min voltage and worst lag seen."""
    if joint not in NUDGE_JOINTS:
        raise ValueError(f"joint must be one of {NUDGE_JOINTS}")
    if not 0 < abs(degrees) <= MAX_DEGREES:
        raise ValueError(f"degrees must be non-zero and at most {MAX_DEGREES} either way")
    start = arm_hold(bus)
    stats = {"min_volts": min(volts(bus).values()), "max_lag_deg": 0.0, "start_deg": start[joint]}
    log(f"holding; {joint} at {start[joint]:.2f} deg, lowest motor voltage {stats['min_volts']:.1f} V")
    lag_count = 0

    def go(target: float) -> None:
        nonlocal lag_count
        current = bus.sync_read("Present_Position")[joint]
        while abs(target - current) > 1e-9:
            command = current + max(-STEP_DEG, min(STEP_DEG, target - current))
            bus.sync_write("Goal_Position", {joint: command})
            sleep(PERIOD_S)
            observed = bus.sync_read("Present_Position")[joint]
            lowest = min(volts(bus).values())
            stats["min_volts"] = min(stats["min_volts"], lowest)
            stats["max_lag_deg"] = max(stats["max_lag_deg"], abs(command - observed))
            if lowest < MIN_VOLTS:
                raise NudgeAbort(f"voltage fell to {lowest:.1f} V (limit {MIN_VOLTS} V)")
            lag_count = lag_count + 1 if abs(command - observed) > STALL_DEG else 0
            if lag_count >= STALL_READS:
                raise NudgeAbort(f"{joint} is not following its command (lag {abs(command - observed):.1f} deg)")
            current = command

    go(start[joint] + degrees)
    log(f"reached +{degrees} deg; holding 1 s")
    sleep(1.0)
    go(start[joint])
    log("back at start; holding 1 s")
    sleep(1.0)
    return stats


def main(argv=None, *, ask=input) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", required=True)
    p.add_argument("--id", default="lookandpick", help="name of the calibration file (the one calibrate wrote)")
    p.add_argument("--joint", default="wrist_roll", choices=NUDGE_JOINTS)
    p.add_argument("--degrees", type=float, default=3.0)
    a = p.parse_args(argv)
    if not 0 < abs(a.degrees) <= MAX_DEGREES:
        raise SystemExit(f"--degrees must be non-zero and at most {MAX_DEGREES} either way")
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
        if ask(f"Arm folded at rest and supported by your hand? Type MOVE to turn torque on and nudge "
               f"{a.joint} by {a.degrees} deg:\n> ") != "MOVE":
            raise SystemExit("Not confirmed; nothing was moved.")
        stats = nudge(robot.bus, a.joint, a.degrees)
        print(f"Done. Lowest voltage {stats['min_volts']:.1f} V, worst lag {stats['max_lag_deg']:.2f} deg.")
    except NudgeAbort as exc:
        print(f"ABORTED: {exc}")
    finally:
        try:
            robot.bus.disable_torque()
        finally:
            robot.bus.disconnect(disable_torque=False)
        print("Torque released; the arm is limp. Support it.")


if __name__ == "__main__":
    main()

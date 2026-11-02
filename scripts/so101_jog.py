"""Jog the real SO-101 follower with a game controller (Linux joystick API, standard library only).

    ~/src/lerobot-venv/bin/python scripts/so101_jog.py --probe                       # no arm: show what the controller reports
    ~/src/lerobot-venv/bin/python scripts/so101_jog.py --port /dev/ttyACM0 --id follower

Xbox layout (as the kernel's xpad driver reports it; check with --probe):
    hold LB (dead-man)         any motion needs this held; let go and every joint holds where it is
    left stick  X / Y          shoulder_pan / shoulder_lift
    right stick X / Y          wrist_roll / elbow_flex
    D-pad up / down            wrist_flex
    RB (while moving)          fine mode, a third of the speed
    X (with LB held)           ramp every joint back to where this session started
    B or Start                 stop now: releases torque and exits (the arm goes limp: support it)
Pushing a stick up or right moves the joint in the POSITIVE direction of the degrees the other scripts use.

Purpose: find each joint's direction and usable range by eye, slowly, with a person in the loop. Not a planner.

SAFETY: powers the servos. Liam must be physically present at an interactive terminal with the 5V kill switch in reach
(refuses over SSH or piped input). Fold the arm to its rest pose first. At most 10 degrees/s, never past a joint's
calibrated range, and a joint that cannot follow its command (a table, a cable) stops pushing that way instead of
fighting it. The supply is checked every few ticks; a sag below 4.5 V releases everything. Torque is released motor by
motor at the end; if any release cannot be confirmed the script says so and you cut the supply.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from so101_nudge import (MIN_VOLTS, STALL_DEG, STALL_READS, TORQUE_LIMIT, NudgeAbort, RANGE_MARGIN_DEG,  # noqa: E402
                         arm_hold, joint_limits, lowest_motor, release_all, settle, volts)
from so101_setup import build_robot, find_ports, require_present  # noqa: E402

MAX_SPEED_DEG_S = 10.0
FINE_DIVISOR = 3.0
DEADZONE = 0.15
TICK_S = 0.05
VOLT_EVERY = 4                  # read the supply every 4th tick (a bus read costs time)

# joint -> (axis number, sign). Sticks report "up" as negative, so up = positive degrees needs sign -1 on Y axes.
JOINT_AXES = {"shoulder_pan": (0, +1), "shoulder_lift": (1, -1), "elbow_flex": (4, -1),
              "wrist_roll": (3, +1), "wrist_flex": (7, -1)}
DEADMAN, FINE, HOME, STOP_B, STOP_START = 4, 5, 2, 1, 7

EVENT = struct.Struct("IhBB")   # time ms, value, type, number  (linux/joystick.h)
JS_BUTTON, JS_AXIS, JS_INIT = 0x01, 0x02, 0x80


class ControllerLost(RuntimeError):
    pass


def read_events(fd: int) -> list[tuple[int, int, int]]:
    """All pending (type, number, value) events; [] when none; ControllerLost if the device went away."""
    try:
        data = os.read(fd, EVENT.size * 64)
    except BlockingIOError:
        return []
    except OSError as exc:
        raise ControllerLost(str(exc)) from None
    if not data:
        raise ControllerLost("controller stopped sending")
    return [(t & ~JS_INIT, n, v) for _, v, t, n in EVENT.iter_unpack(data[:len(data) // EVENT.size * EVENT.size])]


class Pad:
    """Latest axis (-1..1) and button (bool) state."""

    def __init__(self):
        self.axes: dict[int, float] = {}
        self.buttons: dict[int, bool] = {}

    def feed(self, events) -> list[str]:
        notes = []
        for kind, number, value in events:
            if kind == JS_AXIS:
                self.axes[number] = max(-1.0, min(1.0, value / 32767))
            elif kind == JS_BUTTON:
                self.buttons[number] = bool(value)
                notes.append(f"button {number} {'down' if value else 'up'}")
        return notes

    def axis(self, number: int) -> float:
        v = self.axes.get(number, 0.0)
        return 0.0 if abs(v) < DEADZONE else v

    def held(self, number: int) -> bool:
        return self.buttons.get(number, False)


class Jog:
    """Pure jog logic: where each joint's goal goes this tick. No I/O, so it is testable with a fake pad."""

    def __init__(self, start: dict[str, float], limits: dict[str, tuple[float, float]]):
        self.start, self.limits = dict(start), limits
        self.goal = dict(start)
        self.lag = {j: 0 for j in JOINT_AXES}
        self.blocked = {j: 0 for j in JOINT_AXES}     # sign of the direction that stalled; 0 = free
        self.reached = {j: [v, v] for j, v in start.items()}
        self.note: str | None = None

    def tick(self, pad: Pad, present: dict[str, float], dt: float = TICK_S) -> dict[str, float]:
        """Returns the goals that changed this tick."""
        for joint, v in present.items():
            lo, hi = self.reached[joint]
            self.reached[joint] = [min(lo, v), max(hi, v)]
        self.note = None
        if not pad.held(DEADMAN):
            return {}
        speed = MAX_SPEED_DEG_S / (FINE_DIVISOR if pad.held(FINE) else 1.0)
        changed = {}
        for joint, (axis, sign) in JOINT_AXES.items():
            if pad.held(HOME):
                delta = self.start[joint] - self.goal[joint]
                step = max(-speed * dt, min(speed * dt, delta))
                direction = 0
            else:
                direction = sign * pad.axis(axis)
                step = direction * speed * dt
            if self.blocked[joint] and (direction == 0 or direction * self.blocked[joint] < 0):
                self.blocked[joint] = 0
            elif self.blocked[joint]:
                step = 0.0
            # Only a joint being driven can be "blocked"; one merely sagging under gravity while idle is not.
            driven = step != 0.0
            if driven and abs(present[joint] - self.goal[joint]) > STALL_DEG:
                self.lag[joint] += 1
            else:
                self.lag[joint] = 0
            if self.lag[joint] >= STALL_READS:
                self.goal[joint] = present[joint]
                changed[joint] = present[joint]
                self.lag[joint] = 0
                self.blocked[joint] = 1 if direction > 0 else -1 if direction < 0 else 0
                self.note = f"{joint} blocked: it stopped following; pushing that way is ignored until you release the stick"
                continue
            if step == 0.0:
                continue
            new = self.goal[joint] + step
            if joint in self.limits:
                lo, hi = self.limits[joint]
                lo, hi = lo + RANGE_MARGIN_DEG, hi - RANGE_MARGIN_DEG
                if step > 0 and new > hi:
                    new = max(self.goal[joint], hi)
                elif step < 0 and new < lo:
                    new = min(self.goal[joint], lo)
            if new != self.goal[joint]:
                self.goal[joint] = new
                changed[joint] = new
        return changed


def probe(device: str) -> None:
    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    pad = Pad()
    print(f"Reading {device}. Press buttons and move sticks; Ctrl-C to stop. Nothing else is connected or moved.")
    last = {}
    try:
        while True:
            for note in pad.feed(read_events(fd)):
                print(note)
            for number, value in pad.axes.items():
                if abs(value - last.get(number, 0.0)) > 0.4:
                    print(f"axis {number} = {value:+.2f}")
                    last[number] = value
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)


def run(bus, fd: int, *, sleep=time.sleep, log=print, torque_limit: int = TORQUE_LIMIT) -> dict:
    limits = joint_limits(bus)
    start = arm_hold(bus, sleep=sleep, torque_limit=torque_limit)
    jog = Jog(start, limits)
    pad = Pad()
    lowest = settle(bus, sleep=sleep, log=log)
    log("Holding. Hold LB and move the sticks. B or Start stops and releases.")
    ticks = 0
    while True:
        pad.feed(read_events(fd))
        if pad.held(STOP_B) or pad.held(STOP_START):
            break
        present = bus.sync_read("Present_Position")
        changed = jog.tick(pad, present)
        if changed:
            bus.sync_write("Goal_Position", changed)
        if jog.note:
            log(jog.note)
        ticks += 1
        if ticks % VOLT_EVERY == 0:
            reading = volts(bus)
            motor_v, now = lowest_motor(reading)
            lowest = min(lowest, now)
            if now < MIN_VOLTS:
                raise NudgeAbort(f"voltage fell to {now:.1f} V on {motor_v} (limit {MIN_VOLTS} V); all motors: "
                                 + ", ".join(f"{m} {v:.1f}" for m, v in reading.items()))
        sleep(TICK_S)
    return {"reached": jog.reached, "min_volts": lowest, "start": start}


def main(argv=None, *, ask=input) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port")
    p.add_argument("--id", default="lookandpick", help="name of the calibration file (the one calibrate wrote)")
    p.add_argument("--device", default="/dev/input/js0")
    p.add_argument("--torque-limit", type=int, default=TORQUE_LIMIT, choices=range(100, 1001), metavar="100-1000",
                   help=f"Max_Torque_Limit for the arm joints (1000 = full); default {TORQUE_LIMIT}. The gripper stays at {TORQUE_LIMIT}.")
    p.add_argument("--probe", action="store_true", help="only show what the controller reports; no arm")
    a = p.parse_args(argv)
    if not os.path.exists(a.device):
        raise SystemExit(f"{a.device} not found. Plug the controller in (wired is simplest) and try again.")
    if a.probe:
        return probe(a.device)
    if not a.port:
        raise SystemExit("--port is required (e.g. /dev/ttyACM0)")
    if a.port not in find_ports():
        raise SystemExit(f"{a.port} does not exist. Ports now: {find_ports() or 'none'}")
    require_present()
    robot = build_robot(a.port, a.id)
    if not robot.calibration:
        raise SystemExit(f"No calibration file for id '{a.id}'. Run so101_setup.py calibrate first.")
    fd = os.open(a.device, os.O_RDONLY | os.O_NONBLOCK)
    robot.bus.connect()
    failed = []
    try:
        if not robot.bus.is_calibrated:
            raise SystemExit("The motors' calibration does not match the saved file; nothing was moved.")
        print("Positions (deg):", {k: round(v, 1) for k, v in robot.bus.sync_read("Present_Position").items()})
        print("Voltages (V):", volts(robot.bus))
        if ask("Arm folded at rest and supported by your hand? Type JOG to turn torque on and start:\n> ") != "JOG":
            raise SystemExit("Not confirmed; nothing was moved.")
        result = run(robot.bus, fd, torque_limit=a.torque_limit)
        print("Range each joint reached this session (deg):")
        for joint, (lo, hi) in result["reached"].items():
            print(f"  {joint:14s} {lo:7.1f} to {hi:7.1f}  (started at {result['start'][joint]:.1f})")
        print(f"Lowest motor voltage {result['min_volts']:.1f} V.")
    except NudgeAbort as exc:
        print(f"ABORTED: {exc}")
    except ControllerLost as exc:
        print(f"CONTROLLER LOST ({exc}): releasing torque.")
    finally:
        try:
            failed = release_all(robot.bus)
        finally:
            robot.bus.disconnect(disable_torque=False)
            os.close(fd)
        if not failed:
            print("Torque released on all six motors; the arm is limp. Support it.")


if __name__ == "__main__":
    main()

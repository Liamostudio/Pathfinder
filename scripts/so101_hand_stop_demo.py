"""Hand-stop demo on the REAL SO-101: a tiny slow sweep that freezes when a hand comes near, and folds back when it cannot go on.

    ~/src/lerobot-venv/bin/python scripts/so101_hand_stop_demo.py --port /dev/ttyACM0 --id follower --check
    ~/src/lerobot-venv/bin/python scripts/so101_hand_stop_demo.py --port /dev/ttyACM0 --id follower --hold-only
    ~/src/lerobot-venv/bin/python scripts/so101_hand_stop_demo.py --port /dev/ttyACM0 --id follower

The live Deck (hands environment, port 8765) must be running with both cameras: it owns the cameras and runs the hand gate, and this
script reads the hands it publishes on loopback (127.0.0.1 only) and does its own speed-and-separation maths against the REAL arm's tip.
No answer, an old answer or a stale gate reads as "cannot see": the arm freezes and does not fold. See lookandpick/real_arm_demo.py.

Keys while it runs:  SPACE stop (holds still, torque on)   r resume   f fold back now   q fold back then exit   x release torque
Ctrl-C is a SPACE; a second Ctrl-C within 3 s releases torque.   THE 5V KILL SWITCH IS THE TRUE EMERGENCY STOP.

Modes:
  --check      read-only: positions, supply voltage, the Deck's hands, the sweep plan. Torque stays OFF, nothing moves.
  --hold-only  torque on with goals = present for ~10 s, nothing moves, supply voltage printed (the bench "hold test").
  (neither)    the sweep, after you type SWEEP.

SAFETY: powers the servos. Liam must be physically present at an interactive terminal with the 5V kill switch in reach (refuses over
SSH or piped input). Read ~5 V with a multimeter at the adapter terminals BEFORE connecting the URT board; 5V 10A supply only, never
above 7.4 V. Put the arm in its folded rest pose and keep a hand near it. At most 3 deg/s on one joint by default (the commissioned
ceiling is 10 deg/s), never past a joint's calibrated range. The supply is checked every few ticks; a collapse releases torque and says so.
"""
from __future__ import annotations

import argparse
import os
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from so101_nudge import NudgeAbort, SupplyWatch, arm_hold, hold_only, release_all, settle, volts  # noqa: E402
from so101_setup import build_robot, find_ports, require_present  # noqa: E402

from lookandpick.real_arm_demo import DeckHandSource, DemoConfig, DemoRefused, HandStopDemo  # noqa: E402

TORQUE_LIMIT = 500


class BusRig:
    """The Rig the demo drives, on a connected LeRobot bus: reads, goal writes, supply voltage, motor-by-motor torque release."""

    def __init__(self, bus):
        self.bus = bus

    def positions(self):
        return self.bus.sync_read("Present_Position")

    def write_goals(self, goals):
        self.bus.sync_write("Goal_Position", goals)

    def volts(self):
        return volts(self.bus)

    def release(self):
        return release_all(self.bus)


class Keys:
    """Single keypresses from a local terminal, no Enter needed. Restores the terminal on exit."""

    def __init__(self):
        import termios
        import tty
        self._termios, self._fd = termios, sys.stdin.fileno()
        self._saved = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)                  # Ctrl-C still raises KeyboardInterrupt

    def poll(self):
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return None

    def close(self):
        self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._saved)


def build_geometry():
    """Calibrated FK, the measured arm-base frame, the commissioned limits: the same objects the rest of the system uses."""
    from lookandpick.adapters.so101_profile import TOUCH_CALIBRATION, calibrated_kinematics, commissioned_limits
    from lookandpick.kinematics import BaseFrame
    limits = commissioned_limits()
    kin = calibrated_kinematics(limits=limits)
    frame = BaseFrame.from_calibration(str(TOUCH_CALIBRATION))

    def tip_of(joints):
        return frame.to_marker(tuple(kin.forward(joints)))

    def inside_workspace(joints):
        return limits.contains(tuple(kin.forward(joints)))

    return tip_of, inside_workspace, dict(limits.joint_ranges)


def make_demo(rig, hands, config, *, keys=lambda: None, supply_check=lambda v: None, sleep=time.sleep, log=print):
    tip_of, inside_workspace, ranges = build_geometry()
    return HandStopDemo(rig, hands, tip_of=tip_of, inside_workspace=inside_workspace, joint_ranges=ranges, supply_check=supply_check,
                        config=config, keys=keys, sleep=sleep, log=log)


def watch_pose(rig, hands, config, *, sleep=time.sleep, out=print, limit=None):
    """Read-only: show where the arm is and whether the sweep would be accepted from here, until Ctrl-C (or `limit` lines)."""
    tip_of, _, _ = build_geometry()
    out("Move the arm by hand (torque is OFF). The line says OK once the sweep plan would be accepted. Ctrl-C to finish.")
    shown = 0
    try:
        while limit is None or shown < limit:
            pos = rig.positions()
            tip = tip_of(pos)
            try:
                make_demo(rig, hands, config)
                verdict = "OK: sweep plan accepted from here"
            except DemoRefused as exc:
                verdict = "not yet: " + str(exc).split(".")[0][:90]
            sight = "seeing" if hands.read().ok else "BLIND"
            out("\r\x1b[2K" + "pan %6.1f lift %6.1f elbow %6.1f wrist %6.1f | tip %.2f %.2f %.2f | %s | %s"
                % (pos["shoulder_pan"], pos["shoulder_lift"], pos["elbow_flex"], pos["wrist_flex"], *tip, sight, verdict), end="", flush=True) \
                if out is print else out(f"{pos['wrist_flex']:.1f} {verdict}")
            shown += 1
            sleep(0.3)
    except KeyboardInterrupt:
        out("")


def wait_for_sight(hands, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        reading = hands.read()
        if reading.ok:
            return reading
        time.sleep(0.05)
    return hands.read()


def main(argv=None, *, ask=input) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", required=True)
    p.add_argument("--id", default="lookandpick", help="name of the calibration file (the one calibrate wrote)")
    p.add_argument("--deck-url", default="http://127.0.0.1:8765", help="the live Deck (loopback only)")
    p.add_argument("--amplitude-cm", type=float, default=4.0, help="tip travel each side of the start (default 4, at most 6)")
    p.add_argument("--cycles", type=int, default=2, choices=range(1, 11), metavar="1-10")
    p.add_argument("--continuous", action="store_true", help="keep sweeping until you press q or f (ignores --cycles)")
    p.add_argument("--work-area", type=float, nargs=4, metavar=("X0", "X1", "Y0", "Y1"),
                   help="marker-frame rectangle in metres: any hand inside it freezes the arm, however far from the tip")
    p.add_argument("--speed", type=float, default=0.3, help="fraction of the commissioned 10 deg/s (default 0.3, at most 1.0)")
    p.add_argument("--torque-limit", type=int, default=TORQUE_LIMIT, choices=range(100, 1001), metavar="100-1000")
    p.add_argument("--status-port", type=int, default=0, help="serve the demo's gate state on this loopback port for Deck's hand-gate panel (0 = off)")
    p.add_argument("--watch", action="store_true", help="read-only live view while you move the arm by hand: positions, tip, and whether the sweep plan "
                   "would be accepted. Torque stays off. Ctrl-C to finish.")
    p.add_argument("--check", action="store_true", help="read-only check: torque stays off, nothing moves")
    p.add_argument("--hold-only", action="store_true", help="torque on, goals = present for ~10 s, nothing moves; prints the supply voltage")
    a = p.parse_args(argv)
    try:
        config = DemoConfig(amplitude_m=a.amplitude_cm / 100, cycles=a.cycles, speed_fraction=a.speed, continuous=a.continuous,
                            work_area=tuple(a.work_area) if a.work_area else None)
    except ValueError as exc:
        raise SystemExit(f"Refused: {exc}") from None
    if a.port not in find_ports():
        raise SystemExit(f"{a.port} does not exist. Ports now: {find_ports() or 'none'}")
    require_present()
    robot = build_robot(a.port, a.id)
    if not robot.calibration:
        raise SystemExit(f"No calibration file for id '{a.id}'. Run so101_setup.py calibrate first.")
    hands = DeckHandSource(a.deck_url)
    robot.bus.connect()
    keys, demo = None, None
    failed: list[str] = []
    try:
        if not robot.bus.is_calibrated:
            raise SystemExit("The motors' calibration does not match the saved file; nothing was moved.")
        rig = BusRig(robot.bus)
        print("Positions (deg):", {k: round(v, 1) for k, v in rig.positions().items()})
        print("Voltages (V):", rig.volts())
        hands.start()
        sight = wait_for_sight(hands)
        print(f"Deck hands: {'seeing, ' + str(len(sight.hands)) + ' hand(s)' if sight.ok else 'CANNOT SEE (' + sight.why + ')'}")
        if a.watch:
            watch_pose(rig, hands, config)
            return
        try:
            demo = make_demo(rig, hands, config)
        except DemoRefused as exc:
            raise SystemExit(f"Refused before any torque: {exc}") from None
        print("Plan:", demo.plan_summary())
        if a.check:
            print("Check only: torque was never turned on.")
            return
        if a.hold_only:
            if ask("Arm folded at rest and supported by your hand? Type HOLD to turn torque on (nothing will move) for 10 s:\n> ") != "HOLD":
                raise SystemExit("Not confirmed; torque stays off.")
            print(f"Done. Lowest voltage while holding {hold_only(robot.bus, 10)['min_volts']:.1f} V.")
            return
        if not sight.ok:
            raise SystemExit("Refused: the Deck's hand gate is not giving fresh hands. Start the live Deck with both cameras first; "
                             "the arm never moves blind.")
        if ask("Arm folded at rest, a hand near it, kill switch in reach? Type SWEEP to turn torque on and start:\n> ") != "SWEEP":
            raise SystemExit("Not confirmed; nothing was moved.")
        arm_hold(robot.bus, torque_limit=a.torque_limit)
        lowest = settle(robot.bus)
        watch = SupplyWatch()
        keys = Keys()
        last_print = [0.0]

        def sleep_and_show(seconds):
            time.sleep(seconds)
            if time.monotonic() - last_print[0] > 0.5:
                last_print[0] = time.monotonic()
                print("\r\x1b[2K" + demo.status_line()[:200], end="", flush=True)

        demo = make_demo(rig, hands, config, keys=keys.poll, supply_check=lambda r: watch.check(r), sleep=sleep_and_show,
                         log=lambda *m: print("\r\x1b[2K" + " ".join(map(str, m))))
        if a.status_port:
            from lookandpick.status_server import serve
            serve(demo.status_json, None, a.status_port)
            print(f"Gate state served on http://127.0.0.1:{a.status_port}/api/hand-gate")
        print(f"Holding; lowest {lowest:.1f} V. SPACE stops, r resumes, f folds, q folds and exits, x releases. Running.")
        demo.run()
        print()
    except NudgeAbort as exc:
        print(f"ABORTED: {exc}")
    finally:
        if keys is not None:
            keys.close()
        hands.close()
        try:
            if demo is not None and demo.released:                 # the demo already let go (folded, key or fault) and reported any failure
                failed = demo.release_failed
            else:
                failed = release_all(robot.bus)
        finally:
            robot.bus.disconnect(disable_torque=False)
        if not failed:
            print("Torque is off on all six motors; the arm is limp. Support it.")


if __name__ == "__main__":
    main()

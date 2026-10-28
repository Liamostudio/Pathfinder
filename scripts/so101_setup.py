"""Set motor IDs and calibrate the SO-101 follower WITHOUT LeRobot's command-line tools.

    ~/src/lerobot-venv/bin/python scripts/so101_setup.py find-ports
    ~/src/lerobot-venv/bin/python scripts/so101_setup.py setup-motors --port /dev/ttyACM0
    ~/src/lerobot-venv/bin/python scripts/so101_setup.py calibrate --port /dev/ttyACM0

Why this exists: `lerobot-setup-motors`, `lerobot-calibrate` and `lerobot-teleoperate` crash on Python 3.14
(their argument parser, draccus, fails with "str | None is not callable"). The work they do is two method calls
on the follower object, which this script makes directly. It does exactly what LeRobot's own scripts do: the
interactive prompts, motor order and calibration file are LeRobot's.

SAFETY: `setup-motors` and `calibrate` write to the servos (IDs, baud rate, calibration) and `calibrate`
releases torque so you can move the arm by hand. Both need Liam physically present at an interactive terminal
with the 5V kill switch in reach, and refuse over SSH or when input is piped. Connect ONE motor at a time for
setup-motors, exactly as it prompts. Supply: 5V only, never above 7.4V; check ~5V with a multimeter first.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

CONFIRM = "5V KILL SWITCH READY"


def require_present(stdin=None, stdout=None, environ=None, ask=input) -> None:
    """Refuse unless this is a local, interactive terminal and the operator types the confirmation."""
    stdin, stdout, environ = stdin or sys.stdin, stdout or sys.stdout, os.environ if environ is None else environ
    if not (stdin.isatty() and stdout.isatty()):
        raise SystemExit("Refusing: this writes to the servos and needs an interactive terminal with Liam present.")
    if any(k in environ for k in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        raise SystemExit("Refusing: never run servo setup over SSH or any remote session.")
    answer = ask(f"With the 5V kill switch in reach, type exactly: {CONFIRM}\n> ")
    if answer != CONFIRM:
        raise SystemExit("Confirmation not given; nothing was done.")


def build_robot(port: str, robot_id: str):
    """The follower object LeRobot's own scripts build; creating it does not open the serial port."""
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig  # noqa: PLC0415
    return SO101Follower(SO101FollowerConfig(port=port, id=robot_id))


def find_ports() -> list[str]:
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find-ports", help="list serial ports (opens nothing); run before and after plugging the URT board in")
    for name, text in (("setup-motors", "set each motor's ID and baud rate, one motor at a time"),
                       ("calibrate", "LeRobot's manual range-of-motion calibration (releases torque)")):
        s = sub.add_parser(name, help=text)
        s.add_argument("--port", required=True)
        s.add_argument("--id", default="lookandpick", help="name of the calibration file")
    a = p.parse_args(argv)

    if a.cmd == "find-ports":
        ports = find_ports()
        print("\n".join(ports) if ports else "No serial ports found. Is the URT board plugged in and powered?")
        return
    if a.port not in find_ports():
        raise SystemExit(f"{a.port} does not exist. Ports now: {find_ports() or 'none'}")
    require_present()
    robot = build_robot(a.port, a.id)
    if a.cmd == "setup-motors":
        robot.setup_motors()
    else:
        robot.bus.connect()  # opens the port; calibrate() manages torque itself
        try:
            robot.calibrate()
        finally:
            robot.bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    main()

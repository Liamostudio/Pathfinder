"""The first-move script holds the arm first, moves one joint in small steps and always lets go.

A fake bus stands in for the servos; nothing here opens a serial port."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("so101_nudge", Path(__file__).parent.parent / "scripts" / "so101_nudge.py")
nudge_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nudge_mod)

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")


class FakeBus:
    def __init__(self, volts=55, follow=True, stale_goal=None):
        self.pos = {j: 10.0 * i for i, j in enumerate(JOINTS)}
        self.goal = dict(stale_goal or self.pos)
        self.volts, self.follow = volts, follow
        self.torque = False
        self.events = []
        self.motors = {j: None for j in JOINTS}
        self.fail_release = set()
        self.released = set()

    def sync_read(self, name, normalize=True):
        if name == "Present_Voltage":
            return {j: self.volts for j in JOINTS}
        return dict(self.pos)

    def sync_write(self, name, values):
        assert name == "Goal_Position"
        self.goal.update(values)
        self.events.append(("goal", dict(values), self.torque))
        if self.follow and self.torque:
            self.pos.update(values)

    def write(self, name, motor, value):
        self.events.append((name, motor, value))

    def enable_torque(self):
        self.torque = True
        self.events.append(("torque_on", dict(self.goal)))

    def disable_torque(self, motor=None):
        if motor in self.fail_release:
            raise RuntimeError("Overload error!")
        self.torque = False
        self.released.update(self.motors if motor is None else [motor])
        self.events.append(("torque_off",))


def run(bus, joint="wrist_roll", degrees=3.0):
    return nudge_mod.nudge(bus, joint, degrees, sleep=lambda _: None, log=lambda *_: None)


def test_goals_equal_present_before_torque_comes_on_even_if_stale():
    bus = FakeBus(stale_goal={j: 99.0 for j in JOINTS})  # the motor still remembers an old goal
    run(bus)
    on = next(e for e in bus.events if e[0] == "torque_on")
    assert on[1] == {j: 10.0 * i for i, j in enumerate(JOINTS)}


def test_moves_one_joint_in_small_steps_and_returns():
    bus = FakeBus()
    start = dict(bus.pos)
    stats = run(bus, "wrist_flex", 3.0)
    commands = [e[1]["wrist_flex"] for e in bus.events if e[0] == "goal" and e[2] and "wrist_flex" in e[1]]
    steps = [b - a for a, b in zip([start["wrist_flex"]] + commands, commands)]
    assert max(abs(s) for s in steps) <= nudge_mod.STEP_DEG + 1e-9
    assert max(commands) == pytest.approx(start["wrist_flex"] + 3.0)
    assert bus.pos == start and stats["min_volts"] == pytest.approx(5.5)
    assert all(set(e[1]) == {"wrist_flex"} for e in bus.events if e[0] == "goal" and e[2])


def test_refuses_more_than_five_degrees_and_unknown_or_gripper_joints():
    for bad in (0, 5.1, -6):
        with pytest.raises(ValueError):
            run(FakeBus(), degrees=bad)
    for joint in ("gripper", "nonsense"):
        with pytest.raises(ValueError):
            run(FakeBus(), joint=joint)


def test_aborts_when_the_joint_does_not_follow():
    with pytest.raises(nudge_mod.NudgeAbort, match="not following"):
        run(FakeBus(follow=False), degrees=5.0)


def test_aborts_when_voltage_sags():
    with pytest.raises(nudge_mod.NudgeAbort, match="voltage"):
        run(FakeBus(volts=40))


def test_gentle_settings_are_written_to_every_motor():
    bus = FakeBus()
    run(bus)
    accel = {e[1] for e in bus.events if e[0] == "Acceleration" and e[2] == nudge_mod.ACCELERATION}
    torque = {e[1] for e in bus.events if e[0] == "Max_Torque_Limit" and e[2] == nudge_mod.TORQUE_LIMIT}
    assert accel == torque == set(JOINTS)


def test_main_refuses_without_a_terminal_or_port(monkeypatch):
    monkeypatch.setattr(nudge_mod, "find_ports", lambda: [])
    with pytest.raises(SystemExit, match="does not exist"):
        nudge_mod.main(["--port", "/dev/ttyACM0"])
    monkeypatch.setattr(nudge_mod, "find_ports", lambda: ["/dev/ttyACM0"])
    with pytest.raises(SystemExit, match="interactive"):
        nudge_mod.main(["--port", "/dev/ttyACM0"])  # pytest's stdin is not a terminal
    with pytest.raises(SystemExit, match="at most"):
        nudge_mod.main(["--port", "/dev/ttyACM0", "--degrees", "20"])


def test_hold_only_moves_nothing_and_reports_the_voltage():
    bus = FakeBus()
    start = dict(bus.pos)
    stats = nudge_mod.hold_only(bus, 2.0, sleep=lambda _: None, log=lambda *_: None)
    assert bus.pos == start and stats["min_volts"] == pytest.approx(5.5)
    assert [e for e in bus.events if e[0] == "goal" and e[2]] == []  # no goal written after torque came on


def test_hold_only_aborts_on_a_deep_sag():
    with pytest.raises(nudge_mod.NudgeAbort, match="hold-only"):
        nudge_mod.hold_only(FakeBus(volts=38), 2.0, sleep=lambda _: None, log=lambda *_: None)


def test_one_motor_failing_to_release_does_not_stop_the_others():
    bus = FakeBus()
    bus.fail_release = {"elbow_flex"}
    logged = []
    failed = nudge_mod.release_all(bus, log=logged.append)
    assert failed == ["elbow_flex"]
    assert bus.released == set(JOINTS) - {"elbow_flex"}  # motors after the failing one were still released
    assert any("CUT THE 5V SUPPLY" in line for line in logged)


def test_clean_release_reports_no_failures():
    bus = FakeBus()
    assert nudge_mod.release_all(bus, log=lambda *_: None) == []
    assert bus.released == set(JOINTS)

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

    def enable_torque(self, motor=None):
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


def test_ceiling_and_no_gripper_or_unknown_joints():
    for joint, bad in (("shoulder_lift", 20.1), ("elbow_flex", -21), ("wrist_flex", 25), ("wrist_roll", 0)):
        with pytest.raises(ValueError):
            run(FakeBus(), joint=joint, degrees=bad)
    for joint in ("wrist_roll", "wrist_flex", "shoulder_pan", "elbow_flex", "shoulder_lift"):
        run(FakeBus(), joint, 20.0)
    for joint in ("gripper", "nonsense"):
        with pytest.raises(ValueError):
            run(FakeBus(), joint=joint)


class Cal:
    def __init__(self, lo, hi):
        self.range_min, self.range_max = lo, hi


def banded(bus, **positions):
    """Give the fake the calibration an arm built from 2000-tick ranges has: +-87.9 deg about zero."""
    bus.calibration = {j: Cal(1000, 3000) for j in JOINTS}
    bus.pos.update(positions)
    return bus


def test_a_joint_resting_at_the_end_of_its_range_can_only_go_the_other_way():
    bus = banded(FakeBus(), elbow_flex=87.0)
    with pytest.raises(ValueError, match="calibrated range.*Try -20"):
        run(bus, "elbow_flex", 20.0)
    assert not bus.torque and all(e[0] != "torque_on" for e in bus.events)  # refused before any torque
    run(bus, "elbow_flex", -20.0)


def test_wrist_roll_has_no_range_limit():
    run(banded(FakeBus(), wrist_roll=175.0), "wrist_roll", 20.0)


def test_sequence_holds_once_runs_every_move_and_returns_to_start():
    bus = banded(FakeBus())
    start = dict(bus.pos)
    results = nudge_mod.run_moves(bus, [("wrist_roll", 20.0), ("wrist_flex", -10.0), ("shoulder_pan", 20.0)],
                                  sleep=lambda _: None, log=lambda *_: None)
    assert [r["joint"] for r in results] == ["wrist_roll", "wrist_flex", "shoulder_pan"]
    assert sum(e[0] == "torque_on" for e in bus.events) == len(JOINTS)  # one hold, torque on motor by motor
    assert bus.pos == start


def test_a_bad_move_anywhere_in_the_plan_refuses_the_whole_plan_before_torque():
    bus = banded(FakeBus(), elbow_flex=87.0)
    with pytest.raises(ValueError):
        nudge_mod.run_moves(bus, [("wrist_roll", 20.0), ("elbow_flex", 20.0)], sleep=lambda _: None, log=lambda *_: None)
    assert all(e[0] != "torque_on" for e in bus.events)


def test_an_abort_mid_sequence_stops_later_moves():
    bus = FakeBus(volts=55)
    calls = []
    real = bus.sync_write
    def spy(name, values):
        calls.append(tuple(values))
        if len(calls) == 60:
            bus.volts = 40
        real(name, values)
    bus.sync_write = spy
    with pytest.raises(nudge_mod.NudgeAbort, match="voltage"):
        nudge_mod.run_moves(bus, [("wrist_roll", 20.0), ("wrist_flex", 20.0)], sleep=lambda _: None, log=lambda *_: None)
    assert ("wrist_flex",) not in calls


def test_parse_moves():
    assert nudge_mod.parse_moves("wrist_roll:20, elbow_flex:-20") == [("wrist_roll", 20.0), ("elbow_flex", -20.0)]
    with pytest.raises(SystemExit):
        nudge_mod.parse_moves("wrist_roll")


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


def test_torque_limit_option_applies_to_the_arm_joints_but_never_the_gripper():
    bus = FakeBus()
    nudge_mod.run_moves(bus, [("wrist_roll", 5.0)], sleep=lambda _: None, log=lambda *_: None, torque_limit=1000)
    caps = {e[1]: e[2] for e in bus.events if e[0] == "Max_Torque_Limit"}
    assert caps["gripper"] == nudge_mod.TORQUE_LIMIT
    assert all(v == 1000 for k, v in caps.items() if k != "gripper")


def test_a_voltage_abort_names_the_motor():
    class OneLow(FakeBus):
        def sync_read(self, name, normalize=True):
            if name == "Present_Voltage":
                return {j: (38 if j == "elbow_flex" and self.torque and self.pos_moved else 55) for j in JOINTS}
            return super().sync_read(name, normalize)
        pos_moved = False

        def sync_write(self, name, values):
            self.pos_moved = self.pos_moved or len(values) == 1     # a single-joint goal = the move itself has begun
            super().sync_write(name, values)

    with pytest.raises(nudge_mod.NudgeAbort, match="on elbow_flex"):
        nudge_mod.run_moves(OneLow(), [("wrist_roll", 5.0)], sleep=lambda _: None, log=lambda *_: None)


def test_main_refuses_without_a_terminal_or_port(monkeypatch):
    monkeypatch.setattr(nudge_mod, "find_ports", lambda: [])
    with pytest.raises(SystemExit, match="does not exist"):
        nudge_mod.main(["--port", "/dev/ttyACM0"])
    monkeypatch.setattr(nudge_mod, "find_ports", lambda: ["/dev/ttyACM0"])
    with pytest.raises(SystemExit, match="interactive"):
        nudge_mod.main(["--port", "/dev/ttyACM0"])  # pytest's stdin is not a terminal
    with pytest.raises(SystemExit, match="each move"):
        nudge_mod.main(["--port", "/dev/ttyACM0", "--degrees", "25"])


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


def test_torque_comes_on_one_motor_at_a_time_with_a_gap():
    bus = FakeBus()
    gaps = []
    nudge_mod.arm_hold(bus, sleep=gaps.append)
    assert sum(e[0] == "torque_on" for e in bus.events) == len(JOINTS)
    assert gaps == [nudge_mod.STAGGER_S] * len(JOINTS)


def test_a_brief_dip_is_tolerated_but_a_collapse_or_no_recovery_is_not():
    class Dipping(FakeBus):
        def __init__(self, trace):
            super().__init__()
            self.trace = list(trace)

        def sync_read(self, name, normalize=True):
            if name == "Present_Voltage":
                v = self.trace.pop(0) if len(self.trace) > 1 else self.trace[0]
                return {j: v for j in JOINTS}
            return super().sync_read(name, normalize)

    ok = Dipping([55, 41, 41, 43, 50, 54])           # dips to 4.1 V then recovers
    assert nudge_mod.settle(ok, sleep=lambda _: None, log=lambda *_: None) == pytest.approx(4.1)
    with pytest.raises(nudge_mod.NudgeAbort, match="collapsed"):
        nudge_mod.settle(Dipping([55, 37]), sleep=lambda _: None, log=lambda *_: None)
    with pytest.raises(nudge_mod.NudgeAbort, match="not recovered"):
        nudge_mod.settle(Dipping([55, 42]), sleep=lambda _: None, log=lambda *_: None)

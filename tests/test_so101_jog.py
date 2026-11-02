"""Jog logic with a fake controller: dead-man, speed, range limits and blocked-joint handling. No hardware."""
import importlib.util
import os
import struct
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("so101_jog", Path(__file__).parent.parent / "scripts" / "so101_jog.py")
jog_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jog_mod)

START = {"shoulder_pan": -30.0, "shoulder_lift": -100.0, "elbow_flex": 95.0, "wrist_flex": 70.0, "wrist_roll": 0.0, "gripper": 1.0}
LIMITS = {"shoulder_lift": (-104.0, 104.0), "elbow_flex": (-97.0, 97.0), "wrist_flex": (-100.0, 100.0), "shoulder_pan": (-73.0, 73.0)}


def pad(**state):
    p = jog_mod.Pad()
    p.buttons = {jog_mod.DEADMAN: state.pop("lb", True), jog_mod.FINE: state.pop("rb", False), jog_mod.HOME: state.pop("x", False)}
    p.axes = {n: state[k] for k, n in (("lx", 0), ("ly", 1), ("rx", 3), ("ry", 4), ("dy", 7)) if k in state}
    return p


def test_nothing_moves_without_the_deadman_or_inside_the_deadzone():
    j = jog_mod.Jog(START, LIMITS)
    assert j.tick(pad(lb=False, rx=1.0), dict(START)) == {}
    assert j.tick(pad(rx=0.1), dict(START)) == {}


def test_stick_right_is_positive_and_up_is_positive_with_the_axis_sign_flip():
    j = jog_mod.Jog(START, LIMITS)
    out = j.tick(pad(lx=1.0, ly=-1.0), dict(START))   # sticks report up as negative
    assert out["shoulder_pan"] > START["shoulder_pan"] and out["shoulder_lift"] > START["shoulder_lift"]


def test_full_stick_is_at_most_ten_degrees_a_second_and_fine_mode_a_third():
    j = jog_mod.Jog(START, LIMITS)
    step = j.tick(pad(rx=1.0), dict(START))["wrist_roll"] - 0.0
    assert step == pytest.approx(jog_mod.MAX_SPEED_DEG_S * jog_mod.TICK_S)
    j2 = jog_mod.Jog(START, LIMITS)
    fine = j2.tick(pad(rx=1.0, rb=True), dict(START))["wrist_roll"]
    assert fine == pytest.approx(step / jog_mod.FINE_DIVISOR)


def test_a_joint_at_the_end_of_its_range_can_only_go_back():
    j = jog_mod.Jog(START, LIMITS)                     # elbow starts at 95, band top is 97 - 2 = 95
    assert "elbow_flex" not in j.tick(pad(ry=-1.0), dict(START))      # up = +, further into the limit: refused
    assert j.tick(pad(ry=1.0), dict(START))["elbow_flex"] < START["elbow_flex"]


def test_wrist_roll_has_no_limit():
    j = jog_mod.Jog({**START, "wrist_roll": 170.0}, LIMITS)
    assert j.tick(pad(rx=1.0), {**START, "wrist_roll": 170.0})["wrist_roll"] > 170.0


def test_a_joint_that_stops_following_is_pulled_back_and_that_direction_is_ignored_until_release():
    j = jog_mod.Jog(START, LIMITS)
    present = dict(START)                               # the joint never moves: it is blocked
    blocked_at = None
    for i in range(40):
        out = j.tick(pad(rx=1.0), present)
        if j.note and blocked_at is None:
            blocked_at = i
            assert out["wrist_roll"] == present["wrist_roll"]      # goal pulled back to where it really is
    assert blocked_at is not None
    assert j.blocked["wrist_roll"] == 1
    assert j.goal["wrist_roll"] == present["wrist_roll"]           # not pushing any more
    assert "wrist_roll" not in j.tick(pad(rx=1.0), present)        # still holding the stick that way: ignored
    j.tick(pad(rx=0.0), present)                                    # release
    assert j.blocked["wrist_roll"] == 0
    assert j.tick(pad(rx=-1.0), present)["wrist_roll"] < present["wrist_roll"]   # the other way is allowed


def test_an_idle_joint_sagging_under_gravity_is_not_called_blocked():
    j = jog_mod.Jog(START, LIMITS)
    sagging = {**START, "shoulder_lift": START["shoulder_lift"] - 6.0}
    for _ in range(20):
        j.tick(pad(rx=1.0), sagging)                    # only the wrist roll is being driven
    assert j.blocked["shoulder_lift"] == 0 and j.goal["shoulder_lift"] == START["shoulder_lift"]


def test_home_ramps_every_goal_back_to_the_start():
    j = jog_mod.Jog(START, LIMITS)
    present = dict(START)
    for _ in range(10):
        present.update(j.tick(pad(rx=1.0, lx=1.0), present))
    assert j.goal["wrist_roll"] > 0
    for _ in range(200):
        present.update(j.tick(pad(x=True), present))
    assert j.goal["wrist_roll"] == pytest.approx(0.0) and j.goal["shoulder_pan"] == pytest.approx(START["shoulder_pan"])


def test_reading_events_and_the_range_reached():
    r, w = os.pipe()
    os.set_blocking(r, False)
    os.write(w, jog_mod.EVENT.pack(1, 32767, jog_mod.JS_AXIS, 3) + jog_mod.EVENT.pack(2, 1, jog_mod.JS_BUTTON | jog_mod.JS_INIT, 4))
    p = jog_mod.Pad()
    assert p.feed(jog_mod.read_events(r)) == ["button 4 down"] and p.axis(3) == pytest.approx(1.0) and p.held(4)
    assert jog_mod.read_events(r) == []
    os.close(w)                                         # the sender went away: that is a lost controller
    with pytest.raises(jog_mod.ControllerLost):
        jog_mod.read_events(r)
    os.close(r)
    with pytest.raises(jog_mod.ControllerLost):         # and so is a device that vanished under us
        jog_mod.read_events(r)


def test_main_refuses_without_a_controller_or_terminal(monkeypatch):
    with pytest.raises(SystemExit, match="not found"):
        jog_mod.main(["--device", "/nonexistent/js9", "--port", "/dev/ttyACM0"])
    monkeypatch.setattr(jog_mod, "find_ports", lambda: ["/dev/ttyACM0"])
    with pytest.raises(SystemExit, match="interactive"):
        jog_mod.main(["--device", "/dev/null", "--port", "/dev/ttyACM0"])

"""The real-arm hand-stop demo with a fake rig and fake hands: nothing here touches hardware, a camera or the network."""
import math

import pytest

from lookandpick.real_arm_demo import (DeckHandSource, DemoConfig, DemoRefused, HandReading, HandStopDemo, parse_state)
from lookandpick.world import Hand

START = {"shoulder_pan": -30.0, "shoulder_lift": -100.0, "elbow_flex": 95.0, "wrist_flex": 70.0, "wrist_roll": 0.0, "gripper": 1.0}
RANGES = {"shoulder_pan": (-70.0, 70.0), "shoulder_lift": (-104.0, 104.0), "elbow_flex": (-97.0, 97.0), "wrist_flex": (-100.0, 100.0)}


class FakeRig:
    """A servo model: each write moves the goal; the joint follows it by at most `follow` degrees a tick."""
    def __init__(self, start=START, follow=1.0):
        self.present, self.goals, self.follow = dict(start), dict(start), follow
        self.writes, self.released, self.volts_now, self.fail_release = [], False, {m: 5.4 for m in start}, []

    def positions(self):
        for j, g in self.goals.items():
            self.present[j] += max(-self.follow, min(self.follow, g - self.present[j]))
        return dict(self.present)

    def write_goals(self, goals):
        self.goals.update(goals)
        self.writes.append(dict(goals))

    def volts(self):
        return dict(self.volts_now)

    def release(self):
        self.released = True
        return list(self.fail_release)


class FakeHands:
    def __init__(self):
        self.reading = HandReading(True, ())

    def read(self):
        return self.reading


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def tip_of(joints):                                    # a planar arm 0.25 m long, pan about the origin: pan 1 deg ~ 4.4 mm
    a = math.radians(joints["shoulder_pan"])
    return (0.25 * math.cos(a), 0.25 * math.sin(a), 0.02)


def make(*, hands=None, rig=None, keys=None, supply=lambda v: None, inside=lambda j: True, ranges=RANGES, config=None):
    clock, rig, hands = Clock(), rig or FakeRig(), hands or FakeHands()
    queue = list(keys or [])
    demo = HandStopDemo(rig, hands, tip_of=tip_of, inside_workspace=inside, joint_ranges=ranges, supply_check=supply,
                        config=config or DemoConfig(), keys=lambda: queue.pop(0) if queue else None, clock=clock,
                        sleep=lambda s: None, log=lambda *a: None)
    demo.queue, demo.clock_obj = queue, clock
    return demo, rig, hands, clock


def advance(demo, clock, seconds):
    for _ in range(int(seconds / demo.cfg.tick_s)):
        clock.t += demo.cfg.tick_s
        demo.tick()


def near_hand(tip=None, distance=0.05):
    tip = tip or tip_of(START)
    return HandReading(True, (Hand("h1", (tip[0] + distance, tip[1], tip[2]), 0.0),))


# -- planning: refused before anything moves ----------------------------------------------------------------------------------
def test_sweep_is_about_four_centimetres_and_slow_by_default():
    demo, *_ = make()
    assert demo.amp_deg == pytest.approx(0.04 / math.radians(1) / 0.25, rel=0.02) or demo.amp_deg == demo.cfg.max_sweep_deg
    assert demo.cfg.speed_fraction * demo.cfg.max_deg_s <= 3.0 + 1e-9


def test_a_sweep_that_leaves_the_pan_range_or_the_workspace_is_refused_with_a_reason_and_writes_nothing():
    rig = FakeRig(dict(START, shoulder_pan=-66.0))
    with pytest.raises(DemoRefused, match="calibrated range"):
        make(rig=rig)
    assert rig.writes == []
    rig = FakeRig()
    with pytest.raises(DemoRefused, match="workspace"):
        make(rig=rig, inside=lambda j: j["shoulder_pan"] > -29)
    assert rig.writes == []


def test_a_missing_or_non_finite_reading_is_refused():
    rig = FakeRig()
    rig.present["elbow_flex"] = float("nan")
    with pytest.raises(DemoRefused):
        make(rig=rig)


def test_config_cannot_exceed_the_commissioned_speed_or_a_big_sweep():
    for bad in ({"amplitude_m": 0.2}, {"speed_fraction": 1.2}, {"fold_speed_fraction": 0.9}, {"max_deg_s": 20.0}, {"cycles": 0}):
        with pytest.raises(ValueError):
            DemoConfig(**bad)


# -- sweeping and the hand stop -----------------------------------------------------------------------------------------------
def test_with_no_hand_it_sweeps_pan_only_within_the_amplitude_and_never_faster_than_the_ceiling():
    demo, rig, hands, clock = make()
    advance(demo, clock, 2)
    assert demo.mode == "sweeping"
    pans = [w["shoulder_pan"] for w in rig.writes]
    assert all(set(w) == {"shoulder_pan"} for w in rig.writes)
    assert max(abs(b - a) for a, b in zip(pans, pans[1:])) <= demo.cfg.max_deg_s * demo.cfg.tick_s * demo.cfg.speed_fraction + 1e-9
    assert all(abs(p - START["shoulder_pan"]) <= demo.amp_deg + 1e-9 for p in pans)


def test_a_hand_at_the_stop_distance_freezes_the_arm_with_goals_equal_to_present_and_torque_kept():
    demo, rig, hands, clock = make()
    advance(demo, clock, 1)
    hands.reading = near_hand(tip_of(rig.present), 0.05)
    advance(demo, clock, 0.2)
    assert demo.mode == "frozen" and demo.frozen and not rig.released
    held = dict(rig.goals)
    assert {j: held[j] for j in rig.present} == pytest.approx(rig.present, abs=1.0)
    count = len(rig.writes)
    advance(demo, clock, 2)
    assert len(rig.writes) == count, "a freeze writes the held goals once; it does not keep nudging them"
    assert demo.status.scale == 0.0 and not rig.released


def test_a_hand_in_the_slow_band_slows_the_sweep_but_does_not_stop_it():
    demo, rig, hands, clock = make()
    tip = tip_of(START)
    hands.reading = HandReading(True, (Hand("h", (tip[0] + 0.30, tip[1], tip[2]), 0.0),))
    advance(demo, clock, 0.5)
    assert 0.0 < demo.status.scale < 1.0 and demo.mode == "sweeping"


def test_it_resumes_only_after_the_hand_has_been_clear_for_a_moment():
    demo, rig, hands, clock = make()
    advance(demo, clock, 1)
    hands.reading = near_hand(tip_of(rig.present), 0.05)
    advance(demo, clock, 0.3)
    hands.reading = HandReading(True, ())
    advance(demo, clock, demo.cfg.resume_clear_s - 0.3)
    assert demo.mode == "frozen"
    advance(demo, clock, 0.6)
    assert demo.mode == "sweeping"


def test_a_stale_or_failed_hand_source_freezes_and_never_folds_even_when_asked_to():
    demo, rig, hands, clock = make(keys=[])
    advance(demo, clock, 1)
    hands.reading = HandReading(False, why="no fresh answer from the Deck")
    demo.queue.append("f")
    advance(demo, clock, 12)
    assert demo.mode == "frozen" and not rig.released
    assert "cannot see" in demo.status.note and demo.status.sensing is False
    assert rig.goals["shoulder_lift"] == pytest.approx(rig.present["shoulder_lift"], abs=1.0)
    hands.reading = HandReading(True, ())                        # sight returns: the requested fold now goes ahead, after the clear wait
    advance(demo, clock, demo.cfg.fold_clear_s + 0.3)
    assert demo.mode in ("folding", "folded")


# -- folding --------------------------------------------------------------------------------------------------------------------
def test_the_demo_ends_by_folding_back_to_the_start_pose_and_only_then_releases_torque():
    demo, rig, hands, clock = make()
    advance(demo, clock, 6)
    assert not rig.released and demo.mode in ("sweeping", "folding")
    advance(demo, clock, 200)
    assert demo.mode == "released" and rig.released and demo.status.folded
    for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex"):
        assert abs(rig.present[joint] - START[joint]) <= 3.0       # standoff clamps at most the joint's last couple of degrees


def test_the_fold_target_stays_inside_each_end_stop_by_the_standoff():
    start = dict(START, shoulder_lift=-103.5, elbow_flex=96.5)
    demo, *_ = make(rig=FakeRig(start))
    assert demo.fold_target["shoulder_lift"] == pytest.approx(-102.0)
    assert demo.fold_target["elbow_flex"] == pytest.approx(95.0)


def test_a_fold_is_hand_checked_it_pauses_when_a_hand_comes_in_and_carries_on_when_clear():
    demo, rig, hands, clock = make()
    advance(demo, clock, 3)                                       # well away from the start pose
    assert abs(rig.present["shoulder_pan"] - START["shoulder_pan"]) > 3
    demo.queue.append("f")
    advance(demo, clock, 0.1)
    assert demo.mode == "folding"
    hands.reading = near_hand(tip_of(rig.present), 0.05)
    advance(demo, clock, 0.3)
    assert demo.mode == "frozen" and not rig.released
    hands.reading = HandReading(True, ())
    advance(demo, clock, demo.cfg.fold_clear_s + 0.5)
    assert demo.mode in ("folding", "folded", "released")


def test_a_hand_that_holds_it_for_a_long_time_makes_it_give_up_and_fold_once_the_hand_has_gone():
    demo, rig, hands, clock = make()
    advance(demo, clock, 0.5)
    hands.reading = near_hand(tip_of(rig.present), 0.05)
    advance(demo, clock, demo.cfg.give_up_s + 1)
    hands.reading = HandReading(True, ())
    advance(demo, clock, demo.cfg.fold_clear_s + 0.5)
    assert demo.fold_requested and demo.mode in ("folding", "folded", "released")


# -- operator and faults --------------------------------------------------------------------------------------------------------
def test_space_freezes_immediately_and_nothing_moves_until_the_operator_resumes():
    demo, rig, hands, clock = make()
    advance(demo, clock, 1)
    demo.queue.append(" ")
    advance(demo, clock, 0.1)
    assert demo.latched and demo.mode == "frozen" and demo.safety.stopped
    count = len(rig.writes)
    advance(demo, clock, 5)
    assert len(rig.writes) == count and not rig.released
    demo.queue.append("r")
    advance(demo, clock, demo.cfg.resume_clear_s + 0.5)
    assert demo.mode == "sweeping"


def test_x_releases_torque_and_a_release_that_cannot_be_confirmed_says_to_cut_the_supply():
    rig = FakeRig()
    rig.fail_release = ["elbow_flex"]
    lines = []
    demo, rig, hands, clock = make(rig=rig, keys=["x"])
    demo.log = lines.append
    advance(demo, clock, 0.1)
    assert rig.released and demo.mode == "released"
    assert any("CUT THE 5V SUPPLY" in l for l in lines)


def test_a_supply_collapse_releases_torque_as_the_last_resort_and_says_why():
    lines = []

    def collapse(readings):
        if min(readings.values()) < 4.0:
            raise RuntimeError("voltage collapsed to 3.1 V on wrist_roll")

    demo, rig, hands, clock = make(supply=collapse)
    demo.log = lines.append
    rig.volts_now = {m: 3.1 for m in START}
    demo.run(max_seconds=2.0)
    assert rig.released and demo.exit_code_ok is False
    assert any("voltage collapsed" in l and "CUT THE 5V SUPPLY" in l for l in lines)


def test_a_joint_that_will_not_follow_is_a_fault_that_holds_still_and_does_not_fold():
    rig = FakeRig(follow=0.0)                                     # a servo that never moves
    demo, rig, hands, clock = make(rig=rig)
    advance(demo, clock, 3)
    assert demo.mode == "fault" and demo.fault and not rig.released
    demo.queue.append("f")
    advance(demo, clock, 5)
    assert demo.mode == "fault" and not rig.released


def test_a_bus_error_releases_torque_and_reports():
    rig = FakeRig()
    demo, rig, hands, clock = make(rig=rig)
    rig.positions = lambda: (_ for _ in ()).throw(OSError("bus timeout"))
    demo.run(max_seconds=1.0)
    assert rig.released and "OSError" in demo.status.note


# -- the Deck hand source -------------------------------------------------------------------------------------------------------
def deck_state(**overrides):
    state = {"hand_gate": {"state": "clear", "scale": 1.0, "stale_s": 0.05, "positions": [{"id": "hand-1", "position": [0.1, 0.2, 0.05], "radius_m": 0.06}]},
             "scene": {"hands": []}, "stopped": False}
    state.update(overrides)
    return state


def test_parse_state_reads_hands_in_the_marker_frame():
    reading = parse_state(deck_state(), 0.25)
    assert reading.ok and reading.hands == (Hand("hand-1", (0.1, 0.2, 0.05), 0.06),)


@pytest.mark.parametrize("state", [
    {}, {"hand_gate": None, "scene": {"hands": []}},
    deck_state(hand_gate={"state": "stale", "stale_s": 0.1}), deck_state(hand_gate={"state": "latched", "stale_s": 0.1}),
    deck_state(hand_gate={"state": "clear", "stale_s": None}), deck_state(hand_gate={"state": "clear", "stale_s": 2.0}),
    deck_state(hand_gate={"state": "clear", "stale_s": float("nan")}), deck_state(stopped=True),
    deck_state(hand_gate={"state": "clear", "stale_s": 0.05, "positions": [{"id": "h", "position": [0, 0], "radius_m": 0.05}]}),
    deck_state(hand_gate={"state": "clear", "stale_s": 0.05, "positions": [{"id": "h", "position": [0, 0, float("inf")], "radius_m": 0.05}]}),
    deck_state(hand_gate={"state": "clear", "stale_s": 0.05, "positions": [{"id": "h", "position": [0, 0, 0], "radius_m": -1}]}),
    deck_state(hand_gate={"state": "clear", "stale_s": 0.05}),                         # a Deck that publishes no positions is blind, not clear
    deck_state(hand_gate={"state": "clear", "stale_s": 0.05, "positions": None}),
])
def test_anything_unexpected_in_the_deck_state_means_cannot_see(state):
    assert parse_state(state, 0.25).ok is False


def test_the_hand_source_fails_closed_when_the_deck_goes_silent_or_old_and_refuses_non_loopback_urls():
    clock, answers = Clock(), [deck_state()]

    def fetch():
        if not answers:
            raise OSError("connection refused")
        return answers[0]

    source = DeckHandSource(fetch=fetch, clock=clock)
    assert source.read().ok is False                              # nothing received yet
    source.poll_once()
    assert source.read().ok is True
    clock.t += 0.5
    assert source.read().ok is False                              # the answer is now too old
    answers.clear()
    source.poll_once()
    assert source.read().ok is False and "unreachable" in source.read().why
    for url in ("http://192.168.1.142:8772", "https://127.0.0.1:8765", "http://example.com"):
        with pytest.raises(ValueError):
            DeckHandSource(url)


# -- the real calibration profile, software only (no hardware) ----------------------------------------------------------------------
def test_rest_pose_on_the_real_profile_plans_sweeps_freezes_and_folds_on_a_fake_servo():
    from lookandpick.adapters.so101_profile import REST_READING
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("so101_hand_stop_demo", Path(__file__).parent.parent / "scripts" / "so101_hand_stop_demo.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    tip_of_real, inside, ranges = script.build_geometry()
    rig, hands, clock = FakeRig(REST_READING, follow=1.0), FakeHands(), Clock()
    demo = HandStopDemo(rig, hands, tip_of=tip_of_real, inside_workspace=inside, joint_ranges=ranges, supply_check=lambda v: None,
                        config=DemoConfig(), clock=clock, sleep=lambda s: None, log=lambda *a: None)
    assert "sweep shoulder_pan" in demo.plan_summary()
    start_tip = demo.start_tip
    advance(demo, clock, 3)
    assert math.dist(start_tip, tip_of_real(rig.present)) > 0.005          # it really sweeps
    x, y, z = tip_of_real(rig.present)
    hands.reading = HandReading(True, (Hand("h", (x + 0.05, y, z), 0.0),))
    advance(demo, clock, 0.3)
    assert demo.mode == "frozen" and not rig.released
    hands.reading = HandReading(True, ())
    advance(demo, clock, 300)
    assert rig.released and demo.status.folded
    assert max(abs(rig.present[j] - REST_READING[j]) for j in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")) <= 7.0


def test_the_script_refuses_a_missing_port_a_too_fast_speed_and_a_too_big_sweep(monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("so101_hand_stop_demo2", Path(__file__).parent.parent / "scripts" / "so101_hand_stop_demo.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script, "find_ports", lambda: [])
    with pytest.raises(SystemExit, match="does not exist"):
        script.main(["--port", "/dev/ttyACM9", "--check"])
    with pytest.raises(SystemExit, match="Refused"):
        script.main(["--port", "/dev/ttyACM0", "--speed", "1.5"])
    with pytest.raises(SystemExit, match="Refused"):
        script.main(["--port", "/dev/ttyACM0", "--amplitude-cm", "20"])


class FakeBus:
    """Just enough of a connected LeRobot bus for the script's flow: positions follow goals, voltages are steady."""
    def __init__(self):
        from lookandpick.adapters.so101_profile import REST_READING
        self.present, self.goals = dict(REST_READING), dict(REST_READING)
        self.motors = {m: None for m in REST_READING}
        self.enabled, self.disabled, self.goal_writes, self.connected = set(), [], [], False
        self.is_calibrated = True

    def connect(self):
        self.connected = True

    def disconnect(self, disable_torque=False):
        self.connected = False

    def sync_read(self, name, normalize=True):
        if name == "Present_Voltage":
            return {m: 54 for m in self.motors}
        for j, g in self.goals.items():
            self.present[j] += max(-1.0, min(1.0, g - self.present[j]))
        return dict(self.present)

    def sync_write(self, name, values):
        self.goals.update(values)
        self.goal_writes.append(dict(values))

    def disable_torque(self, motor=None):
        self.disabled.append(motor)

    def enable_torque(self, motor=None):
        self.enabled.add(motor)

    def write(self, *args):
        pass


def load_script(name):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent.parent / "scripts" / "so101_hand_stop_demo.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


class SeeingHands:
    max_age_s = 0.25

    def __init__(self, *a, **k):
        pass

    def start(self, *a, **k):
        return self

    def close(self):
        pass

    def read(self):
        return HandReading(True, ())


def patch_script(script, monkeypatch, bus):
    class Robot:
        calibration = {"x": 1}

        def __init__(self):
            self.bus = bus

    monkeypatch.setattr(script, "find_ports", lambda: ["/dev/ttyACM0"])
    monkeypatch.setattr(script, "require_present", lambda *a, **k: None)
    monkeypatch.setattr(script, "build_robot", lambda port, rid: Robot())
    monkeypatch.setattr(script, "DeckHandSource", SeeingHands)


def test_script_check_mode_never_turns_torque_on_or_writes_a_goal(monkeypatch, capsys):
    script, bus = load_script("demo_check"), FakeBus()
    patch_script(script, monkeypatch, bus)
    script.main(["--port", "/dev/ttyACM0", "--check"])
    out = capsys.readouterr().out
    assert "Check only" in out and "Plan:" in out
    assert not bus.enabled and not bus.goal_writes


def test_script_refuses_to_sweep_when_the_deck_cannot_see_and_enables_no_torque(monkeypatch):
    script, bus = load_script("demo_blind"), FakeBus()
    patch_script(script, monkeypatch, bus)
    monkeypatch.setattr(script, "DeckHandSource", type("Blind", (SeeingHands,), {"read": lambda self: HandReading(False, why="Deck unreachable")}))
    monkeypatch.setattr(script, "wait_for_sight", lambda hands, seconds=3.0: hands.read())
    with pytest.raises(SystemExit, match="never moves blind"):
        script.main(["--port", "/dev/ttyACM0"], ask=lambda prompt: "SWEEP")
    assert not bus.enabled and not bus.goal_writes


def test_script_wrong_confirmation_moves_nothing(monkeypatch):
    script, bus = load_script("demo_noconfirm"), FakeBus()
    patch_script(script, monkeypatch, bus)
    with pytest.raises(SystemExit, match="Not confirmed"):
        script.main(["--port", "/dev/ttyACM0"], ask=lambda prompt: "yes")
    assert not bus.enabled and not bus.goal_writes


def test_script_sweep_flow_folds_on_q_and_releases_every_motor_once(monkeypatch, capsys):
    script, bus = load_script("demo_flow"), FakeBus()
    patch_script(script, monkeypatch, bus)
    held = []
    monkeypatch.setattr(script, "arm_hold", lambda b, **k: held.append(dict(b.present)) or dict(b.present))
    monkeypatch.setattr(script, "settle", lambda b, **k: 5.3)
    pressed = iter(["q"])

    class OneKey:
        def __init__(self):
            pass

        def poll(self):
            return next(pressed, None)

        def close(self):
            pass

    monkeypatch.setattr(script, "Keys", OneKey)
    monkeypatch.setattr(script.time, "sleep", lambda s: None)
    script.main(["--port", "/dev/ttyACM0"], ask=lambda prompt: "SWEEP")
    out = capsys.readouterr().out
    assert held and "Plan:" in out
    assert "folded" in out.lower() or "releasing torque" in out
    assert len(bus.disabled) == len(bus.motors), "each motor released exactly once, not again in the script's finally"


def test_continuous_keeps_sweeping_past_the_finite_run_until_the_operator_folds():
    demo, rig, hands, clock = make(config=DemoConfig(continuous=True, cycles=1, speed_fraction=0.5))
    advance(demo, clock, 120)                                      # a finite 1-cycle run would have folded and released long ago
    assert demo.mode == "sweeping" and not rig.released and not demo.fold_requested
    pans = [w["shoulder_pan"] for w in rig.writes]
    assert max(pans) > START["shoulder_pan"] + demo.amp_deg - 1 and min(pans) < START["shoulder_pan"] - demo.amp_deg + 1
    demo.queue.append("f")
    advance(demo, clock, 60)
    assert rig.released and demo.status.folded


def test_a_hand_inside_the_work_area_freezes_the_arm_however_far_from_the_tip_and_it_resumes_when_it_leaves():
    area = (0.3, 0.9, -0.5, 0.5)                                   # tip is near x 0.2: the hand is far outside the slow band
    demo, rig, hands, clock = make(config=DemoConfig(work_area=area))
    advance(demo, clock, 1)
    hands.reading = HandReading(True, (Hand("h", (0.8, 0.4, 0.05), 0.05),))
    advance(demo, clock, 0.3)
    assert demo.mode == "frozen" and "work area" in demo.status.note and not rig.released
    hands.reading = HandReading(True, (Hand("h", (1.5, 0.4, 0.05), 0.05),))
    advance(demo, clock, demo.cfg.resume_clear_s + 0.5)
    assert demo.mode == "sweeping"


def test_a_bad_work_area_is_refused():
    for bad in ((0.9, 0.3, -0.5, 0.5), (0.3, 0.9, 0.5, -0.5), (0.3, 0.9, -0.5, float("nan")), (1, 2, 3)):
        with pytest.raises(ValueError):
            DemoConfig(work_area=bad)


def test_watch_mode_is_read_only_and_says_when_the_plan_would_be_accepted(monkeypatch):
    script, bus = load_script("demo_watch"), FakeBus()
    bus.present["wrist_flex"] = bus.goals["wrist_flex"] = -94.0               # Liam's folded wrist: tip above the ceiling, inside the minimum reach
    patch_script(script, monkeypatch, bus)
    seen = []
    from lookandpick.real_arm_demo import DemoConfig as Cfg
    script.watch_pose(script.BusRig(bus), SeeingHands(), Cfg(), sleep=lambda s: bus.present.update(wrist_flex=bus.present["wrist_flex"] + 40), out=seen.append, limit=3)
    assert seen[1].startswith("-94.0 not yet")
    assert seen[-1].endswith("OK: sweep plan accepted from here")
    assert not bus.enabled and not bus.goal_writes


def test_a_hand_in_scene_hands_alone_is_not_used_and_a_gate_with_positions_is():
    sim_only = deck_state(hand_gate={"state": "clear", "stale_s": 0.05}, scene={"hands": [{"id": "sim", "position": [0, 0, 0], "radius_m": 0.05}]})
    assert parse_state(sim_only, 0.25).ok is False
    assert parse_state(deck_state(), 0.25).hands[0].id == "hand-1"


def test_the_default_stop_distance_is_ten_centimetres_from_the_hands_surface_not_its_centre():
    demo, rig, hands, clock = make()
    tip = tip_of(START)
    hands.reading = HandReading(True, (Hand("h", (tip[0] + 0.30, tip[1], tip[2]), 0.18),))     # centre 30 cm, surface 12 cm: slows, does not stop
    advance(demo, clock, 0.3)
    assert demo.mode == "sweeping" and 0 < demo.status.scale < 1
    hands.reading = HandReading(True, (Hand("h", (tip[0] + 0.26, tip[1], tip[2]), 0.18),))     # surface 8 cm: inside 10 cm
    advance(demo, clock, 0.3)
    assert demo.mode == "frozen"
    with pytest.raises(ValueError):
        DemoConfig(stop_distance_m=0.01)

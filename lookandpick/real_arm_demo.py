"""Hand-stop demo for the REAL SO-101: a tiny slow sweep that freezes when a hand comes near, and folds back when it cannot go on.

Built for the bench (scripts/so101_hand_stop_demo.py): Liam present, the 5V kill switch in reach. This module powers nothing by
itself: it works through a `Rig` (positions, goals, supply voltage, torque release) that the script wires to the LeRobot bus, and
that the tests wire to a fake. Nothing here opens a serial port, a camera or a network connection other than loopback.

What it does, in plain words
  * Sweeps ONE joint (shoulder pan) a few centimetres either side of where the session started, slowly. No pick.
  * A hand near the tip path slows the sweep (speed-and-separation) and at the stop distance FREEZES the arm: every goal is set
    to the present pose and torque stays on, so the arm holds still. It is not limp and does not drop.
  * It resumes only after the hand has been clear for a moment. If the arm has been held for a long time, or the demo is over,
    or the operator asks, it folds back to the pose the session started in: slowly, hand-checked at every step, stopping a couple
    of degrees short of each end stop. Torque is released only once folded.
  * If it cannot SEE (the hand source is down, stale or fails), it freezes and does NOT fold: moving blind is exactly what the
    safety layer exists to prevent. The operator folds by key once sensing is back, or cuts the supply.
  * A supply collapse, a bus error or a joint that stops following its command is a fault. A supply collapse releases torque as a
    last resort and tells the operator to cut the 5V supply; a joint that stops following freezes the arm and waits for the operator.
  * SPACE is STOP: it freezes at once and nothing moves again until the operator says so. The 5V kill switch is the true emergency
    stop; this is software.

Where the hands come from: the live Deck owns both cameras (a second process cannot open them) and runs the hand gate in its own
environment, so this process reads the hands the Deck already publishes on loopback, and does its own speed-and-separation maths
against the REAL arm's tip path. The hand check therefore depends on loopback HTTP freshness from the Deck, and fails closed:
no answer, an old answer, a stopped or stale gate, or malformed data all read as "cannot see" -> freeze. It never uses the network
beyond 127.0.0.1.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .adapters.so101 import JOINTS
from .safety import Safety
from .world import Hand, Vec3

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
HEALTHY_GATE_STATES = ("clear", "slow", "stop")      # the Deck's gate says it is seeing: anything else (stale, latched, ...) is not sight
ARM_JOINTS = JOINTS[:5]                              # the gripper is never commanded here


class DemoRefused(RuntimeError):
    """The plan was refused before any torque came on (or before anything moved)."""


class Rig(Protocol):
    def positions(self) -> dict[str, float]: ...
    def write_goals(self, goals: dict[str, float]) -> None: ...
    def volts(self) -> dict[str, float]: ...
    def release(self) -> list[str]: ...              # torque off, motor by motor; returns the motors NOT confirmed released


@dataclass(frozen=True)
class HandReading:
    ok: bool                                         # False = cannot see: the caller must freeze
    hands: tuple[Hand, ...] = ()
    why: str = ""
    stale_s: float | None = None


class HandSource(Protocol):
    def read(self) -> HandReading: ...


def _loopback(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "http" or (parts.hostname or "") not in LOOPBACK_HOSTS:
        raise ValueError("the hand source must be http on 127.0.0.1 / localhost: safety checks never depend on the network")
    return url.rstrip("/")


def parse_state(state: dict, max_age_s: float) -> HandReading:
    """Turn a Deck `/api/state` document into hands, or a refusal to see. Anything unexpected is a refusal."""
    try:
        gate = state["hand_gate"]
        scene_hands = state["scene"]["hands"]
        if not isinstance(gate, dict) or gate.get("state") not in HEALTHY_GATE_STATES:
            return HandReading(False, why=f"hand gate is {gate.get('state') if isinstance(gate, dict) else 'missing'}")
        stale = gate.get("stale_s")
        if stale is None or not math.isfinite(float(stale)) or float(stale) > max_age_s:
            return HandReading(False, why=f"hand data is stale ({stale} s)", stale_s=None if stale is None else float(stale))
        if state.get("stopped"):
            return HandReading(False, why="the Deck is stopped")
        hands = tuple(Hand(id=str(h["id"]), position=tuple(float(c) for c in h["position"]), radius_m=float(h.get("radius_m", 0.0)))
                      for h in scene_hands)
        for h in hands:
            if len(h.position) != 3 or not all(math.isfinite(c) for c in h.position) or not math.isfinite(h.radius_m) or h.radius_m < 0:
                return HandReading(False, why="malformed hand data")
        return HandReading(True, hands, stale_s=float(stale))
    except (KeyError, TypeError, ValueError, AttributeError):
        return HandReading(False, why="malformed Deck state")


class DeckHandSource:
    """Polls the Deck on loopback in a thread; `read()` returns the latest hands, or a refusal if they are old or the Deck is silent."""

    def __init__(self, url: str = "http://127.0.0.1:8765", *, max_age_s: float = 0.25, timeout_s: float = 0.2,
                 fetch: Callable[[], dict] | None = None, clock=time.monotonic):
        self.url, self.max_age_s, self.timeout_s, self.clock = _loopback(url), max_age_s, timeout_s, clock
        self._fetch = fetch or self._http
        self._lock = threading.Lock()
        self._latest = HandReading(False, why="no answer from the Deck yet")
        self._received = float("-inf")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _http(self) -> dict:
        with urllib.request.urlopen(f"{self.url}/api/state", timeout=self.timeout_s) as response:    # noqa: S310 - loopback only, checked above
            return json.loads(response.read(2_000_000))

    def poll_once(self) -> None:
        try:
            reading = parse_state(self._fetch(), self.max_age_s)
        except Exception as exc:  # noqa: BLE001 - any failure at all is "cannot see"
            reading = HandReading(False, why=f"Deck unreachable ({type(exc).__name__})")
        with self._lock:
            self._latest, self._received = reading, self.clock()

    def start(self, period_s: float = 0.05) -> "DeckHandSource":
        def loop():
            while not self._stop.is_set():
                self.poll_once()
                self._stop.wait(period_s)
        self._thread = threading.Thread(target=loop, name="deck-hand-source", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()

    def read(self) -> HandReading:
        with self._lock:
            reading, received = self._latest, self._received
        if self.clock() - received > self.max_age_s:
            return HandReading(False, why="no fresh answer from the Deck")
        return reading


@dataclass
class DemoConfig:
    amplitude_m: float = 0.04            # tip travel each side of the start, along the pan arc (about 4 cm)
    cycles: int = 2                      # left-right-left passes before the demo ends and folds
    continuous: bool = False             # keep sweeping until the operator folds (q / f); `cycles` is then ignored
    work_area: tuple[float, float, float, float] | None = None   # marker-frame x0, x1, y0, y1 (m): any hand inside freezes the arm
    speed_fraction: float = 0.3          # of the commissioned 10 deg/s: 3 deg/s at scale 1
    fold_speed_fraction: float = 0.5
    tick_s: float = 0.05
    max_deg_s: float = 10.0              # the commissioned ceiling; never raised here
    max_sweep_deg: float = 12.0
    resume_clear_s: float = 1.0          # the way must be clear this long before a freeze lifts
    fold_clear_s: float = 2.0            # ...and this long before a fold starts (hand just left)
    give_up_s: float = 30.0              # held this long by a hand: stop trying, and fold once it has gone
    fold_standoff_deg: float = 2.0       # fold stops this far inside each calibrated end stop
    arrive_tol_deg: float = 1.0
    stall_deg: float = 2.0
    stall_reads: int = 4
    stall_progress_deg: float = 0.5
    volt_every: int = 4
    stop_distance_m: float = 0.15        # the real arm's distances (safety.py); the simulated arm's 0.10 is not used here
    slow_distance_m: float = 0.50

    def __post_init__(self):
        if not 0.005 <= self.amplitude_m <= 0.06:
            raise ValueError("sweep amplitude must be between 0.5 cm and 6 cm")
        if not 0 < self.speed_fraction <= 1.0 or not 0 < self.fold_speed_fraction <= 0.5:
            raise ValueError("sweep speed must be above 0 and at most 1.0, and fold speed at most 0.5, of the commissioned 10 deg/s")
        if self.work_area is not None:
            if len(self.work_area) != 4 or not all(math.isfinite(v) for v in self.work_area) \
                    or not self.work_area[0] < self.work_area[1] or not self.work_area[2] < self.work_area[3]:
                raise ValueError("work area must be x0 x1 y0 y1 in metres with x0 < x1 and y0 < y1")
        if not 1 <= self.cycles <= 10 or not 0.02 <= self.tick_s <= 0.1 or not 0 < self.max_deg_s <= 10:
            raise ValueError("cycles 1..10, tick 20..100 ms, speed ceiling at most 10 deg/s")


@dataclass
class Status:
    mode: str = "starting"               # sweeping | frozen | folding | folded | fault | released
    scale: float = 0.0
    nearest_m: float | None = None
    hands: int = 0
    sensing: bool = False
    note: str = ""
    tip: Vec3 | None = None
    lowest_volts: float | None = None
    stale_s: float | None = None
    folded: bool = False
    history: list = field(default_factory=list)


class HandStopDemo:
    """One session. `tick()` does one control step; `run()` loops it. Everything outside the arm is injected, so tests need no hardware."""

    def __init__(self, rig: Rig, hands: HandSource, *, tip_of: Callable[[dict[str, float]], Vec3],
                 inside_workspace: Callable[[dict[str, float]], bool], joint_ranges: dict[str, tuple[float, float]],
                 supply_check: Callable[[dict[str, float]], None], config: DemoConfig | None = None,
                 keys: Callable[[], str | None] = lambda: None, clock=time.monotonic, sleep=time.sleep, log=print):
        self.rig, self.hands, self.tip_of, self.inside_workspace = rig, hands, tip_of, inside_workspace
        self.ranges, self.supply_check, self.cfg, self.keys, self.clock, self.sleep, self.log = joint_ranges, supply_check, config or DemoConfig(), keys, clock, sleep, log
        self.safety = Safety(self.cfg.stop_distance_m, self.cfg.slow_distance_m)
        self.start = self._read_arm()                                    # where the session starts is where it folds back to
        self.goal = dict(self.start)
        self.fold_target = self._fold_target()
        self.start_tip = self.tip_of(self.start)
        self.amp_deg = self._amplitude_deg()
        self._legs = [self.amp_deg, -self.amp_deg] * self.cfg.cycles + [0.0]     # pan offsets from the start, visited in turn
        self._leg = 0
        self.mode = "sweeping"
        self.status = Status(folded=False)
        self.frozen = False                                              # goals currently pinned to a held pose
        self.latched = False                                             # SPACE: nothing moves until the operator resumes
        self.fault: str | None = None
        self.quit_after_fold = False
        self.fold_requested = False
        self.clear_since: float | None = None
        self.frozen_since: float | None = None
        self.ticks = 0
        self.released = False
        self.release_failed: list[str] = []
        self.exit_code_ok = True
        self._seen = {j: [] for j in ARM_JOINTS}
        self._lag = 0
        self._check_plan()

    # -- planning, all before anything moves ---------------------------------------------------------------------------------
    def _read_arm(self) -> dict[str, float]:
        positions = self.rig.positions()
        if set(positions) != set(JOINTS) or not all(math.isfinite(v) for v in positions.values()):
            raise DemoRefused("the arm did not report a finite position for every joint")
        return {k: float(v) for k, v in positions.items()}

    def _fold_target(self) -> dict[str, float]:
        """The start pose, pulled a couple of degrees inside any calibrated end stop so the fold never pushes on one."""
        target = dict(self.start)
        for name in ARM_JOINTS:
            if name in self.ranges:
                lo, hi = self.ranges[name]
                target[name] = min(hi - self.cfg.fold_standoff_deg, max(lo + self.cfg.fold_standoff_deg, target[name]))
        return target

    def _amplitude_deg(self) -> float:
        probe = dict(self.start)
        probe["shoulder_pan"] += 1.0
        metres_per_deg = math.dist(self.tip_of(self.start), self.tip_of(probe))
        if metres_per_deg < 1e-4:
            raise DemoRefused("shoulder pan barely moves the tip from this pose; cannot size the sweep")
        return min(self.cfg.max_sweep_deg, self.cfg.amplitude_m / metres_per_deg)

    def _check_plan(self) -> None:
        lo, hi = self.ranges.get("shoulder_pan", (-math.inf, math.inf))
        margin = self.cfg.fold_standoff_deg
        for off in (self.amp_deg, -self.amp_deg):
            pan = self.start["shoulder_pan"] + off
            if not lo + margin <= pan <= hi - margin:
                raise DemoRefused(f"the sweep needs shoulder_pan {pan:.1f} deg, outside its calibrated range {lo:.1f} to {hi:.1f} "
                                  f"(less {margin:g} deg). Move the arm to a pose nearer the middle of the pan range first.")
            there = dict(self.start, shoulder_pan=pan)
            if not self.inside_workspace(there):
                raise DemoRefused(f"the sweep end at shoulder_pan {pan:.1f} deg leaves the commissioned workspace box. "
                                  "Place the arm further from the base or nearer the table centre first.")

    def plan_summary(self) -> str:
        a, b = self.tip_of(dict(self.start, shoulder_pan=self.start["shoulder_pan"] + self.amp_deg)), self.tip_of(dict(self.start, shoulder_pan=self.start["shoulder_pan"] - self.amp_deg))
        return (f"sweep shoulder_pan {self.start['shoulder_pan']:.1f} deg +/- {self.amp_deg:.1f} deg ({self.cfg.cycles} cycle(s)), "
                f"tip about {math.dist(a, b) * 100:.1f} cm end to end, at most {self.cfg.max_deg_s * self.cfg.speed_fraction:.1f} deg/s; "
                f"fold target = the pose it started in, {self.cfg.fold_standoff_deg:g} deg inside any end stop")

    # -- one control step ----------------------------------------------------------------------------------------------------
    def _freeze(self, present: dict[str, float]) -> None:
        """Hold still, torque on: every goal := where the arm is now, once. Later ticks leave the goals alone (re-writing the present
        pose each tick would let the arm sag a little more every time)."""
        if self.frozen:
            return
        self.goal = {j: present[j] for j in ARM_JOINTS} | {"gripper": present["gripper"]}
        self.rig.write_goals(dict(self.goal))
        self.frozen = True
        self.frozen_since = self.clock()

    def _handle_keys(self) -> None:
        while (key := self.keys()):
            key = key.lower()
            if key == " ":
                self.latched = True
                self.safety.stop("person", "operator pressed SPACE")
                self.mode = "frozen"
                self.status.note = "STOP: held still. r = resume, f = fold, x = release torque"
            elif key == "r" and not self.fault:
                self.latched = False
                self.safety.reset()
                self.status.note = "resume asked: carrying on once the way has been clear a moment"
            elif key in ("f", "q"):
                self.fold_requested = True
                self.quit_after_fold = self.quit_after_fold or key == "q"
                self.latched = False
                self.safety.reset()
                self.status.note = "fold requested"
            elif key == "x":
                self._release("operator asked for torque release: the arm goes LIMP, support it")

    def _release(self, why: str) -> None:
        self.log(why)
        failed = self.rig.release()
        self.release_failed = list(failed)
        self.released = True
        self.mode = "released"
        self.status.mode = "released"
        if failed:
            self.log(f"!!! torque NOT confirmed released on {', '.join(failed)}: CUT THE 5V SUPPLY NOW.")

    def _scale(self, hands: tuple[Hand, ...], tip: Vec3, goal_tip: Vec3) -> tuple[float, float | None]:
        scale = self.safety.allowed_speed(list(hands), (tip, goal_tip))
        nearest = min((max(0.0, math.dist(h.position, tip) - h.radius_m) for h in hands), default=None)
        return scale, nearest

    def _hand_in_work_area(self, hands: tuple[Hand, ...]) -> bool:
        if self.cfg.work_area is None:
            return False
        x0, x1, y0, y1 = self.cfg.work_area
        return any(x0 - h.radius_m <= h.position[0] <= x1 + h.radius_m and y0 - h.radius_m <= h.position[1] <= y1 + h.radius_m for h in hands)

    def _stall_check(self, present: dict[str, float], driven: bool) -> None:
        for name in ARM_JOINTS:
            history = self._seen[name]
            history.append(present[name])
            del history[:-(self.cfg.stall_reads + 1)]
        if not driven:
            self._lag = 0
            return
        stuck = any(abs(self.goal[n] - present[n]) > self.cfg.stall_deg
                    and len(self._seen[n]) > self.cfg.stall_reads and abs(self._seen[n][-1] - self._seen[n][0]) < self.cfg.stall_progress_deg
                    for n in ARM_JOINTS)
        self._lag = self._lag + 1 if stuck else 0
        if self._lag >= self.cfg.stall_reads:
            self._fail("a joint is not following its command (something is in the way, or a servo is overloaded)")

    def _fail(self, reason: str) -> None:
        self.fault = reason
        self.mode = "fault"
        self.status.note = f"FAULT: {reason}. Held still; x = release torque, or cut the 5V supply."
        self.log(self.status.note)

    def tick(self) -> None:
        """One control step. Raises nothing the caller must handle except SupplyCollapse from the supply check (see run())."""
        now = self.clock()
        self.ticks += 1
        present = self._read_arm()
        self._handle_keys()
        if self.released:
            return
        if self.ticks % self.cfg.volt_every == 0:
            readings = self.rig.volts()
            self.status.lowest_volts = min(readings.values()) if readings else None
            self.supply_check(readings)
        tip = self.tip_of(present)
        reading = self.hands.read()
        self.status.tip, self.status.sensing, self.status.hands, self.status.stale_s = tip, reading.ok, len(reading.hands), reading.stale_s
        if not reading.ok and not self.status.note.startswith("FAULT"):
            self.status.note = f"cannot see ({reading.why}): held still, not folding"

        if self.mode == "folded":
            return
        if self.fault:
            self._freeze(present)
            self.status.mode, self.status.scale = "fault", 0.0
            return
        if self.latched or not reading.ok:
            self._freeze(present)
            self.mode = "frozen"                     # a lifted freeze always goes back through the clear-for-a-moment check below
            self.status.mode, self.status.scale = "frozen", 0.0
            self.clear_since = None
            return

        folding = self.mode == "folding" or self.fold_requested
        goal_tip = self.start_tip if folding else self.tip_of(dict(present, shoulder_pan=self.start["shoulder_pan"] + self._legs[self._leg]))
        scale, nearest = self._scale(reading.hands, tip, goal_tip)
        in_area = self._hand_in_work_area(reading.hands)
        if in_area:
            scale = 0.0
        self.status.scale, self.status.nearest_m = scale, nearest
        if scale <= 0.0:
            self._freeze(present)
            self.clear_since = None
            self.status.mode = "frozen"
            self.status.note = "hand in the work area: held still" if in_area else "hand near the path: held still"
            if self.mode == "folding":
                self.mode = "frozen"
                self.fold_requested = True
            elif self.mode == "sweeping":
                self.mode = "frozen"
            return

        # the way is open for this tick
        self.clear_since = now if self.clear_since is None else self.clear_since
        clear_for = now - self.clear_since
        if self.mode == "frozen":
            held_for = now - (self.frozen_since or now)
            if self.fold_requested and clear_for >= self.cfg.fold_clear_s:
                self.mode = "folding"
            elif not self.fold_requested and held_for >= self.cfg.give_up_s:
                if clear_for >= self.cfg.fold_clear_s:
                    self.log("held for a long time: giving up and folding instead")
                    self.fold_requested = True
                    self.mode = "folding"
                else:
                    self.status.mode = "frozen"
                    return
            elif not self.fold_requested and clear_for >= self.cfg.resume_clear_s:
                self.mode, self.frozen = "sweeping", False
                self.goal = {j: present[j] for j in JOINTS}
                self.status.note = "clear: carrying on"
            else:
                self.status.mode = "frozen"
                return
        if self.mode == "sweeping" and self.fold_requested:
            self.mode = "folding"
        if self.mode == "folding":
            self.frozen = False
            self._fold_step(present, scale)
        elif self.mode == "sweeping":
            self._sweep_step(present, scale)
        self.status.mode = self.mode

    def _sweep_step(self, present: dict[str, float], scale: float) -> None:
        step = self.cfg.max_deg_s * self.cfg.tick_s * self.cfg.speed_fraction * scale
        target = self.start["shoulder_pan"] + self._legs[self._leg]
        pan = self.goal["shoulder_pan"]
        pan += max(-step, min(step, target - pan))
        self.goal["shoulder_pan"] = pan
        self.rig.write_goals({"shoulder_pan": pan})
        self._stall_check(present, driven=True)
        if abs(pan - target) < 1e-9 and abs(present["shoulder_pan"] - target) < self.cfg.arrive_tol_deg:
            self._leg += 1
            if self.cfg.continuous and self._leg >= len(self._legs) - 1:      # keep swinging; the trailing "back to centre" leg is for finite runs
                self._leg = 0
            if self._leg >= len(self._legs):
                self.log("demo finished: folding back")
                self.fold_requested = True
                self.mode = "folding"
                self.status.note = "demo finished: folding back"

    def _fold_step(self, present: dict[str, float], scale: float) -> None:
        step = self.cfg.max_deg_s * self.cfg.tick_s * self.cfg.fold_speed_fraction * scale
        deltas = {n: self.fold_target[n] - self.goal[n] for n in ARM_JOINTS}
        biggest = max(abs(d) for d in deltas.values())
        if biggest > 1e-9:
            fraction = min(1.0, step / biggest)
            self.goal.update({n: self.goal[n] + deltas[n] * fraction for n in ARM_JOINTS})
            self.rig.write_goals({n: self.goal[n] for n in ARM_JOINTS})
        self._stall_check(present, driven=biggest > 1e-9)
        arrived = biggest <= 1e-9 and all(abs(present[n] - self.fold_target[n]) <= self.cfg.arrive_tol_deg for n in ARM_JOINTS)
        if arrived and self.mode != "fault":
            self.mode = "folded"
            self.status.mode, self.status.folded = "folded", True
            self.status.note = "folded: releasing torque"
            self._release("folded to the session start pose: releasing torque (the arm settles onto its rest)")

    # -- the loop ------------------------------------------------------------------------------------------------------------
    def run(self, max_seconds: float | None = None) -> Status:
        """Loops until folded and released, released by the operator, or `max_seconds` of wall time. A supply collapse or any other
        unexpected error releases torque as the last resort. Ctrl-C is a SPACE; a second within three seconds releases."""
        began, last_interrupt = self.clock(), None
        try:
            while not self.released and (max_seconds is None or self.clock() - began < max_seconds):
                try:
                    self.tick()
                    self.sleep(self.cfg.tick_s)
                except KeyboardInterrupt:
                    now = self.clock()
                    if last_interrupt is not None and now - last_interrupt < 3.0:
                        self._release("second Ctrl-C: releasing torque, the arm goes LIMP: support it")
                    else:
                        self.latched, self.mode = True, "frozen"
                        self.log("Ctrl-C: held still. Press Ctrl-C again within 3 s to release torque, or use r / f / x.")
                    last_interrupt = now
        except BaseException as exc:  # noqa: BLE001 - supply collapse, bus error, anything: last resort is to let go and say so
            self.exit_code_ok = False
            self.status.note = f"{type(exc).__name__}: {exc}"
            self.mode = "fault"
            self.status.mode = "fault"
            self._release(f"STOPPED by {type(exc).__name__}: {exc}. Torque released as the last resort: the arm goes LIMP, support it. "
                          "If the supply collapsed or a motor will not respond, CUT THE 5V SUPPLY.")
        return self.status

    def status_json(self) -> dict:
        """The shape Deck's hand-gate panel reads (state, scale, nearest_m, hands, ...), for lookandpick.status_server."""
        s = self.status
        state = {"sweeping": "clear" if s.scale >= 1 else "slow", "frozen": "stop", "folding": "slow", "folded": "clear",
                 "fault": "latched", "released": "latched"}.get(s.mode, "stale")
        if not s.sensing:
            state = "stale"
        return {"state": state, "scale": round(s.scale, 3), "nearest_m": None if s.nearest_m is None else round(s.nearest_m, 3),
                "hands": s.hands, "fps": 0.0, "latency_ms": {"p50": None, "p95": None}, "stale_s": s.stale_s,
                "reason": f"real arm: {s.mode}. {s.note}", "updated": time.time()}

    def status_line(self) -> str:
        s = self.status
        tip = "tip ?" if s.tip is None else "tip %.3f %.3f %.3f" % s.tip
        near = "no hand" if s.nearest_m is None else f"hand {s.nearest_m * 100:.0f} cm"
        volts = "V ?" if s.lowest_volts is None else f"V {s.lowest_volts:.1f}"
        return f"[{s.mode:8s}] scale {s.scale:.2f}  {near}  {'seeing' if s.sensing else 'BLIND '}  {tip}  {volts}  {s.note}"

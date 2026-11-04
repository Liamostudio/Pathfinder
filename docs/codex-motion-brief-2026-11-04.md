# Codex brief: motion tasks (4 Nov 2026, hard stop 18:00 UK)

Full task list: `docs/motion-tasks-2026-11-04.md`. Claude is doing the logic (Tasks 1 to 5 in `tools.py`, `safety.py`, `runtime.py`, `hand_*.py`, `models/orchestrator.py`). These three pieces are yours and touch none of those files. Pull (`git pull --rebase --autostash`) before every commit, commit your own files only, add a PROGRESS entry saying what was tested, tick no milestone.

**Do not edit:** `lookandpick/tools.py`, `safety.py`, `runtime.py`, `hand_safety.py`, `hand_gate.py`, `zones.py`, `models/*`, `adapters/*`. The Spark Open agent is in `models/meta.py`, `glasses.py` and the `--model` flags. Ask Claude for anything you need from those.

**Wait for Claude's Task 1 commit first** (the simulator `cancel()`, paused time not counting towards a move's timeout, the 10 cm sim stop distance in `engine.py`, `client.py`, `server.py`). Claude will say when it is in.

## A. Route planner: new `lookandpick/routing.py` + `tests/test_routing.py` (Task 4 core, reused by Task 5). Start now, no dependency.

```python
plan_route(start, end, violation, reachable, hands=(), hand_clearance_m=0.10, tool_radius_m=0.04, margin_m=0.02, ceiling_m=0.35)
    -> list[Vec3] | None     # waypoints after `start`, ending at `end`; [end] if the straight line is fine; None if boxed in
```
- `violation(a, b) -> str | None` is `ManualZoneGuard.violation` (a reason string means blocked). `reachable(point) -> bool` is the arm's reach check (Claude passes the simulator's IK test). `hands` are `world.Hand` (position, radius_m).
- Candidates: (a) **up and over**: rise above `start` to the lowest height that clears the blocked cubes plus tool radius and margin, cross, descend; (b) **sideways**: one or two waypoints left or right of the blocked region. Return the shortest route where EVERY segment passes `violation`, every waypoint passes `reachable`, and every segment is at least `hand_clearance_m` (+ the hand's `radius_m`) from every hand (segment-to-point distance, `safety.point_segment_distance`).
- Pure geometry, no I/O, no arm calls. Tests with REAL grids from `tests/test_zones.py`: a wall of cubes between pick and drop means over; a tall but narrow wall means around; boxed in means `None`; a hand parked to one side means a route on the other; a hand in a narrow workspace means `None`; every returned segment is re-checked in the test.

## B. Simulator support, in `simulation/sim_env/*` and `simulation/tests/*`

1. `Simulation.rest()`: a joint-space move to the Rest pose. Map `REST_READING` (`lookandpick/adapters/so101_profile.py`) to the simulator's joints. Stop a few degrees short of the end stops. It moves at the speed the hand check allows (use the existing `_move_joints`, so it pauses and `cancel()` works), and refuses while stopped. Add the server action `rest` (a job like `move`), `SimulationClient.rest()` and a test in `simulation/tests`.
2. Test hooks for Task 3, as server actions that work while idle or mid-move: `nudge` (`name`, `dx`, `dy` metres: move a free object, and one held in the gripper is not moved), a fault `grip_miss_once` (the next close reports no object, then clears itself), and `hide` (`name`, `hidden`: the object is absent from `objects()` and the camera segmentation while hidden). Document them in `simulation/README.md`.
Claude adds the matching `SimArm` fake methods in `adapters/fake.py`.

## C. Deck UI (`lookandpick/web/*`, plus the state fields in `deck_runtime.py`)

Build against a fake feed first; Claude fills the real values. Contract (extra keys in the `/api/state` JSON, absent when nothing applies):
```
stop:  {kind: "person" | "fault", reason: str, recovering_in_s: float | null, resets_left: int}
route: {kind: "over" | "around" | "hand", waypoints: [[x, y, z], ...]}      # marker-frame metres
```
- Show the stop **kind** and reason, and a countdown while it waits to recover on its own (`kind: "fault"` only). A person's stop shows "STOP (you)" and never counts down.
- Buttons: **Cancel** (sends the text `cancel` as a command; it is handled locally and never queued behind the running command) and **Carry on** (sends `carry on`; Claude is adding the handling). The existing reset button is unchanged.
- New timeline lines come through as `source: "Arm"` or `"Safety"` with short text ("Pausing: a hand is in the way", "Going around", "Clear, carrying on"); make sure they show and are not filtered.
- If cheap: draw `route.waypoints` as a polyline in the 3D view. If not, the timeline is enough.

## Reminders
Simulation only. Nothing here may power or move the real arm. Stop distance is 10 cm for the simulated arm only; the real-arm profile keeps 15 cm.

# Brief: 3D simulation environment for Pathfinder (Look-and-Pick)

Read first: `CLAUDE.md` (safety rules are non-negotiable), `PROGRESS.md`, `lookandpick/adapters/base.py`,
`world.py`, `safety.py`, `tools.py`, `calibration.py`, `intrinsics.py`, `adapters/camera.py`, `record.py`.
New code goes under `lookandpick/sim/` with tests in `tests/sim/`; heavy dependencies go in a new optional
`sim` extra, never core. Do not edit `calibration.py`, `intrinsics.py` or `scripts/calibrate_lens.py`
(Claude owns them). Update `PROGRESS.md` honestly in the same commit; tick nothing without a passed test.

## 1. Purpose

A test bench that stands in for the arm, cameras, hands and objects behind the **existing adapter
interfaces**, so every other part of the system runs closed-loop with no hardware: the same `Tools`,
`Safety`, calibration and (later) detector/orchestrator code, unchanged. It also produces footage and a
replayable trace for the demo video and the hosted "live product" build.

What it must **not** do: it cannot validate detector accuracy (hands and objects in renders are not real
footage), and it never drives real hardware. Anything about real hand/person detection quality is tested
on recorded real clips, not here.

## 2. Engine and constraints

- Recommended: **MuJoCo** with the official SO-101 model (TheRobotStudio/SO-ARM100 ships URDF/MJCF). Verify
  mujoco installs on Python 3.14 first; if not, use its own venv outside the repo and talk to it over a
  small local interface. Isaac Sim/Isaac Lab are out: too heavy for the 8 GB card.
- The RTX 5070 has **8 GB VRAM shared** with SAM 3.1 (~4.5 GB) and later cuRobo. Default to CPU/EGL
  rendering at 640x360, 1280x720 optional. Must run headless (no display) and in a normal window.
- Deterministic: seeded scenarios, simulated clock separate from wall clock (real-time and
  faster-than-real-time modes), identical results for identical seeds.

## 3. What it models

**Arm.** SO-101: 5 joints + gripper, URDF joint limits, STS3215 behaviour (4096 ticks/rev quantisation,
velocity cap, P-controlled position tracking with realistic lag, torque limit, small command latency).
Implements `SpeedControlledArm`: `move_to(Vec3, speed_scale)` blocks until done and returns True, or
False if refused/interrupted; `set_speed_scale(0)` pauses mid-motion and resumption continues; `halt()`
latches independently. Needs IK from a Cartesian target (the Arm interface takes `Vec3`), reachability
and self/table collision checks (unreachable => False, never a crash). Expose joint angles too.

**Frames.** `calibration.py` uses the marker centre as origin (x/y across the table, z up); `world.py`
says "arm-base frame". Make this explicit: world frame = marker centre, with the arm base pose as a
measured config constant (`arm_base_offset`, default: marker beside the base on the table). Flag it in
the code and docs as something Liam must measure on the real build.

**Table and objects.** Table plane at z=0, ArUco marker (DICT_4X4_50 id 0, 120 mm) as a real texture, so
the calibration pipeline runs end to end. Rigid-body props with mass, friction and ground-truth id, label
and pose: mug, apple, bottle, block, a "light prop" for the first real pick. Gripper contact must be
physical enough that a clean pick succeeds and a bad approach fails. Randomisable placement.

**Cameras.** At least two virtual cameras about 90 degrees apart looking at the table, plus the option of
a third overhead one. Match real intrinsics: load `calibration/<name>.json` (`Intrinsics`) and apply the
lens distortion by post-processing (MuJoCo renders pinhole; use `cv2.remap`). Defaults: 1280x720, 30 Hz.
Optional exposure change, noise, motion blur, frame drop and latency. Each camera is a `FrameSource`
(`frames()` yields `(timestamp_s, BGR frame)`), recordable with `record.py` (MP4 plus timestamp sidecar),
so sim output and real recordings are interchangeable downstream.

**Hands and people.** Two modes, both needed:
1. *Ground-truth injection*: scripted 3D hand trajectories (reach into the path, hover, wave, withdraw,
   fast lunge, two hands, hand hidden behind the arm), projected through the camera models into
   `PixelHand` landmarks and fed to the safety loop. This tests logic and latency without vision.
2. *Rendered*: simple articulated hand/forearm meshes in the camera images, for pipeline plumbing only.

## 4. Fault injection (this is where the safety tests live)

Camera freeze, black frame, dropped or delayed frames; detector stall, crash or ghost hands (false
positives); hand lost for N frames mid-approach; command refused; arm stall; Stop pressed mid-move;
serial dropout; network entirely unavailable (safety must not depend on it). Every fault is a scenario
parameter with a seed, not a hack.

## 5. Scenario runner and metrics

Declarative scenarios (Python or YAML): setup, scripted events on the sim clock, assertions. Batch
runner with seeds, `N/N` repeat counts (acceptance: "hand stops the arm 10/10 times"), a JSON report, and
a short table fit for `PROGRESS.md`. Record per run: minimum hand-to-arm separation, slow-down onset
distance, time from hand entering the path to speed 0 (ms), Stop latency, pick success, end state.
Baseline scenarios: clean pick-and-place; hand crosses the path; hand approaches slowly then withdraws;
Stop mid-move; detector dies mid-motion; two hands; object occluded; unreachable target.

## 6. Interfaces and contract tests

Implement against what exists: `Tracker` (`objects()`, `hands()`), `Arm`/`SpeedControlledArm`,
`HandDetector`, `FrameSource`, feeding `WorldState` via its existing `update`. Add **contract tests** for
each protocol that run against the fake adapters and the sim today and the real SO-101/camera adapters
later, so swapping hardware in changes nothing the tests don't cover.

Deck (`lookandpick/mission_control.py`, `web/`): stream sim state in the existing `snapshot()` schema,
plus joint angles, so the schematic scene can be backed by the sim. A **replay export** (trajectory plus
events as JSON, playable in a browser with no Python) is wanted for the hosted submission build. Optional:
a 3D view using the SO-101 URDF in the browser.

## 7. Priority order (matches the project's cut lines)

1. **Must:** headless MuJoCo arm + table + objects behind `SpeedControlledArm`/`Tracker`, with contract
   tests and a clean scripted pick-and-place through the real `Tools`/`Safety`.
2. **Must:** virtual cameras with distortion and the marker texture, plus a test that the real calibration
   code recovers camera poses and table positions from sim renders to within 1 cm of sim ground truth
   (a stricter test than the current synthetic homographies).
3. **Must:** ground-truth hand injection, fault injection and the scenario runner with the metrics above.
4. **Should:** Deck integration, replay export, recorded sim footage through `record.py`.
5. **Should:** rendered hands, optional SAM/hand-detector in the loop on sim frames (plumbing only).
6. **Stretch:** export the sim world (obstacles) to cuRobo for planning, which also needs a CUDA toolchain
   and is risky; do not block on it. Fallback is IK plus simple joint-space planning.

## 8. Rules

No real hardware, ever. Nothing in `sim/` may let a remote or unattended session reach a real arm. Report
what you could not verify (for example rendering speed on the Legion, or Python 3.14 wheel availability).

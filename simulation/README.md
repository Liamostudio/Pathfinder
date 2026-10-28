# Pathfinder simulation environment

A local 3D test bench for Look-and-Pick, built on MuJoCo 3.17 and the upstream SO-101 follower URDF. Printed parts use Pathfinder lime **#D5FD51**; servos remain dark. No hardware adapter, serial port, model endpoint or cloud service is used.

## Run

The isolated environment is already installed in this folder. From the repository root:

```sh
simulation/.venv/bin/python simulation/start.py
```

The launcher detaches the server from the invoking terminal/chat, waits for a successful health check and writes `server.log`/`server.pid` in this folder. Running it again reuses an already-running simulator. For foreground use or custom flags, run `run.py` instead.

Open http://127.0.0.1:8766. Drag the main view to orbit and scroll to zoom. Choose Camera A, Camera B, overhead or TCP inspection; the angle/distance controls move the selected camera. Camera A and B start 90° apart. Pick/place uses the project's existing `Tools` and `Safety`. **Stop** or Space interrupts motion; **Reset scene** clears its latch once the active command has returned.

For a fresh installation:

```sh
python3 -m venv simulation/.venv
simulation/.venv/bin/python -m pip install -r simulation/requirements.txt
```

Python 3.14.4 and the MuJoCo CPython 3.14 wheel were verified here. Core project dependencies are unchanged. Linux defaults to EGL (no display required); the browser supplies the windowed workbench. Set `MUJOCO_GL=glfw` for a desktop OpenGL context where EGL is unavailable. Use `--no-render` for physics/controller tests without OpenGL.

Default capture is **640×360, target 10 Hz**, to leave resources for SAM. Use `--resolution 1280x720 --fps 30` for higher resolution/rate; the requested rate is a cap, not a measured throughput guarantee. EGL can use the GPU; this implementation does not promise CPU-only rendering or a VRAM budget.

## Plug into the controller

The bridge client uses only the Python standard library until camera arrays are requested. Add this folder to `sys.path` in your launcher:

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'simulation'))  # adjust to where this file sits relative to the repo

from sim_env.client import SimulationClient, SimulationCamera
from lookandpick.tools import Tools
from lookandpick.world import WorldState
from lookandpick.safety import Safety

sim = SimulationClient()  # start run.py first
controller = Tools(WorldState(), sim, sim, Safety())
print(controller.get_scene())
print(controller.pick('obj1'))
print(controller.place('obj1', (0.18, -0.20, 0.018)))
controller.stop()
```

`SimulationClient` implements `Arm`, `SpeedControlledArm` and `Tracker`. Moves block and return `True` only after completion; refusal, timeout or interruption returns `False` (connection errors raise). Polling failure requests a stop before propagating the error. A timeout also requests a stop. The **local simulation's own separation checks run independently of the bridge**.

For in-process use, `sim_env.engine.Simulation` implements the same interfaces and exposes `sim.tools`. `Simulation(realtime=False)` advances at maximum speed while commands run; call `step()` explicitly when idle. `snapshot()` returns the existing WorldState scene schema. `state()` adds joints, limits, simulation clock, events and control status. Separate processes do not share memory: use the bridge rather than constructing two simulations.

The current Deck server is unchanged and still uses its original fake adapters. This workbench is a separate UI; the client and matching scene schema are the integration points for a later Deck backend change.

## Coordinate frame

World origin is the **ArUco marker centre**, metres, x/y across the table, z up. The table is z=0. `ARM_BASE_OFFSET = (0, -0.12, 0)` in `sim_env/model.py` places the robot beside the marker. **Liam must measure this transform on the real build.** All Cartesian commands, object positions, hands, exported camera poses and replay data use this marker frame. Joint targets use radians in the upstream new-calibration convention, not LeRobot normalised motor values.

The 120 mm DICT_4X4_50/id 0 marker is a real scene texture, with a 15 mm white border. `obj1` and `obj2` are 36 mm rigid cubes with mass, friction and gravity. Positions can be deterministically varied with `sim.randomise(seed)`.

## Camera/vision adapter

```python
camera = SimulationCamera(sim, name='camera_a', fps=10)
frames = camera.frames()
stamp, bgr = next(frames)  # existing FrameSource contract
frames.close()
camera.close()

capture = sim.camera_frame('camera_b')
# rgb: uint8 H×W×3; depth: float32 optical z in metres
# segmentation: int32 H×W×2 [MuJoCo object id, object type]
# K, dist, world_from_camera (OpenCV x-right/y-down/z-forward), time_s, timestamp
```

RGB, depth, segmentation, pose and intrinsics in each NPZ come from one captured state. Camera timestamps are monotonic capture times for live watchdog use; `time_s` is the separate simulation clock. `SimulationCamera` suppresses repeated cached timestamps, so a stalled renderer stops producing new observations.

Use `--calibration-dir /absolute/path/to/calibration` to load existing `Intrinsics` JSONs named `camera_a.json`, `camera_b.json`, `top.json`, etc. Intrinsics sizes must match `--resolution`. The renderer's pinhole output is resampled with `cv2.remap` to match the desired focal lengths, principal point and distortion. RGB is interpolated; depth/segmentation use nearest-neighbour sampling. Metadata includes the applied distortion. The real `TableCalibration` recovers camera A/B positions and table points within 1 cm in the 1280×720 render tests, including a nonzero-distortion test. Low-resolution, overhead and occluded-marker precision are not guaranteed.

The TCP inspection view follows the tool from nearby; it is **not a calibrated physical wrist-camera mount**.

To record interchangeable MP4/timestamp sidecars, pass the BGR frames to `lookandpick.adapters.camera.Recorder`:

```python
from lookandpick.adapters.camera import Recorder
from pathlib import Path
rec = Recorder(Path('recordings'), 'sim-camera-a', fps=10)
try:
    for i, (stamp, frame) in enumerate(SimulationCamera(sim).frames()):
        rec.write(stamp, frame)
        if i >= 99:
            break
finally:
    rec.close()
```

`GroundTruthHandDetector` projects 21 synthetic landmarks with camera K/distortion/pose and implements `HandDetector`. Use it with the existing `HandDetectionLoop`/projector to exercise plumbing. Hands in renders are simple palm/finger/forearm geometry. Ground-truth data does not validate a real detector.

## Safety and modelling limits

- The original `Tools`/`Safety` check every pick/place action. The simulation additionally checks hand separation against link-centre capsules on every 4 ms simulation step; a 25 mm link envelope is added to each hand's radius.
- Limits come from the upstream URDF. Position-only IK rejects unreachable and colliding goals. Joint interpolation checks candidate self/table/arm-link-to-object contacts each step, ignoring adjacent-link mounting overlap. Collision meshes are MuJoCo convex hulls, so geometry remains approximate.
- `set_speed_scale(0)` pauses ongoing motion and resuming continues. `halt()` latches independently; speed updates cannot release it. Stop bypasses the command lock. New concurrent actions are refused.
- Joints use velocity-limited **kinematic** servos at 0.7 rad/s. There is no STS3215 torque/current model, quantisation, communication delay or measured lag.
- A gripper close attaches a nearby object's centre within 35 mm; an empty/misaligned close refuses the grasp. Fingers do not push objects while closing. Attached poses follow the tool; releasing restores free-body gravity. This is an **ideal grasp**, not validated contact/force simulation.
- No motion planner reroutes around obstacles. Collision checks refuse a move instead.

## Tests, scenarios and replay

```sh
simulation/.venv/bin/python -m pytest simulation/tests -q
simulation/.venv/bin/python simulation/run_scenarios.py \
  --repeat 10 --output simulation/reports/acceptance.json
```

Some tests open temporary loopback HTTP servers. Contracts cover the existing fake arm/tracker and the MuJoCo adapters; the remote bridge is tested by running the unchanged `Tools` against it. Scenario events use the simulation clock and seeded object placements. Reports include separation, slowdown onset, synthetic stop latency, pick success, end state and per-step samples. These timings are simulated logic timings, not measured physical stop performance.

Built-in cases: clean pick/place, hand crossing, approach/withdraw, stop mid-move, detector timeout, two hands, held-object tracker occlusion, unreachable target, command refusal and simulated serial dropout. `FaultCamera` adds seeded drops/noise, exposure, black frames, repeated-timestamp freeze and wall-clock delivery delay. `FaultDetector` adds stall, crash, missing frames and injected `PixelHand` false positives. Arm fault flags include `command_refused`, `arm_stall` and `serial_dropout`; none touches a real bus.

**Export JSON** saves the last 15,000 trajectory samples (~10 minutes at 25 Hz), joints, link poses, objects, hands and events. Open `web/replay.html` directly from disk and load the JSON for a browser-only schematic 3D replay. Keep `replay.html`, `replay.js` and `style.css` together when copying the player. This is separate from the existing hosted Deck product.

The full comparison against `docs/sim-brief.md`, including missing features, is in `BRIEF-CHECK.md`.

## HTTP surface

All endpoints bind to 127.0.0.1, reject foreign Host/Origin headers and make no external requests.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/state` | Scene + arm joint/control state + camera configuration |
| `POST /api/action` | JSON action; blocking operations return a job ID |
| `GET /api/jobs/<id>` | `running`, `completed`, `refused` or `error` with result |
| `GET /api/cameras/<name>/rgb.jpg` | Latest colour view |
| `GET /api/cameras/<name>/metadata` | Pose, intrinsics, distortion, timestamps |
| `GET /api/cameras/<name>/frame.npz` | Synchronised sensor arrays |
| `GET /api/replay` | Trajectory and event export |

Actions: `pick {id}`, `place {id, position}`, `move {position, speed_scale}`, `joints {positions: six radians}`, `grip {closed}`, `speed {scale}`, `hand {position: XYZ or null}`, `camera {name, pose}`, `stop`, `reset`. Stop, hand and speed changes bypass a running job. Job history is bounded to 100 entries.

## Open source used

[MuJoCo](https://github.com/google-deepmind/mujoco) provides dynamics/rendering. The SO-101 URDF and 13 meshes are vendored unmodified from [TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100), under its Apache 2.0 licence. `assets/so101/SOURCE.json` records the source revision; `LICENSE.upstream` preserves the licence. The runtime MJCF applies the lime material, marker-frame base offset and simulation contact settings. New simulator, bridge, UI, scenario and test code was written during this hackathon.

`reports/acceptance.json` (per-step samples, about 5 MB) is git-ignored and regenerated by `run_scenarios.py`; `reports/acceptance-summary.json` is the committed copy without samples.

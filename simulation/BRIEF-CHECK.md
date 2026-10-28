# Check against docs/sim-brief.md

Checked 27 October 2026. This is a working first test bench, **not full completion of the brief**. Status below distinguishes tested behaviour from approximations and missing work.

The user's explicit destination takes precedence over the brief's suggested layout: code, tests, assets and dependencies live under `simulation/`, rather than `lookandpick/sim/` and `tests/sim/`. Heavy dependencies are isolated in this folder's requirements/venv; no core dependencies or calibration files were changed.

## Verified

- Python 3.14.4 installs and runs MuJoCo 3.17.0 from a wheel.
- Headless EGL renders the actual SO-101 URDF/meshes, with Pathfinder lime #D5FD51 printed parts. Browser UI provides movable orbit/fixed/overhead/TCP inspection views and joint/Cartesian controls.
- Arm and Tracker contract tests run against the existing fakes and MuJoCo implementation. A localhost client is also exercised by the unchanged `Tools` running in another controller context.
- Synchronous acknowledged moves, mid-motion pause/resume, independent stop latch, URDF joint limits, position-only IK, unreachable-target refusal and table collision refusal.
- Free rigid props with gravity/friction; clean pick/place through existing `Tools`/`Safety`.
- World origin = marker centre, z up; explicit arm-base offset defaults to `(0, -0.12, 0)`. Real build must be measured.
- Actual textured 120 mm ArUco marker. Camera A/B start 90° apart; optional overhead view. Intrinsics JSON loading and `cv2.remap` distortion; matched RGB, optical depth, segmentation, camera pose and K export.
- Existing calibration code detects the marker in MuJoCo renders and recovers both fixed camera poses and table positions within 1 cm at 1280×720. A nonzero-distortion render also meets the 1 cm pose threshold. Default low-resolution views do not have that precision guarantee.
- Camera FrameSource-compatible BGR output; MP4/timestamp-sidecar round trip through the existing Recorder/ReplayCamera.
- Ground-truth 21-landmark `PixelHand` projection; synthetic palm/fingers/forearm rendering. Continuous local hand/link separation checks and compatibility with existing HandSafetyMonitor.
- Seeded scenarios advance on the simulation clock. Seed-reproducibility assertion compares samples and end scene. Real-time mode and faster-than-real-time stepping are available.
- Scenario report records minimum separation, slowdown onset, synthetic hand/Stop/fault latency, pick success, end state and samples.
- JSON trajectory export and a browser-only schematic replay player requiring no Python.
- Loopback HTTP bridge, foreign Origin rejection, bounded request/job history, no external network calls from the simulator.

## Acceptance results

`reports/acceptance-summary.json`: seeds 0–9, 100/100 scenario runs passed.

| Scenario | Passed |
| --- | --- |
| Clean pick-and-place | 10/10 |
| Hand crosses path | 10/10 |
| Hand approaches and withdraws | 10/10 |
| Stop mid-move | 10/10 |
| Detector timeout mid-motion | 10/10 |
| Two hands | 10/10 |
| Held-object tracker occlusion | 10/10 |
| Unreachable target | 10/10 |
| Command refused | 10/10 |
| Simulated serial dropout | 10/10 |

The hand-crossing test inserts a hand abruptly inside the envelope and checks that motion pauses. It does not establish that an arbitrary high-speed incoming hand can always be kept outside the envelope. Recorded latencies use simulated time, not physical servo or OS worst-case latency.

Simulator suite: **28 passed**, including camera rendering and bridge integration. Existing project suite at the time of checking: **98 passed**. JavaScript syntax checks passed. Browser workbench inspected with live 3D rendering. No real hardware touched.

## Partial or missing requirements

| Brief area | Current implementation | Remaining work |
| --- | --- | --- |
| STS3215 behaviour | 0.7 rad/s capped kinematic joint tracking | 4096 ticks/revolution, P tracking/lag, torque/current caps and command latency are absent. |
| Physical grasp | Near-tool ideal attachment; bad/empty close refuses; release falls under gravity | Contact/force-based grasping, slip and payload validation. Fingers currently do not push props during closing. |
| Full prop set | Two labelled rigid cubes with seeded placement | Mug, apple, bottle and distinct light prop. |
| Camera performance | Default 640×360 at target 10 Hz; optional 1280×720/30 Hz; EGL headless | Sustained 30 Hz, CPU-only renderer, measured GPU/VRAM coexistence with SAM/cuRobo and native desktop viewer are unverified/absent. Browser workbench is the windowed UI. |
| Camera effects | Seeded noise, exposure, drop, freeze, black frame and latency wrappers | Motion blur, time-based event configuration, and complete end-to-end watchdog acceptance for each camera fault. |
| Ground-truth hands | Projected 21-landmark detector, injected hand positions, crossing/withdraw/two-hand cases | Full declarative trajectory library (wave/lunge/hidden hand), realistic articulated hand mesh, rendering both hands, and end-to-end projected-landmark-to-monitor tests across all trajectories. |
| Fault scenarios | Detector timeout, stop, command refusal, serial dropout acceptance; camera/detector fault wrappers; arm-stall flag | Stall/ghost/missing-hand/black/drop/freeze/delay/network-outage acceptance matrix is not complete. Serial dropout is a simulated flag, not a real serial adapter test. |
| Declarative runner | Python scenario records with seeded setup and named behaviours | General setup/event/assertion schema; event timing and fault probabilities supplied per scenario instead of fixed named branches. |
| Object occlusion | Tracker hides an already-held object; existing WorldState preserves it | Rendered camera occlusion and downstream detector reacquisition are not tested. |
| Contract matrix | Arm/speed/tracker and HTTP bridge contracts; hand projection and camera/Recorder plumbing | Shared parameterised FrameSource/HandDetector contracts across fake/sim/real hardware adapters. Real hardware remains unavailable. |
| Deck | Existing snapshot schema, bridge client and separate simulation workbench | Existing `lookandpick.mission_control` still uses FakeTracker/SimArm; selecting this backend in Deck is not wired. |
| Replay | Offline link/prop schematic from trajectory JSON, event data included | Full SO-101 meshes in offline player, precise variable-time playback and hosted Deck replay integration. |
| cuRobo | None; IK and collision refusal fallback | Obstacle export and GPU planner integration remain stretch work. |
| SAM / MediaPipe in sim loop | FrameSource and HandDetector plumbing supplied | No model quality claims; no full SAM/MediaPipe safety-loop acceptance on these renders. |

## Recommended next implementation order

1. Add the remaining hand/camera fault acceptance cases and projected-hand safety-loop tests; keep physical safety milestones unticked.
2. Add lag/quantisation/torque-limited joint simulation and replace ideal attachment with a force/contact grasp model.
3. Add named props and fully declarative scenario configuration.
4. Wire the tested client into Deck, then measure render rates/VRAM alongside SAM on the Legion.

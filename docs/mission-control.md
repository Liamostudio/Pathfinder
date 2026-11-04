# Mission control

Run from the project root:

```sh
.venv/bin/python -m lookandpick.mission_control
```

Open **http://127.0.0.1:8765**. Use `--port 8766` if the default port is occupied.
Stop the server with Ctrl+C. The built-in simulation needs no Node installation, frontend build, API key or GPU. Optional local reconstruction uses the isolated GPU runtimes described below.
The GUI and fonts are included when the Python package is built as a wheel.

## Local workspace setup

Choose **set up workspace** to load the completed experiment or reconstruct local
photos/video using MapAnything, SAM 3.1 and gsplat. GPU inference runs only during
setup; saved versions load without inference. See [local workspace setup](local-workspaces.md)
for runtime requirements, correction controls, exports and validation limits.

## First demo

Click **play demo**. It starts a fresh simulation, adds an explicitly scripted Muse Spark
example, introduces an approaching hand, blocks a pick with the hand in the path, clears
the hand, then picks and places the cube. Tools actually call the existing Python `Tools`,
`Safety`, `WorldState`, `FakeTracker` and `SimArm` classes. Scene animation illustrates the
completed simulated moves; it is not a robot kinematics model or a real-time control loop.

- Select either object to change the target and the path checked by the safety gate.
- **clear / approaching / in path** place a synthetic hand at a known distance from the
  current selected path. The readout is allowed speed, not measured arm speed.
- The round arrow picks the selected object. **place in drop zone** releases the held object
  when that object is selected. Errors and blocked actions appear in the timeline.
- **stop** latches the simulator. Space is also a stop shortcut, except when typing in an
  input or selecting text options. **new simulation** or **play demo** starts a fresh simulator.
- Fixed demo phrases include `pick up the red cube`, `pick the blue mug` and `place the red cube`.
  These use an explicit phrase parser. They do not call a language model.
- **session log** shows measured tool-call latency and input/output. Scripted model cues
  carry a separate label and no fabricated inference latency. The log retains 100 events;
  **export session** downloads a JSON snapshot.
- The theme key switches between Deck's light and dark themes; the choice stays in the browser.

## Media and calibration

**use webcam** asks the browser for video permission, without audio. **open recording**
opens a local video file. Preview media stays in the browser, without upload to this server
or a model service. **return to scene** releases the webcam and returns to the schematic.

Media preview does not feed detections or calibration into world state. There are no synthetic
tracking boxes drawn over camera footage, and robot controls continue to use the synthetic scene.
Use a browser-compatible encoding: WebM/VP8 was decoded successfully in the in-app browser;
OpenCV's default `mp4v` encoding was rejected by that browser. MP4 playback depends on the
file's codec and the browser. Unsupported recordings show an error and return to the scene.
Physical webcam preview has not been exercised in this session.

The calibration tab is a readiness checklist. Real-camera capture, lens calibration,
marker-to-arm alignment and the physical 1 cm acceptance check remain outstanding.

## Implementation and extension

- `lookandpick/mission_control.py`: standard-library loopback HTTP server and simulation session.
- `lookandpick/web/`: HTML, CSS, JavaScript and bundled fonts; no external runtime requests.
- `/api/state`: shared scene, gate status and timeline. The UI polls every 600 ms.
- `/api/action`: a bounded JSON request with a predefined action; no arbitrary code execution.
- Only loopback access is supported. Host/origin checks and JSON-only action requests reject
  unrelated websites; this is a local development tool, not a public server.

Before adding hardware, implement the watchdog, observation freshness and continuous monitoring
described in the [audit](audit-2026-10-27.md). Never substitute a real adapter into this GUI and
assume the schematic, browser polling or stop button constitutes a hardware safety system.

The next integration is to publish timestamped tracker observations into world state and add
honest model-call events (model name, input, output, latency). Keep the detector watchdog and
hardware stop enforcement local, independent of the browser.

## Design sources and validation

Deck v1 comes from Liam's [shared design guide](https://github.com/Liamostudio/Looper/blob/main/Docs/Deck-UI-System/DESIGN.md)
and [tokens](https://github.com/Liamostudio/Looper/blob/main/Docs/Deck-UI-System/deck-tokens.json).
The interface uses the grey chassis, white keys, black controls, lime accent and both themes.
Space Grotesk and Space Mono are bundled from Liam's Scrypt repository, with their font licence
texts alongside the font files. The scene is code-drawn artwork, not camera or SAM output.

Validation: 62 Python tests pass, including 14 mission-control cases covering scene updates,
hand slowdown/blocking, stop/reset, parser behaviour, labelled model cues, bounded history,
HTTP actions and static-path/origin boundaries. JavaScript syntax checks pass. Wheel build
and asset inclusion pass. Browser checks covered the guided demo, blocked pick, stop/reset,
both themes, calibration view and a 390 px layout without horizontal overflow. Local WebM
decoding and the unsupported-recording error were checked. Webcam permission was not requested.


## Simulator Runtime backend (3 November)

Start the MuJoCo bridge with `simulation/.venv/bin/python simulation/start.py`. Then run `.venv/bin/python -m lookandpick.mission_control --backend sim --model fake`. The default backend remains the original model-free workbench. To keep both open, add `--port 8769` to the Runtime Deck. `--model spark` uses the existing configured Meta account, spend cap and CallLog; `--model glimmer` uses the existing local model configuration. Keys never enter the browser.

Commands run in a worker through `Runtime.command`; `POST /api/command {"text":"..."}` immediately returns a job ID (202). Poll `GET /api/commands/<id>`. `POST /api/stop {}` bypasses the command lock and model; `GET /api/timeline?since=<id>` returns the existing CallLog schema, including cost and token counts. Reset waits until a command has returned. The UI keeps polling while commands run, and STOP remains enabled. The schematic uses simulator state. Use “Saved workspace / voxel editor” to inspect the independent visual workspace.

### Hand gate

The hand-gate panel reads `GET /api/hand-gate`, shows scale, nearest distance, hands, fps, p50/p95 latency and age, and marks observations STALE after 150 ms. Missing/invalid sources show NOT CONNECTED. Without a camera gate the Runtime backend explicitly labels its feed as simulation ground truth; it has no measured camera latency.

Claude's live gate can run **on the simulated arm** in the same process using `--backend sim --hand-gate 2 --intrinsics calibration/ace-pro.json` in the hands-capable environment. Starting a camera is explicit; no camera or hardware is opened by default. RuntimeSession accepts `runtime.hand_gate_status` directly. Alternatively `--hand-gate-status-url http://127.0.0.1:8768` proxies Claude's read-only status server (including `python -m lookandpick.status_server --fake`). External status is labelled display-only and does not impose a gate on this Deck's arm. “Preview all gate states” is a labelled UI fixture, never a safety source.

### Real-arm panel and traces

The real-arm view starts NOT CONNECTED. Only “Open read-only telemetry” opens a named serial port. It reads IDs, positions, voltage and temperatures, draws calibrated ranges and a Rest tick, leaves torque state unknown, and never calls a follower or motor-writing method. It checks for existing port users and reserves an opened port with kernel exclusion. Failures close the port; reconnection is manual. Disconnect before running a terminal bench tool. Actual hardware telemetry/port-contention acceptance still needs a bench check; automated verification uses fake buses and never opens the arm.

Load a bench `--trace` CSV locally for per-move voltage/time and voltage/observed-angle charts, coloured motor legends and lowest-point annotations. Malformed data produces a visible error. The recorded sample demonstrates every joint without serial access. Telemetry connection and under-voltage events use source `real_arm` in the shared timeline.

### Replay and filming demo

`replay/` is a static offline-capable submission player; `demo.json` contains one deterministic MuJoCo run and `demo-data.js` embeds the same data for file-folder playback. It includes the trajectory, model/tool timeline and hand-gate states. No hosted deployment has been made. Open `/replay/index.html` or serve the folder with `python3 -m http.server`.

On the Runtime backend, Run demo builds the seeded MuJoCo scenario in an isolated simulation subprocess, then **plays the recording**, explicitly labelled. This prevents wall-clock camera/model jitter from changing the filming sequence. It uses a scripted model, an injected hand which slows/stops/resumes the arm during pick/place, and STOP during a second move. Three independent exporter runs must match exactly. It does not run a real camera or hardware. STOP also cancels demo generation. The original fake workbench retains its simple scripted button demo.

`GET /api/replay` exports Runtime trajectory samples and CallLog events from the current session. The export-session button downloads this replay in Runtime mode. Recorded simulated scenes contain no source photographs. Regenerate the packaged demo with `simulation/.venv/bin/python -m lookandpick.deck_demo --output replay/demo.json --embed`.
## Motion status and controls (4 November 2026)

Deck accepts these optional fields in `/api/state`:

```json
{
  "stop": {"kind":"fault", "reason":"Camera stalled", "recovering_in_s":1.2, "resets_left":2},
  "route": {"kind":"over", "waypoints":[[0,0,0.3],[0.2,0,0.3],[0.2,0,0.1]]}
}
```

Stop kinds are `person` and `fault`. Route kinds are `over`, `around` and `hand`,
with waypoints in marker-frame metres. Absent fields clear the corresponding
display. A person stop shows **STOP (you)** and never displays an automatic
countdown. Fault recovery is decided by the runtime; Deck only shows its supplied
countdown/reset allowance and marks stale updates unavailable.

The adapter reads `Runtime.motion_status() -> dict`, or an injected
`RuntimeSession(..., motion_status=callback)` for a fake feed. Only `stop` and
`route` are copied; a display feed cannot override the safety latch or job state.
`stop_distance_m` comes from the actual session safety configuration. The controls
send `cancel` and `carry on` directly to `/api/command`, independently of a running
instruction. Cancel uses the existing local runtime cancellation; Carry on calls
`Runtime.resume()` and is disabled until that method is available. Neither goes
to a model. The reset button retains its existing action. Claude owns the recovery,
Rest, retry and route-execution logic; this UI does not implement those decisions.

Short `Arm` and `Safety` timeline entries appear in the status area and the
tools/safety filter. Routes appear in the runtime schematic and, when marker
alignment is available, in the saved/setup CAD viewer. CAD conversion uses
`canonical_from_marker` and the workspace's metres-per-unit scale; unanchored
scenes do not invent a transform. Fixed obstacles are the user's manual no-go
cubes, not automatically detected objects. A drawn route is a simulator preview,
not physical clearance or full-arm collision certification.

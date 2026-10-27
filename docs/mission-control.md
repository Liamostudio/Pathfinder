# Mission control

Run from the project root:

```sh
.venv/bin/python -m lookandpick.mission_control
```

Open **http://127.0.0.1:8765**. Use `--port 8766` if the default port is occupied.
Stop the server with Ctrl+C. No Node installation, frontend build, API key or GPU is required.
The GUI and fonts are included when the Python package is built as a wheel.

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

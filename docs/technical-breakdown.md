# Pathfinder: technical breakdown (models and techniques)

Written 4 November 2026, the last day of the hackathon (26 Oct to 4 Nov 2026). Every statement is tied to code in this repository or to an entry in [`PROGRESS.md`](../PROGRESS.md), which is the evidence ledger. Where something was only tested with fakes, or never run, this document says so. Status words used throughout:

- **Live**: seen working on real hardware, a real camera or the real Meta API.
- **Sim**: working on the MuJoCo simulated arm with real software around it.
- **Fakes**: tested with fake adapters or recorded data only.
- **Not done**: designed or discussed, not built, or built but never run.

## 1. What it is, in one paragraph

Pathfinder is a safety layer that lets a table-mounted robot arm (a self-built SO-101) work around people. **Meta's models see, understand and decide; a small, checkable, local layer enforces; the models never drive the motors.** A person speaks a command through Meta glasses, a model turns it into a call on five constrained tools, and every tool call passes through a local speed-and-separation gate, a stop latch and hard no-go rules before the arm is commanded. Cameras watch the workspace and a hand entering the arm's path slows then stops it. A scanned 3D copy of the workspace carries an editable voxel map where the person draws no-go cubes.

## 2. Architecture

```
 SPEECH / VIEW                    MODELS (decide)                        LOCAL LAYER (enforces; no network)
 Meta glasses mic -> iPhone app   Muse Voice Transcribe (API)            +------------------------------------+
   -> HTTPS relay (laptop)    ->  Muse Spark 1.3 (API): sees scene,      | Tools: get_scene pick place stop say|
   -> Deck /api/glasses/*         picks tool calls                  ->   | ManualZoneGuard (no-go cubes)       |
   -> review + confirm            SAM 3.1 (local GPU): finds objects     | Safety: speed-and-separation, latch |
                                                                         | Hand gate: 150 ms watchdog          |
 CAMERAS (2, fused)                                                      +----------------+-------------------+
   fast: MediaPipe hands 28-32 fps  ------------------------------------>   speed scale   |  halt / zero speed
   slow: SAM 3.1 people+hands ~1 Hz -> voxel map only (never the safety path)              v
                                                                         MuJoCo SO-101 (sim)   |   real SO-101 (separate, bench only)
```

Shared world state; no model calls another model. The fast loop overrides everything. The three rates are: fast (hand gate, 15 to 30 Hz), medium (triangulation and map update, 1 to 2 Hz), slow (model calls, on events).

### The rules the code enforces (non-negotiable, from `CLAUDE.md`)

- Safety-critical checks run locally and never depend on the network.
- Slow as people approach, stop at minimum distance, rather than hard pauses.
- "Stop" bypasses everything: it is a local word handled before any model is asked.
- The real arm is only ever powered with Liam present and the 5V kill switch in reach; never from a remote or unattended session.

## 3. Models used

| Model | Vendor | Job | Where it runs | In the safety path? | State |
| --- | --- | --- | --- | --- | --- |
| **SAM 3.1** | Meta | Text-prompted segmentation: table/wall/robot masks in workspace setup; "person" and "hand" masks live; finds named objects for pick targets | Local GPU (RTX 5070 Laptop, 8 GB). Weights gated, not in Git | **No** (answers in 0.6 to 1.0 s, too slow for a 150 ms watchdog) | Live (people feed on a real camera, 4 Nov); Sim (object finding on sim frames) |
| **MapAnything** (`facebook/map-anything-apache`) | Meta | Joint camera pose and depth from 2 to 4 photos for the workspace scan | Local GPU, offline pinned revision | No | Live on photos (4-photo scans); reconstruction quality not independently measured |
| **Muse Spark 1.3** (`muse-spark-1.3`) | Meta | Orchestrator: reads scene JSON (+ glasses photo), resolves "this one", calls the five tools; also names objects on the table (vision) | Meta Model API | No (it proposes; `Tools` and `Safety` decide) | Live against the sim arm, typed and spoken |
| **Muse Voice Transcribe 1.0** | Meta | Spoken command to text (file upload endpoint) | Meta Model API | No | Live on Liam's speech and through the glasses |
| **Meta glasses** (Adventurer) + **Wearables Device Access Toolkit 1.0.1** | Meta | Microphone (Bluetooth HFP through the iPhone); replies read back in the glasses | iPhone companion app | No | Live (spoken commands and replies, Liam's report plus timeline) |
| **MediaPipe Hand Landmarker** | Google | Fast hand detection, 21 landmarks per hand, CPU | Local CPU | **Yes: the only person-safety detector** | Live (two cameras, about 32 fps) |
| EfficientDet-Lite0 | Google | Person boxes in the browser path only | Local CPU | No | Optional; exact weights licence unverified |
| gsplat | Nerfstudio | Gaussian splat fit for the photographic workspace view | Local GPU | No (visual only) | Live |
| Depth Anything 3, SAM 3D Objects | ByteDance, Meta | Optional developer-tool prototypes, outside the demo path | n/a | No | Not part of the demo; SAM 3D Objects mocked only |

**Not used:** Muse Glimmer (client written, nothing served), Muse Wonder, Muse Image, Spark Open, V-JEPA 2, cuRobo (planned, not integrated). Licences and provenance are in the licences document on the `readme-video` branch and `docs/dependency-provenance.md`; no model weights are in Git.

## 4. Techniques, by subsystem

### 4.1 The fast hand gate (the only person-safety path)

Files: `lookandpick/hand_gate.py`, `live_hands.py`, `hand_safety.py`, `adapters/hands.py`, `calibration.py`. Docs: `docs/hand-safety.md`.

- **Detection:** MediaPipe Tasks Hand Landmarker, CPU, VIDEO mode. 21 pixel landmarks per hand.
- **Pixel to table:** the ArUco marker lies flat on the table, so its plane is the table plane. `solvePnP` on the four marker corners gives the camera pose; each landmark's viewing ray is intersected with a horizontal plane at an **assumed hand height** (0.08 m in the live Deck). No metric depth is inferred, and MediaPipe's relative z is not used as distance. The assumption is explicit, not hidden.
- **Hand envelope:** a sphere enclosing the projected landmarks plus a 3 cm development margin. The live Deck reports envelopes of roughly 14 to 20 cm radius, which makes the gate conservative.
- **Two cameras, one gate:** each camera projects into the shared marker frame and the newest hands from both are combined into one observation for one watchdog. A hand seen by either camera slows the arm; if either camera stalls, the arm stops. Each camera needs its own lens calibration and must see the marker.
- **Independent watchdog:** polls at 100 Hz with a 150 ms freshness deadline. No initial result, a stalled capture, stalled inference, malformed geometry or timing, a source failure or end of recording **stops and latches**. A late result cannot re-enable motion. A failed monitor cannot be re-armed by clearing the latch alone: a new monitor must be built and armed.
- **Fail closed:** if the gate is not running, `hands()` reports a huge hand at the arm, so every move is refused. A fresh observation with no hands is distinct from no observation.
- **Measured (live, 4 Nov midday, Ace Pro 640x360 + UGREEN 1280x720, both calibrated from the marker at 0.25 and 0.33 px):** fused gate about 32 fps, p50 37 ms, p95 60 ms. In an 80 s hand test the gate went clear, slow, stop, clear. Not measured against ground truth.

### 4.2 Speed-and-separation and the stop latch

File: `lookandpick/safety.py`. The allowed speed scale is 0 at or inside the stop distance, 1 at or beyond the slow distance, and linear between: `(separation - stop) / (slow - stop)`. Separation is the distance from the hand's envelope to the **segment** the arm is about to travel, not to its current point. Defaults: stop 0.15 m, slow 0.50 m; the simulated arm uses 0.10 m so a route 10 cm from a hand can proceed; the real-arm demo uses 0.10 m measured from the hand's surface.

- **Two kinds of stop:** a *person* stop (STOP button, space bar, spoken) and a *fault* stop (camera stall, detector loss, a move that made no progress). A person's stop is never downgraded and never clears by itself; only "carry on" or reset clears it.
- **Recovery supervisor** (`recovery.py`, sim arm): after a fault stop it rebuilds the hand gate, holds zero speed until the way has been clear for 2 s, then lets the interrupted move continue, at most 3 times per minute.
- **Waiting versus refusing:** `Tools` waits (re-checking every 50 ms) only when the *sole* reason for zero speed is a hand in the way ("Pausing: a hand is in the way" ... "Clear, carrying on"). Every other zero (a latch, invalid geometry, gate not running) is an immediate refusal.

### 4.3 The slow, rich layer: SAM 3.1 people and hands

Files: `sam_people.py`, `adapters/sam_client.py`, `adapters/sam_masks.py`, `scripts/sam_worker.py`.

SAM 3.1 is prompted with text ("person", "hand") on the live frame about once a second. A single camera gives each mask pixel a ray, not a depth, so every sampled pixel is projected onto several heights above the table, marking a line of cells along the ray, conservatively (more red, never less). The result paints the voxel map. It is isolated: its own thread, read-only access to frames, and any failure only means the map stops updating from SAM. **It cannot touch the arm, the latch or the watchdog.** The accurate description is: fast and simple enforces, slow and rich explains. SAM 3.1 is not in the safety loop.

Live evidence (4 Nov): with nobody in view only the 18 manual no-go cubes were red; when a person entered, the people feed marked 114 more cubes then 200 to 250, and the map held steady after a flicker fix (the feed re-offers detections for up to 1.5 s). About 700 cubes occupied with two cameras and a body in view.

### 4.4 Voxel working areas and no-go cubes

Files: `workspaces/voxels.py`, `workspaces/areas.py`, `zones.py`, `voxel_feed.py`, `routing.py`. Docs: `docs/voxel-working-area.md`.

- **A bounded table volume** (up to 4,096 cubes), anchored to the ArUco marker (detected on original source pixels, never on reconstructed texture).
- **Fail-closed occupancy:** a missing observation never means free space. Cubes a camera has seen turn red; **no cube ever turns green from a camera**, because an RGB camera cannot certify empty space. Unknown (amber) cubes do not block moves, otherwise nothing could ever move.
- **Manual no-go cubes are hard rules:** `ManualZoneGuard` samples the arm's path every centimetre, with the tool's radius, against the **active saved workspace version's** policy. A path crossing a blocked cube is refused before the arm is commanded and the model is told why. Local and instant; no network. With no workspace, area or marker alignment there are no zones.
- **Routing:** a bounded waypoint search (up-and-over preferred to lateral detours) with a hand clearance of 0.10 m and tool radius 0.04 m. "None" means no route in the candidate families, not a proof that none exists. A detour around a hand only helps when the hand is clear of the whole arm.
- **Fixed obstacles are marked by the user, not detected.**

### 4.5 Orchestration: five constrained tools

Files: `models/orchestrator.py`, `models/meta.py`, `models/calllog.py`, `tools.py`, `runtime.py`.

Muse Spark is reached through Meta's OpenAI-compatible Model API, with the standard library only. It can only call `get_scene()`, `pick(id)`, `place(id, loc)`, `stop()`, `say(text)`. Each call is validated, then executed through `Tools`, which applies the zone guard, the speed gate and the latch. "Stop" and "cancel" are local words handled without asking the model. Every model call is recorded by name with input, output, latency and cost and shown in the Deck timeline. Costs observed: Voice Transcribe about $0.0003 to $0.0004 per clip; Spark $0.002 to $0.009 per call; the 4 Nov glasses session totalled $0.0117.

**Language to target** (`scene.py`, `scene_labeller.py`): Spark names the objects on the camera frame (**only when the picture changed**, to protect credits), SAM 3.1 segments each name, and the calibration turns the mask into a table position. If the table cannot be seen the runtime refuses to move rather than act on a scene it could not look at. Verified on sim frames; not yet on a real camera with real objects.

### 4.6 Voice and glasses path

Files: `voice.py`, `glasses.py`, `glasses_input.py`, `glasses_gateway.py`, `glasses_api.py`, `mobile/PathfinderGlasses/`. Doc: `docs/glasses-transport.md`.

Glasses Bluetooth microphone, iPhone WAV, authenticated HTTPS relay on the laptop (a development CA, a bearer pairing token, only glasses routes forwarded, browser-origin requests refused), loopback Deck, Muse Voice Transcribe, **transcript review and explicit confirm** (90 s window), then the normal runtime. A STOP increments a generation counter and revokes every pending transcript; a direct local STOP route exists that is independent of the speech and model queue. Live on 4 Nov: two spoken commands through the glasses, real transcription and real Spark, a pick and place in the sim including a grip miss that the retry logic recovered, then a local STOP. The native iOS app was written without Xcode on this host; its build and device acceptance were done by Liam on his phone and are not independently checked here.

### 4.7 Workspace reconstruction and the table texture

Files: `lookandpick/workspaces/`, `gaussian_studio/`, `table_texture.py`, `workspace_bridge.py`. Doc: `docs/local-workspaces.md`.

Photos or video, 2 to 4 overlapping views, run sequentially in isolated subprocesses on the 8 GB GPU: **MapAnything** (cameras and depth), **SAM 3.1** (table, wall and robot masks), planar surface fitting, **gsplat** (Gaussian fit), preview export. Versions are immutable and explicitly activated; drafts and failed rebuilds leave the active version intact. A GPU lease serialises SAM and reconstruction.

- **Marker-plane texture:** the marker's four corners define the table plane exactly, so a homography flattens one photo into a metric top-down texture; the marker comes out a square 120.0 mm with 90-degree corners, whatever the camera pose or lens. Heights above the plane are not corrected, so it is used for the flat top only.
- **Placement:** the robot model is placed from the measured calibration (offset, yaw). The calibrated yaw (138.1 degrees) is the pan-zero direction, not the physical foot orientation (180 degrees), so a stored visual pan offset of -41.87 degrees is applied to drawn joints. Visual only; never a safety input.
- **Limits:** reconstruction quality is not independently tested; scale comes from the measured marker; more than 4 views ran out of GPU memory on the 8 GB card.

### 4.8 Kinematics and real-arm calibration

Files: `kinematics.py`, `adapters/so101_profile.py`, `adapters/so101.py`, `scripts/so101_touch.py`.

- **FK/IK in pure Python** from the vendored SO-101 URDF: position-only IK by damped least squares from a seed (three equations, four joints), shared by the simulator and the real arm. It checks joint limits only; collision, table and hand checks belong to the safety layer.
- **Tip-touch calibration (3 Nov):** 20 touches by hand with torque off, then a fit of small zero offsets in shoulder_lift, elbow_flex and wrist_flex plus a tool-length correction. Held-out error (5 touches left out, 40 random splits): median 2.1 mm in tip height. The base's offset and yaw in the marker frame come from five marker touches (two rounds agree to 5 mm and 0.5 degrees). The calibration only covers the touched region.
- **Commissioned profile:** joint ranges from the LeRobot calibration minus a 3 degree margin; workspace box and a minimum reach from the pan axis; 10 deg/s ceiling; 1 cm Cartesian steps planned in full before anything moves; refuses to build unless the touch fit's held-out median is at most 5 mm.
- **`SO101Arm` safeguards:** live mode needs `--i-am-present`, a local TTY and the typed phrase `5V KILL SWITCH READY`; position-servo goals mean pausing must also freeze goals; any exception halts.

### 4.9 Simulation

`simulation/` (MuJoCo 3.17, the SO-101 URDF, Pathfinder lime parts): kinematic servo, ideal-grasp rule, two cameras 90 degrees apart, bridge client implementing `Arm`, `SpeedControlledArm` and `Tracker`. 100 seeded scenario runs and `scripts/sim_motion_check.py` (10 of 10 motion behaviours) pass. The sim is where the safety layer was validated; it is not a certification.

### 4.10 Grasp orientation (sim only)

`object_geometry.py`: a visual hull carved from two or three SAM masks and calibrated cameras gives length, width, height and long-axis yaw, so the wrist can align to a pen or bar. Width is an upper bound (safe for a jaw-opening check). Tested on synthetic views and masks rendered in the sim (cube, can, pen, toy duck); concave toys not handled; not on real cameras or wired into the live tracker.

### 4.11 Deck, launcher and replay

`mission_control.py` and `web/` (Deck on port 8765): the operator UI, a 3D Run view of the saved workspace with the simulated arm, hands, voxels and a caption strip, timeline of model calls, STOP. `lookandpick/launcher/` is a loopback-only, click-driven service launcher with preflight checks. `replay/` is a static, offline, clearly labelled recording of one simulator run (no backend or key), the basis of the hosted replay.

## 5. The real arm: the hand-stop test (4 Nov evening)

Files: `scripts/so101_hand_stop_demo.py`, `lookandpick/real_arm_demo.py`, `docs/real-arm-hand-stop-bench.md`.

A slow shoulder-pan sweep on the real SO-101 that slows and freezes for a hand, then carries on and folds back. Design: the Deck owns both cameras, so the arm script (a different Python environment) reads the camera hands the Deck's gate publishes on **loopback** (`hand_gate.positions`) and does its own speed-and-separation against the real tip path; a stale, missing or latched answer means "cannot see": the arm **freezes with torque held** and does **not** fold (moving blind is what the safety layer exists to prevent). Freeze means goals set to the present pose (torque kept), not a limp arm. A supply collapse or a run of failed bus reads releases torque as a last resort and says to cut the 5V supply.

**Seen on the real arm (Liam at the bench):** supply 5.3 to 5.4 V under torque; the sweep slowed as a hand approached (scale 0.10 at a 14 cm gap), froze at scale 0.00, and carried on 0.3 s after the hand cleared; a blind moment (Deck gate latched) froze and resumed.

**Found and fixed on the bench:** the first live run did not stop (the script read the simulator's empty `scene.hands` instead of the gate's positions); one bus glitch released torque; a wrist at -94 degrees put the tip outside the workspace box and was refused; the first fold target (2 degrees inside the end stops) asked the folded arm to lift itself and faulted, so the target is now the exact start pose.

**Not verified:** the fold-and-release completing on the arm after the fix; the 10 cm stop distance is a chosen margin, not a measured safe distance, and the stop latency is unmeasured. The real-arm hand check depends on loopback HTTP freshness from the Deck (it fails closed) and is not an in-process gate like the simulator's.

## 6. Honest limits and gaps

- The demo arm is the **MuJoCo simulation**. The real SO-101 is built, calibrated (about 2 mm held-out tip-height error) and has had joint-level powered tests and the hand-stop sweep above. No live Cartesian pick through the full pipeline on the real arm.
- Fixed obstacles are marked by the user, not detected.
- The safety layer is validated on the simulator and on the bench sweep above; it is **not certified**, and a camera cannot certify empty space.
- A hand's position uses an assumed height, not measured depth.
- SAM 3.1 and MediaPipe sometimes disagree (SAM reported a person and a hand for seconds while MediaPipe posted nothing; unexplained).
- Marker scale: the camera reads the checkerboard about 3 per cent large; the print or marker scale still wants a ruler.
- The DAT SDK, hosted API terms and some library licences are unverified (see the licences document).
- cuRobo is not integrated. Glimmer, Wonder, Image and V-JEPA 2 were not used.
- GPU: SAM holds about 2.3 GB of the 8 GB card; MapAnything with four views ran out of memory until fewer views and `expandable_segments` were used.

## 7. Evidence and how to reproduce

- Ledger: `PROGRESS.md` (each entry says what was tested and how). Last full-suite figures recorded there (3 and 4 Nov): 609 to 632 workspace/core Python tests, 98 MuJoCo/kinematics tests, 15 JavaScript tests, run with `scripts/test_all.sh`. The real-arm demo adds 52 tests in `tests/test_real_arm_demo.py` (fake bus and fake hands).
- Run with no keys and no hardware: `python -m lookandpick.mission_control --backend sim --model fake` (start `simulation/start.py` first for the MuJoCo arm), then open http://127.0.0.1:8765. Environment details are in `docs/demo-2026-11-04.md` and the README.
- The replay needs nothing: open `replay/index.html` or serve the folder.
- Secrets: API keys live in `.env` (git-ignored); the glasses pairing token and TLS keys stay out of Git.

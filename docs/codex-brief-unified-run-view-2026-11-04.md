# Codex brief: one window, the live run inside the 3D workspace (4 Nov 2026, evening)

Read first: `CLAUDE.md` (safety rules are non-negotiable), `docs/demo-2026-11-04.md`, `PROGRESS.md` (4 Nov entries), `docs/mission-control.md`, `docs/voxel-working-area.md`. Commit only your own files; pull (`git pull --rebase --autostash`) before every commit; PROGRESS entry saying what was tested and how; tick no milestone; British English.

## The problem

Deck has two different pictures of the same thing. The **Runtime schematic** (a flat 2D SVG: `#scene-svg`, plus the `#motion-route` overlay) shows the live run: the arm, the objects, the hand, the plan. The **Saved workspace / voxel editor** (WebGL: the Gaussian photographic background, the CAD layer with voxel cubes and the robot model, `workspace-renderer.js`) shows the real scanned table and the no-go cubes, but not the run. Liam has to flip between them (`#show-sim-view` / `#show-saved-view`), and for the film that is the wrong way round: **the thing on screen while the arm works should be the 3D workspace, with the run drawn inside it.**

## What to build: a single Run view

When a saved workspace is active, Deck's main (Workspace) page shows the 3D workspace scene by default, with the live run drawn into it. The 2D schematic remains only as the fallback (no active workspace, or WebGL unavailable) and as an explicit small "Schematic" switch; the two-button toggle goes away. Keep the right-hand control column (safety panel, action panel, hand gate panel, STOP / Reset / Cancel / Carry on) and the session log below, as they are.

Drawn in the 3D scene, live, every poll (use the existing `JointEase` smoothing):
1. **The simulated arm**, driven by the Deck state's `joints` (radians, SO-101 URDF joint names: `shoulder_pan ... gripper`). These are the simulator's own joint angles in the URDF convention, so feed them straight to the robot model with NO real-arm calibration offsets (those apply only to the read-only real arm telemetry, `urdf_joint_radians`).
2. **The objects** (`scene.objects`: id, label, position in the MARKER frame, `held`) as small coloured boxes, the held one riding with the gripper. Colour from the label (red cube red, blue cube blue); label on hover or always.
3. **Hands and people** from the hand gate (`scene.hands`, with `radius_m`) as translucent spheres, plus the live voxel cubes already coloured by the voxel service (red from hands and people, amber unknown, manual no-go cubes) via the existing `VoxelCubes` live mode and `/api/workspaces/<id>/voxels`.
4. **The plan**: `route.waypoints` as a polyline (the pieces already exist: `routeVertices` / `routeFrame` in `motion-view.js`), the target highlighted, and the stop-distance bubble around the gripper tip (`stop_distance_m` is in the state).
5. **A caption strip** over the scene for the film: the latest spoken command (the `Meta glasses` / `Muse Voice Transcribe` timeline entry), what Spark answered, and the arm's short status lines ("Pausing: a hand is in the way", "Going around", "Clear, carrying on", "Heading back to rest"; these are `source: Arm` or `Safety` timeline entries).
6. **State banners** from the existing fields: `stop` (kind `person` / `fault`, `reason`, `recovering_in_s`, `resets_left`), `hand_gate.state`, `busy`, and the speed scale.

Marker-frame to scene: use the workspace's marker alignment (`canonical_from_marker`, the same transform the voxel cubes use). All run data is in the marker frame.

## First, the arm is in the wrong place today (found 4 Nov evening)

Liam sees the robot model on the LEFT while the real arm in the scan is on the RIGHT. Cause (checked in `recordings/workspaces/ws-380f51eac8164bda/versions/v0003/workspace.json`): the saved robot placement is `illustrative`, a hand-set default (`translation [0.335, 0, 0]`, `rotation [0, 0, 180]`), and the marker alignment was saved with `marker_size_measured: false`. The measured placement exists (`lookandpick/workspaces/placement.py::measured_placement`, from `calibration/so101-touch-2026-11-03.json`: base offset (+0.217, -0.031) m, yaw 138.1 degrees) but was never applied to this workspace. It needs a re-anchor with the measured 120 mm black square first (`MARKER_MEASUREMENT`), then the measured placement. **Do this FIRST, on the existing scan, no re-scan needed**, and verify in the 3D view that the model stands on the real arm in the scan. A fresh scan would come out `illustrative` too until the same two steps are run, so also make the ingest flow (and the launcher's Setup stage) run them automatically after the marker is anchored. Check that the left/right is not a mirroring or axis-handedness bug as well: after the measured placement is applied the model must stand on the real arm; if it is still mirrored, report it before building anything on top.

### Found while placing the measured base (4 Nov evening): the pan-zero direction is NOT the foot's orientation

`measured_placement` draws the robot with the calibration's yaw (138.1 degrees in the marker frame). That is the direction the arm points at shoulder_pan = 0 (LeRobot's pan zero is the middle of the servo travel), and the tip-touch fit is correct for it. But Liam's physical foot is square to the table edge, which is yaw 180 degrees in the marker frame (checked by drawing the predicted base on his photos: the base POSITION lands exactly on the real foot). The foot drawn at 138 degrees is therefore 42 degrees off. Correct, visual-only treatment: draw the foot at the physical yaw (180 in the marker frame, scene -6.2 degrees in the current scan) and add a constant **pan draw offset of -41.9 degrees** (= calibrated yaw 138.1 minus foot yaw 180) to the shoulder_pan angle of anything drawn from calibrated readings. Put both constants in the calibration data, not in the renderer, and apply them in `measured_placement` / `urdf_joint_radians` (Codex files) so the editor, the live read-only arm and the Run view all agree. The editor only has number fields for yaw and sliders (step 0.01 rad) for joints, so exact values are awkward to type: expose the pan offset as a stored calibration value. This is not a safety input.

## The one real decision: where the simulated arm sits

The simulator's arm base is the placeholder `arm_base_offset = (0, -0.12, 0)` in the marker frame (it is in the simulator's `/api/state`, NOT yet in Deck's state). The workspace robot model is placed from the MEASURED real base (offset (+0.217, -0.031) m, yaw 138.1 degrees, `lookandpick/workspace_bridge.robot_placement`, status `touch_calibrated`). They disagree by about 25 cm. If the simulated arm's joints are drawn on the measured-base robot, the gripper tip will NOT land on the cubes.

- **Phase 1:** draw the simulated arm at the simulator's own base (a THIRD position, neither the left default nor the real arm's spot, so on its own it does not fix the 'wrong place' complaint; it only makes the tip land on the cubes). Add `arm_base_offset` (and the frame label) to Deck's `/api/state` (copy it from the simulator state in `deck_runtime.py`) and place the run robot there, labelled "simulated arm". Do not move the static real arm model if the scene shows it; if the photographic background contains the real arm, say so in the caption ("real arm: static, not powered") so two arms do not confuse a viewer.
- **Phase 2 (this is what makes the animated arm stand where the scan's arm stands; Liam to decide tonight):** move the simulator's base to the measured one so the simulated arm sits exactly where the real one stands. That changes reach, the Rest pose and every preset; it needs Claude to re-run the motion checks (`scripts/sim_motion_check.py`) afterwards. Do not do it silently.

## Data you have (all already in Deck's `/api/state`, polled)

`joints`, `scene` (objects, hands, arm_position), `hand_gate` (state, scale, nearest_m, hands, fps, `cameras` when two are fused), `stop` / `route` (absent when nothing applies), `busy`, `active_job`, `stop_distance_m`, `motion_controls`, `events` (the timeline: `source`, `input`, `output`, `latency_ms`, `cost_usd`). Voxel state and sources: `/api/workspaces/<id>/voxels`. Poll at about 10 to 20 Hz for the arm and objects, the voxels as they do today.

## Replay (the payoff, keep it in mind)

Each Deck frame already records `scene`, `joints`, `hand_gate`, `stop` and `route` (`/api/replay`). Build the Run view so it can render from a recorded frame as well as from live state (one `render(frame)` function). The hosted replay then plays the SAME 3D view from a take with no backend. Do not build the hosted page now, just do not make live polling the only way in.

## Constraints (non-negotiable)

- Read-only display. Nothing in this view may start, power or command the real arm, and it must not call `/api/real-arm/connect` or `/disconnect`. The real-arm page stays separate and unchanged.
- Do not change safety logic. Do NOT edit `lookandpick/tools.py`, `safety.py`, `runtime.py`, `hand_safety.py`, `hand_gate.py`, `recovery.py`, `zones.py`, `routing.py`, `models/*`, `adapters/*`. Ask Claude for anything you need from them (the contract above is what the runtime already provides).
- Performance: the SAM worker holds about 2.3 GB of the 8 GB GPU while the browser renders the Gaussian background. Keep the photographic layer optional (a "Scene photo" toggle, off to fall back to the CAD layer alone) and keep the frame rate smooth on the laptop with both cameras and SAM running.
- No new reconstruction work and no new dependency. Plain JS modules like the rest of `lookandpick/web/`.
- The simulator workbench on :8766 and the launcher are separate tools; the launcher should open THIS view as the single demo window (tell whoever owns the launcher the URL does not change).

## Tests and acceptance

JavaScript tests with your node runtime next to the existing ones (`tests/workspaces/*.test.mjs`): marker-to-scene mapping of an object and a hand, the held object following the gripper, route polyline, the caption picking the latest glasses / Spark / status entries, and the fallback to the schematic when there is no active workspace or no WebGL. Python tests for the new `arm_base_offset` state field.

Live acceptance with Liam at the machine (scripted Deck is enough, `--model fake`):
- With the active workspace loaded, the Workspace page shows the 3D scene by default and the schematic only via the small switch.
- A pick-and-place shows the arm moving in the scene, the cube riding with the gripper, the plan line, and the gripper tip landing on the cube (this is the check that the base placement is right).
- A hand in front of a camera shows a sphere and red cubes, the arm pauses and the banner says why; STOP shows the person-stop banner; "carry on" clears it.
- A spoken or typed command appears in the caption strip with Spark's answer.
- Saving a take and reloading it plays the same 3D view with no backend.
- `PROGRESS.md` says what was tested for real and what only with fakes.

## Order of work (stop after any item and it still works)

1. The state field and the 3D arm placement from the simulator base (Phase 1), arm moving in the scene (**P0**).
2. Objects, hands, route and banners in the scene; make it the default with the schematic fallback (**P0**).
3. Caption strip (**P1**).
4. `render(frame)` replay path (**P1**).
5. Phase 2 base alignment, only after Liam agrees (**P3**).

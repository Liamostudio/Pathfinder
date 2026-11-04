# Codex master brief: make the 3D workspace look right, and run the demo inside it (4 Nov 2026, evening)

This is the list to work from. The technical detail and the data contract are in `docs/codex-brief-unified-run-view-2026-11-04.md` (read it too: the pan-zero finding, the table-texture finding and the data contract are there). Read `CLAUDE.md` first (safety rules are non-negotiable). Commit only your own files; `git pull --rebase --autostash` before every commit; add a PROGRESS entry saying what was tested and how; tick no milestone; British English.

**The standard:** Liam is filming this view as the hero of the demo. It has to look deliberate and correct, not like a debug tool. Judge every change by a screenshot of the live Deck (`http://127.0.0.1:8774/`, scripted model is enough). Save before/after screenshots under `docs/screens/` and mention them in your PROGRESS entry. Liam reviews by eye, so say what you changed in plain words.

## What Liam sees today (screenshots, 4 Nov)
1. The marker and checkerboard are visibly skewed (not square).
2. The robot's foot is rotated about 42 degrees from the real foot (the real foot is square to the table edge).
3. A floating grey slab above the table (the fitted wall) looks like a mistake.
4. The table's lower-right corner and the area around the robot are smeared.
5. The run is somewhere else: the live arm, cubes, hands and plan are in a flat 2D schematic on a different view.
6. Green corner handles, an unstyled dark background, default camera angle.

## P0: fix what is wrong (do these first, in this order)

### P0.1 Table texture from the marker plane (square, metric)
Use `lookandpick/table_texture.py::rectify(...)` (done, tested, CLI `python -m lookandpick.table_texture photo.jpg out.png --extent X0 X1 Y0 Y1`) to build the table's top texture from the most top-down source photo that shows the marker, and map it onto the table quad in the CAD view instead of the browser's estimated-pose projection. On Liam's top-down photo this gives a black square of exactly 120.0 mm per side with 90 degree corners and 15.4 x 15.7 mm checkerboard squares. You need the table rectangle in the MARKER frame (from the saved `canonical_from_marker` and the fitted `surfaces` table) so the texture's extent lands on it. Heights above the plane are not corrected, so this is for the flat top only; keep the current projection as the fallback if no photo shows the marker. Acceptance: the marker and checkerboard look square in a straight-on screenshot, and unseen table is filled with a plain wood tone, not a smear.

### P0.2 Robot foot and pan, stored in the calibration
`measured_placement` draws the foot at the calibrated pan-zero yaw (138.1 degrees in the marker frame). The physical foot is square to the table edge, 180 degrees in the marker frame. Draw the foot at 180 and add a constant pan draw offset of -41.9 degrees (138.1 minus 180) to the shoulder_pan of everything drawn from calibrated readings. Put BOTH numbers in the calibration data (new fields, not hard-coded in the renderer) and apply them in `measured_placement` / `urdf_joint_radians` so the editor, the live read-only arm and the Run view agree. Liam can only type integer yaw and use 0.01 rad sliders, so a stored value matters. Visual only; not a safety input. Acceptance: after "Use measured robot base frame" the foot is square to the right-hand table edge and the base sits on the real base in the scan photo.

### P0.3 The Run view (one window)
Build the Run view exactly as in the unified brief: the saved workspace in 3D with the simulated arm (from the Deck state's `joints`), objects, hands, voxel cubes, route and stop banners drawn into it, as the default Workspace view, with the 2D schematic only as fallback. Phase 1 base placement (the simulator's own base, labelled) is acceptable tonight; Phase 2 (move the simulator's base to the measured one) only after Liam agrees.

## P1: make it look good (after P0 works)

### Scene
- **Remove the floating wall slab** from the default view: hide the fitted wall planes, or draw them as a very faint backdrop that fades out. Never a solid grey slab.
- **Background:** a deliberate dark neutral with a soft vignette and a subtle floor shadow under the table, not flat black.
- **Table:** the marker-plane texture, a clean edge with a thin lighter bevel, no green editing handles in the Run view (keep them in the editor).
- **Lighting:** one soft key light plus ambient, soft contact shadows under the arm and the cubes; no harsh default shading. Anti-aliased, device-pixel-ratio aware.
- **Default camera:** a three-quarter view from the front-right at about 35 degrees elevation that frames the table and the arm with margin; a "Film view" preset and a slow, smooth orbit button; no jump when the page loads.

### The run, drawn well
- **Arm:** the lime SO-101 with joint easing (exists). A faint motion trail of the gripper tip for the last few seconds.
- **Objects:** crisp small boxes in their real colours with a thin outline and a small label that billboards towards the camera; the target gets a soft ring; the held object rides with the gripper.
- **Hands and people:** translucent spheres with a soft edge, plus the red voxel cubes drawn as softly shaded, low-opacity cubes (not hard saturated blocks); manual no-go cubes as a clean red wireframe; amber unknown cells very faint.
- **Safety bubble:** the stop-distance bubble around the gripper tip as a gradient shell that warms as a hand approaches, with the speed scale as a small gauge.
- **Route:** the planned waypoints as a clean polyline with small nodes, dashed for the part still to travel.
- **Banners and caption strip:** designed for film at 1080p and 4K: one line of large type for the latest spoken command, one for Spark's answer, one for the arm's status ("Pausing: a hand is in the way", "Going around", "Heading back to rest"); person-stop in red, fault-recovery in amber with the countdown. Good contrast, no tiny text, fades out after a few seconds.
- **Typography and layout:** one type scale, consistent spacing, the control column quieter (it is for the operator, not the audience).

### Film mode
A single key (and a button) that hides the operator controls and the session log so only the 3D scene, the caption strip and a small Pathfinder phase indicator remain (Setup, Live, Take n). Must work at 1920x1080 and 3840x2160, and screen-recording must not stutter.

## P2: robustness and performance
- Keep 60 fps on the laptop with both cameras and the SAM worker running (SAM holds about 2.3 GB of the 8 GB card and the browser renders the Gaussian layer too). The photographic Gaussian layer must be optional ("Scene photo" toggle, off falls back to the CAD layer alone), and must default to whatever stays smooth.
- Poll the arm and objects at 10 to 20 Hz, ease between samples; never freeze when a request is slow; a clear "no data" state when Deck stops answering.
- Fallbacks: no active workspace, no WebGL, or a failed texture must degrade to the 2D schematic or the plain wood tone with a clear note, never a blank view.

## P3: replay (design for it now, build after)
Make the Run view render from `render(frame)` so it can play a recorded take from `/api/replay` with no backend. The hosted replay will reuse it. Do not build the hosted page tonight.

## Constraints (non-negotiable)
- Read-only display. Nothing here may start, power or command the real arm; never call `/api/real-arm/connect` or `/disconnect`.
- Do not change safety logic. Do NOT edit `lookandpick/tools.py`, `safety.py`, `runtime.py`, `hand_safety.py`, `hand_gate.py`, `recovery.py`, `zones.py`, `routing.py`, `models/*`, `adapters/*`. Ask Claude for anything you need from them.
- No new reconstruction work. Plain JS modules like the rest of `lookandpick/web/`; no build step, no new dependency.
- Do not change the active workspace or save a version on Liam's behalf. His current draft (robot yaw -6.18 degrees, pan -0.7308 rad, set through the API) is unsaved; v0003 is still active.

## Tests and acceptance
JavaScript tests next to the existing ones (`tests/workspaces/*.test.mjs`): marker-frame to scene mapping, the pan draw offset and foot yaw, the caption picking the latest entries, the fallbacks. Python tests for the stored calibration fields and the texture extent in the marker frame (a synthetic photo through `rectify` into the table quad). Live check with Liam: before/after screenshots for P0.1, P0.2 and the default camera, then a pick-and-place filmed on screen with the caption strip.

## Order of work (stop after any item and it still works)
P0.1, P0.2, P0.3, then P1 scene (wall, background, table, lighting, camera), P1 run drawing, caption strip and film mode, then P2, then P3.

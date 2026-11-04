# Grasp orientation from fused masks (plan, 4 Nov 2026)

Written 4 Nov, the day of the feature freeze. This is a small, isolated increment with a hard cut line (below). Status of each step is
kept at the bottom and in `PROGRESS.md`.

## The gap

"Pick a pen up" finds *which* object (Muse Spark names it, SAM 3.1 masks it), but nothing works out *how to hold it*:

- `Tools.pick(id)` moves above the object's centre, descends and closes the gripper. No orientation anywhere.
- `lookandpick/kinematics.py` is position-only IK. There is no gripper yaw or approach direction.
- A tracked object is an id, a label and a position (`world.TrackedObject`). `SamTracker` takes the mask *centroid* from one camera.
- The sim grasp is an "ideal grasp": anything within 35 mm of the tool when the gripper closes is attached (`simulation/README.md`). A pen
  would "work" in sim whatever its angle, which would be misleading.

Orientation cannot come from the language. Language says which object; geometry says how to hold it.

## Approach: fuse two or three masks into an object shape

No new model. SAM 3.1 already gives a mask per camera; the cameras are already calibrated to the marker frame (`TableCalibration`).

1. **Carve a visual hull.** Around the object's rough position, build a small grid (default 20 cm box, 2 mm cells). Project each cell into
   every camera (with its lens distortion) and keep it only if it lands inside that camera's mask. What survives is a 3D shape the
   object must fit inside. A cell a camera cannot see (outside the frame, behind it) does not vote; a cell needs at least two votes to survive.
2. **Read the geometry from the hull.** PCA on the surviving cells: long axis (in 3D, so a leaning pen is handled), centre, length,
   height, width. Also the yaw of the long axis in the table plane.
3. **Check it makes sense.** Keep the largest connected piece and report how much was discarded; refuse an empty or fragmented hull
   (two cameras looking at different objects produce exactly that, so it doubles as a cross-camera association check).
4. **Plan the grasp.** If the shape is clearly elongated (length/width at least 1.5), the jaws close across the long axis: grasp yaw =
   long axis + 90 degrees, folded to the wrist's range. A compact object (a cube) gets no yaw and is picked exactly as today. Refuse if
   the width is more than the jaw opening, or the height is not plausible.
5. **One camera is the fallback, not the plan.** With a single mask the same module projects the mask onto the table plane at half the
   assumed height and takes the long axis there. Less accurate (no height, perspective-limited) but it keeps "one camera" working.

Why a visual hull is fine for a pen: a thin horizontal rod back-projects, at every height, to a thin strip parallel to the rod, so two
non-parallel views intersect in a thin shape around the rod (the length is limited by the end caps). The width it reports is an upper
bound (inflated by view angle and mask edge), which is the safe direction for a jaw-opening check.

## What it does not do (be honest in the README and video)

- It does not use a learned grasp model. Orientation is geometry from SAM masks, not a language model's guess.
- Not run on real cameras or a real pen yet; the first evidence is synthetic views with known ground truth.
- The sim's grasp is ideal until the contact grasp exists, so sim success will not by itself show that orientation was right. The
  module's tests, and (stretch) a sim pen with a pass/fail alignment check, carry that evidence.
- The real SO-101 has five joints plus the gripper. Wrist roll is a pure yaw only if the tool points straight down; the IK is
  position-only, so the approach pitch at the pen must be checked before any real-arm use. **No real-arm motion from this work without
  Liam present with the 5V kill switch in reach.**

## Where the Meta models fit

| Model | Role here |
| --- | --- |
| SAM 3.1 | The input. Masks of the target from each camera. |
| Muse Spark | Chooses *which* object. Optional cross-check: ask it for the pen's angle from a crop and flag a disagreement of more than ~20 degrees with the geometry. Never the source of the angle. Not built. |
| SAM 3D Objects | Already in the repo as the hosted (fal.ai) option for the workspace Gaussian scan (`workspace_splat/`). One image to 3D; not in the grasp path. Listed as the upgrade path for a single-camera pose. |
| V-JEPA 2 / 2-AC | Not used. Reported zero-shot grasp success on a different arm is 25-65%, and the arm differs. README mention only, labelled not built. |

## Other items: cube, can, ball, small toys (4 Nov, after the pen)

Same module, new shapes. Synthetic views first (`tests/test_object_geometry_items.py`, perfect masks, 6 yaws, 2 and 3 views), then the
same items in the sim from rendered masks (`simulation/tests/test_item_grasp.py`: three cameras, calibration from the rendered marker,
then geometry, then `Tools.pick`; 9 yaws per item). Where the answer was bad, the code or the claim changed rather than the test.

| Item | What it gave | Verdict |
| --- | --- | --- |
| Cube 36 mm (sim) | 9/9 picked, no yaw asked. Reads 42-53 x 40-52 mm (true 36): big, the safe side. | Works |
| Cube 30 / 45 mm (synthetic) | Centre within 8 mm; never reads smaller than the cube; no yaw with 3 views. | Works |
| Can 66 x 100 mm upright | Sim 9/9 picked; reads 68-70 mm across (true 66), height 128 (true 100, loose). Synthetic 2 views read 85 x 77 mm. A 100 mm can is taller than the default 80 mm search box, so `height_clipped` is set unless `max_height_m` is raised. | Works; height is only an upper bound |
| Can on its side | 3 views: yaw within 12 degrees at all 6 poses. 2 views: yaw up to ~15 degrees out and centre up to 19 mm off. It would also roll. | 3 views only |
| Ball 40 mm | Centre fine, no yaw with 3 views (2 views can read it 1.5:1, a harmless spurious yaw). **The base layers read it 25-30 mm wide**, because a ball is widest above the table. | Fixed for the jaw check by a silhouette floor (see below) |
| Toy car (box body + cabin) | 3 views: jaws across the body at all 6 poses, within 12 degrees. | Works |
| Toy duck (ellipsoid + head) | Sim, 9 yaws: **7 picked, 2 missed** (3 views; 2 views the same, with yaw errors up to 24 degrees). The two misses are poses where it reads as compact (elongation 1.2-1.4 against a 1.5 threshold), so no yaw is given and the sim correctly misses. A yaw that is given is always within 17 degrees. | Partly works: 78% |
| L-shaped toy | The hull fills the notch (reads 115 x 73 mm for an 80 x 80 mm L) and the centre lands ~10 mm off the material at every pose. `centre_on_object` refuses only 4 of 12 poses. | **Not handled.** Needs depth or a contact check |

What changed because of this:

- **Silhouette-width floor.** Round-bottomed items (ball, duck) are widest above the table, so the base-layer measurement under-reads them
  by up to ~40%, the unsafe direction for a jaw check. `ObjectGeometry.silhouette_width_m` (the widest camera silhouette, in metres, largest
  piece of the mask only so a stray speck cannot stretch it) is now a floor for compact objects in `plan_grasp`.
- **`centre_on_object`** (refuse when the measured centre has no object under it) and **`height_clipped`** (taller than the search box).
- **Not changed, on purpose:** the elongation threshold stays 1.5. Lowering it would give the duck a yaw more often, but cubes and balls
  would get an unneeded wrist turn, and the cube pick is the hero. `plan_grasp(min_elongation=...)` is there if a toy demo wants it.
- A sim lesson: a toy with a heavy head hopped across the table; the head is now light (3 g of 15 g). Sim camera `top` looks almost
  straight down and the resting arm hides part of the marker, so the item tests aim it from the side (azimuth -135).

Two honest limits: the sim items are rigid solids with ideal grasps (the pass/fail rule is ours), and none of this has met a real can,
toy or camera.

## Steps, acceptance tests and cut line

1. **`lookandpick/object_geometry.py`** (pure numpy + OpenCV projection). Accept: on synthetic pens with known pose rendered through
   two calibrated cameras 90 degrees apart, recovered long-axis angle within 3 degrees over a sweep of 0-180 degrees, centre within 5 mm;
   three views no worse; one view falls back to the footprint; a cube returns no yaw; a wide object is refused for the jaw; masks of
   different objects in two cameras are refused; speckle and 1 px edge growth degrade gracefully. *Tests in `tests/test_object_geometry.py`.*
2. **Plumbing.** Optional `yaw` and `size` on the tracked object; `Tools.pick` passes the yaw to arms that accept it and ignores it for
   those that do not; cubes and every existing test unchanged.
3. **Sim check (stretch).** A pen prop in the sim and a grasp that fails when the jaws are within 30 degrees of its long axis, so a
   wrong orientation visibly fails and a right one visibly succeeds.
4. **Live wiring (stretch).** `SamTracker` collecting each camera's mask and calling step 1; needs a second live camera.

**Cut line:** if step 1 is not passing by the evening, stop; the cube pick remains the hero and this stays documented as designed and
partly built. Never cut: hand safety, one camera, one clean pick. Nothing here touches the safety path.

## Status

- **Step 1 done (synthetic only):** `lookandpick/object_geometry.py`, `tests/test_object_geometry.py` (25 tests). Measured over 54 poses
  per case (3 table positions x 18 yaws, pen 140x10x10 mm, cube 30 mm), perfect masks and calibration:

  | | angle error (max) | centre error (max) | length | width (true 10) | height (true 10) |
  | --- | --- | --- | --- | --- | --- |
  | pen, 2 views | 0.2 deg | 2.1 mm | 142-152 mm | 12-27 mm | 14-36 mm |
  | pen, 3 views | 0.3 deg | 1.4 mm | 142-149 mm | 12-22 mm | 14-24 mm |
  | cube 30 mm, 2 / 3 views | (compact, no yaw) | 3.8 / 1.7 mm | 41-55 / 39-48 mm | 37-47 / 35-45 mm | 46-52 / 44-46 mm |

  Sizes are upper bounds (a silhouette hull over-reads, most with two views at diagonal yaws), so the jaw-opening check errs towards refusing.
  Finding that shaped the code: using the whole hull, a diagonal pen read 4.6 degrees off and 71 mm wide (a ghost sheet leans away from both
  cameras); the lowest layers sit on the table where there is no parallax, so the planar measurement uses only those. Also tested:
  single-view fallback (within 8 degrees), strong lens distortion, 1 px of mask growth plus 1% dropped pixels, ~1.6 px aim error, different
  objects in the two cameras (refused), object off the search box (refused), too wide for the jaws (refused). Speed 0.3-0.45 s per estimate.
- **Step 2 done:** `TrackedObject.grasp_yaw_deg` / `size_m` (omitted from the scene dict until measured), `Tools.pick` calls
  `arm.set_grasp_yaw(...)` when the arm has it (always, so a stale yaw never carries over), `oriented_object(obj, views, jaw_opening_m=)`
  to apply it. The fake arm records it; the sim and the real adapter do not take it yet. `tests/test_grasp_yaw_plumbing.py` (6 tests).
- **Step 3 done (the sim rule is ours, not physics):** `Simulation(pen=True)` adds a flat pen (`obj3`, 140x10x10 mm), `place_pen(x, y, yaw)`
  and `set_grasp_yaw`; a close on the pen misses (grip refused, reason given) unless the commanded yaw puts the jaws within 30 degrees of
  across its long axis, and a pen with no yaw at all misses too. The wrist is not rotated and the grasp is still the ideal attachment, so
  this checks that the *decision* is right, not that a real gripper would hold it. Off by default: the default scene is still two cubes.
  The yaw also travels over the HTTP bridge (`grasp_yaw` action).
  **End to end from sim-rendered masks** (`simulation/tests/test_pen_grasp.py`): the sim renders the pen from `camera_a` and `camera_b` at
  1280x720, the calibration comes from the rendered marker, the masks from the sim's segmentation, `oriented_object` gives the yaw and
  position, and `Tools.pick` picks. Pen yaws 0, 35, 70, 110, 150: grasp yaw within 5 degrees of across the pen, position within 10 mm,
  pick succeeds (8 repeat runs of the whole file, 17 tests each, all pass). Also refused: a camera whose mask is empty. Wrong, missing and
  borderline yaws are covered both ways. Lesson from debugging: the sim camera thread stamps a frame after rendering but copies the scene
  before, so a test (or a live tracker) must wait for a frame from a loop that began after the scene changed, or it fuses stale masks;
  the geometry refused those stale pairs ("the cameras disagree") instead of returning a wrong yaw.
- **Other items done:** see "Other items" above (`simulation/tests/test_item_grasp.py`, `tests/test_object_geometry_items.py`).
- **Not done:** step 4 (live wiring into `SamTracker`, needs a second live camera), real cameras, a real pen, jaw opening, wrist-roll and
  approach-pitch mapping on the real arm, a contact-based sim grasp.

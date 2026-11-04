# Unified Run view — handover, 4 November 2026

Implemented from the master and unified Run view briefs. The live Deck is at
http://127.0.0.1:8774/. Its existing Spark configuration was preserved when Liam
approved a restart. No camera, serial connection, real-arm command or paid model
request was used for these checks.

## What changed

- **Table:** the tested `table_texture.rectify` generates a metric marker-plane
  PNG from the largest, least foreshortened source marker. Table corners are
  transformed into marker metres; per-corner UVs retain their proper registration
  when the fitted table is rotated. Existing SAM masks remove raised objects.
  The table mask's convex hull retains printed marker/checkerboard holes. Unseen
  or occluded pixels use plain wood, without inpainting or stretched edges.
- **Foot:** `calibration/so101-touch-2026-11-03.json` now stores
  `visual_alignment.foot_yaw_marker_deg = 180` and
  `visual_alignment.pan_draw_offset_deg = -41.871965619071574`.
  Measured placement uses the foot frame; read-only real-arm readings receive
  the pan correction through `urdf_joint_radians`. The existing physical
  `base_frame`, joint calibration offsets and safety calculations are unchanged.
- **Run:** the saved 3D scene is the default. Named simulator joints, its reported
  `arm_base_offset`, objects, held objects, hands, route and captions share the
  marker-to-scene transform. Simulator radians receive **no** real-arm offset.
  Missing pose/base/anchor data is labelled rather than inferred.
- **Presentation:** no fitted wall slab or edit handles in Run; dark vignette,
  wood edge, soft key/ambient lighting and approximate contact shadows; three-
  quarter Film view and slow orbit; lime arm easing, a short TCP trail, outlined
  boxes with billboard labels/target rings, soft hand spheres, a warming stop
  bubble, very faint unknown voxels, shaded intrusions and wireframe manual blocks.
  The remaining route is dashed with waypoint nodes.
- **Captions:** latest transcript, model answer, Arm/Safety status and allowed
  speed. Voice text comes from the transcript output. Pending speech confirmation
  is explicit; old answers/status lines are not presented as new instructions.
  Person stops are red; fault stops are amber and show the runtime's countdown.
- **Filming:** Film/F hides operator panels and the timeline; Escape restores
  them. Captions and the small phase indicator scale for 1080p and 4K.
- **Existing demo:** Play demo now feeds the same scene renderer with the existing
  deterministic, scripted MuJoCo recording. It is labelled **Take 1 / simulation**,
  including its recorded STOP. Return to live restores the live display; control
  actions also exit playback. Live sensor occupancy is not mixed into the take.
  The old schematic player remains the fallback for older recording formats.
- **Performance/fallbacks:** visible state polling targets about 12 Hz, with no
  overlapping state requests, easing and a stale-pose indication. CAD is default;
  the Gaussian layer loads only on Scene photo and is not sorted/drawn when off.
  Setup's second renderer loads only when opened. Asset timeouts, texture failures,
  missing workspaces and lost graphics contexts have labelled fallbacks.

## Operations and data

- Workspace → **Film view**, **Orbit**, **Scene photo**, **Film · F**.
- **Voxel editor** restores editing controls; **Return to Run** leaves them.
- Setup → **Table top** gives a straight-on inspection. Editing the table tells
  the user to rebuild, re-anchor the marker, then apply the measured base frame.
- Derived appearance uses GET `.../table-texture.json` and `.../table-texture.png`
  on draft or version roots. A managed `visual-cache/` sidecar is keyed by source,
  geometry and marker registration. It does not rewrite workspace manifests.
- `WorkspaceRenderer.render(frame)` / `renderFrame(frame)` consumes the same
  display contract for live and recorded data. Runtime recordings and seeded
  demo frames now include base offset, frame label and stop distance.
- The separately hosted replay page and legacy export bundle were not rebuilt;
  the new table appearance remains a derived local cache. No dependency/build
  step, reconstruction run, physical-base move or protected safety-file edit.

## Validation and evidence

- Required Python suites: **642 passed, 1 skipped** in core/workspace;
  **98 passed, 5 skipped** in MuJoCo. Bundled Node: **12 test files pass**,
  including 11 focused Run scene tests. Follow-up affected Python tests pass;
  the seeded demo matches across three runs after its frame metadata changes.
- Python checks cover synthetic square-marker rectification into a rotated quad,
  printed-marker retention and raised-object masking, missing-marker fallback,
  read-only cache/HTTP boundaries, and the exact stored foot/pan relationship.
- Browser: default live scene, private-copy placement action, photo on/off, orbit,
  scripted pick/place (including a grip retry), a synthetic hand/person STOP,
  labelled take playback/return, and 1920×1080 / 3840×2160 Film layouts. The final
  live browser check reported no console errors. No save or activation on Liam's
  behalf; v0003 remains active. Concurrent draft edits were left alone.

Screenshots in `docs/screens/`:

- [Before: schematic](screens/run-before-2026-11-04.png) and
  [old saved scene](screens/workspace-before-2026-11-04.png).
- [Straight-on texture/foot QA](screens/tabletop-marker-plane-qa-2026-11-04.png):
  private workspace copy, no save/activation of the original.
- [Live operator view](screens/run-after-live-deck-2026-11-04.png),
  [1080p Film](screens/run-after-live-1080p-2026-11-04.png),
  [4K Film](screens/run-after-live-4k-2026-11-04.png).
- [Recorded take at 1080p](screens/run-take-live-1080p-2026-11-04.png),
  [4K](screens/run-take-live-4k-2026-11-04.png), and
  [private live person-stop QA](screens/run-person-stop-qa-2026-11-04.png).

## Still requiring acceptance

Liam's visual approval and the two-camera + SAM **60 fps hardware load test**
remain outstanding; no frame-rate guarantee is claimed. Photo/CAD occlusion is a
visual overlay, contact shadows are approximate, RGB occupancy and reconstruction
retain their existing limits. GPU context loss was not forced on the live Deck.
The simulator stays at its own base (**Phase 1**); moving it to the measured
physical base remains **Phase 2, only after Liam agrees**. No milestone ticked.

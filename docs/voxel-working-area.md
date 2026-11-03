# Voxel working areas — people and hands, RGB preview

In Pathfinder on http://127.0.0.1:8765, open **set up workspace**, select a
reconstructed workspace, and find **Working area / voxel volume** beneath the
CAD inspector.

1. Click **Draw tabletop footprint**. The view scrolls to the table; drag between
   two points on its surface. The rectangle follows the table's axes and starts
   at tabletop height.
2. Pull the lime top handle upwards to extrude the volume. Height and cube size
   can also be entered numerically. Grid fidelity offers Coarse, Balanced, Fine and Custom settings. The selected volume
   stays fixed; boundary cells are clipped to its edges. The limit is
   4,096 cubes; increase cube size for larger volumes.
3. Under **Anchor to ArUco marker**, choose an original photo, marker ID (default
   0, DICT_4X4_50) and printed side length. The default 120 mm is nominal; measure
   the black square (excluding its white border/panel) before ticking the measured-size box. The detector never uses a
   reconstructed marker texture. Optional lens intrinsics must match that source
   image's resolution. Model-estimated intrinsics yield a visual alignment only.
4. Use **Simulate an intruder** to check cube rendering and transitions. This is
   explicitly labelled simulation and includes simulated clear-space evidence.
   Stop/reset returns cells to unknown. Simulation cannot mix with a live source.
5. Save a workspace version and activate it. Geometry, cube settings and marker
   alignment persist. Occupancy is transient: reopening starts unknown. Draft
   observations also appear in the active version only when its area and marker
   calibration exactly match the draft; editing either invalidates old evidence.

## Cube interaction and fidelity

Cubes are faint and separated at rest. Nearby cubes grow smoothly towards full
size as the pointer approaches, then settle back slightly. Pulling the volume's
height handle or dragging the camera upwards briefly reveals all cubes. Reduced-motion preferences disable the
spring animation.

Click a cube vertex, then click an X, Y or Z edge to select that full voxel row.
Right-click returns from the selected row to axis choice; right-click again
returns to vertex selection. Row buttons block or unblock the selected row.
Double-click directly toggles the individual voxel under the pointer.

**Edit layout** uses green for manually unblocked cells and red for blocked cells.
These editing colours do not certify live clearance. A live occupied cell remains
red even after its manual block is removed. **Live status** uses the sensor states
below, with manual restrictions always taking precedence.

Manual restrictions are durable operational policy, stored separately from live
sensor evidence and immutable scene assets. The active scene and draft share the
policy when their geometry and canonical frame match. Saving a version also
records a baseline policy for export. Sensor reset and restart do not erase manual
restrictions. Edits use policy revisions to reject conflicting updates.

Fidelity presets target about 256, 1,024 or up to 4,096 cells. The exact cube size
and resulting count are shown. Custom cube size remains available. Changing
resolution preserves the volume boundary and conservatively maps blocked regions
into the new cells; it may mark extra edge cells to avoid dropping a restriction.

## Live status colours

- **Red:** a detected person/hand may intersect this cube. RGB bounding regions
  conservatively mark the intersected depth range, rather than guessing height.
- **Amber:** unknown, unobserved, stale or occluded; unsafe for a voxel path check.
- **Green:** explicit fresh clear-space evidence. Currently supplied by the labelled
  simulation or a calibrated 3D adapter. A blank RGB detection result does not
  prove empty 3D space and never makes a cube green.

Only **people and hands** are enabled. Other detected classes do not mark cubes
occupied. This follows the chosen human-intrusion policy; it is not general object
collision avoidance. The 2D people/hand detectors can miss detections. RGB ambiguity,
occlusion and reconstruction/alignment errors remain limitations.

The overlay uses the existing CAD view and shared camera; it is not composited
into the photographic splat view. The marker's RGB axis triad helps inspect the
visual registration. Robot-base alignment remains independently unverified.

## Live or recorded RGB input

Expand **Live people + hands / RGB preview**. Connect a fixed webcam or open a
browser-compatible video (replays are explicitly labelled). **Play / restart replay**
restarts a clip after detector warm-up. Enter that camera's calibrated intrinsics at the
captured resolution (the preview samples at a maximum edge of 640 pixels):

```json
{"camera_matrix":[[600,0,320],[0,600,240],[0,0,1]],"dist":[0,0,0,0,0]}
```

These are format examples, not calibration values for your camera. Use the
existing lens-calibration tools. Camera matrix rows scale with image resizing;
distortion coefficients do not. Supported OpenCV distortion lengths are 0/4/5/8.
The workspace and live camera must observe the same physical marker. Select
**Register marker + start detection** with the marker visible. Movement of the
fixed camera requires re-registration. Stop releases browser camera tracks.

The existing Pathfinder Vision detector implementations are reused in a separate
persistent CPU process: MediaPipe Hand Landmarker and EfficientDet-Lite0 with
`person` allowlisting. Default runtime: `~/src/hands-venv/bin/python`; override
`PATHFINDER_PEOPLE_PYTHON`. Existing cached weights are read from
`weights/hand_landmarker.task` and `weights/efficientdet_lite0.tflite`. Nothing is
downloaded. The detector has no camera/motor/network access; the browser sends
bounded JPEG frames locally. Missing models/runtime and warm-up failures are
shown in the UI. Stopped, paused, slow or failed feeds become stale.

The shared main-server process remains free of model imports. The CPU detector
exits with its owning server; source camera registration and observations are
invalid after a server restart. Source calibration and working-area geometry
remain saved. Registration frames and private detector logs are git-ignored.

## Local integration contract

All routes use the main app's loopback Host/Origin checks.

- `PATCH /api/workspaces/{id}/draft` with `working_area` edits composition only.
- `POST /api/workspaces/{id}/marker-alignment` detects an original-image marker.
- `GET /api/workspaces/{id}/voxels` returns a runtime revision, cube states/counts,
  freshness and source status. Index order is `((z * ny) + y) * nx + x`.
- `POST .../voxel-camera` registers a bounded JPEG with explicit camera intrinsics.
- `POST .../voxel-detect` runs the local people/hand models and projects detections.
- `POST .../voxel-rgb` accepts labelled adapter detections (`hand` or `person` and
  normalised `box: [left, top, right, bottom]`), with a registered camera ID.
- `POST .../voxel-observations` accepts `points` in `marker_metres` with `kind`
  `hand`/`person`, or an explicitly labelled `simulation`. A 3D adapter can supply
  `free` cube IDs only with measured-marker and calibrated-intrinsics inputs.
- `POST .../voxel-edits` accepts `revision`, `policy_revision`, `indices` and a
  boolean `blocked`. Unblocking removes only manual restrictions, without
  creating or clearing sensor evidence.
- `POST .../voxel-reset` revokes in-flight observation revisions and returns unknown.
- `POST .../voxel-path` checks `start`, `end` and swept-sphere `radius` with
  explicit `frame: "canonical"`, or `frame: "marker_metres"` plus matching
  `calibration_id`. Outside-volume, occupied and unknown cells reject the path.
  This endpoint never authorises physical motion.

Live observations need `revision`, matching `calibration_id`, `source`, increasing
`sequence`, and same-host epoch `captured_at_ms`. Delayed/out-of-order observations
are rejected. Source age is bounded by `ttl_ms` (default 500 ms); occupied cubes
have a configurable hold (default 750 ms). Fresh occupancy from any camera wins
over another source's clear evidence. Browser display freshness also expires.
Area changes, marker changes, resets and server restarts revoke old revisions.
Saved versions support `/versions/{version}/voxels`, `/voxel-camera`,
`/voxel-detect`, `/voxel-rgb`, `/voxel-observations`, `/voxel-path`, `/voxel-edits` and `/voxel-reset`.

This is a visual/development monitor and a tested voxel path-check API. The main
simulated pick/place controls continue using their separate synthetic scene.
Physical use still requires measured marker-to-robot alignment, validated sensor
coverage and latency, full-arm swept-volume collision checks and independent
local stopping/watchdog enforcement. No physical motion is enabled by this feature.

## Validation

Automated tests cover rotated grids, exact boundary contacts, stable cube snapping,
voxel limits, conservative swept-radius checks, stale/unknown states, multiple-camera
occupied precedence, class filtering, RGB depth projection, old revisions, reset,
immutable versions and original-image marker-to-map transforms. JavaScript tests
cover footprint projection, extrusion, cube topology and display freshness.

Browser checks exercise draw, height dragging, marker detection, simulation,
save/activation and reopen, including a 990-cell example saved as v0003.
The regression suite passes 350 Python cases and four JavaScript test files. Real CPU model smoke tests used the supplied workspace
photo: warm inference approximately 43–76 ms, no person/hand detected. That is not
a real-person detection accuracy or physical safety test. The existing source
photos detect marker 0, but their estimated intrinsics and nominal 120 mm marker
size do not establish calibrated physical accuracy.

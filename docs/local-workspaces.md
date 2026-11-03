# Local workspace setup in Pathfinder

Run the existing main app from the repository root:

```sh
.venv/bin/python -m lookandpick.mission_control
```

Open http://127.0.0.1:8765 and choose **set up workspace**. This flow uses the
main app's navigation and Deck styling. It makes visual tabletop workspaces;
the existing simulated pick/place controls still use their synthetic world state.

## First workspace

1. Choose **Load completed experiment** to inspect the existing four-photo
   result without inference, or create a workspace and add photos/video.
2. Set the video sample rate and frame budget, then **Import and choose views**.
   Originals remain on disk. The video sampling budget does not truncate photos.
   Select 2–4 overlapping, sharp views for joint reconstruction. More views have
   not been profiled on this 8 GB GPU. Keep the scene static and lighting consistent.
3. Choose Quick (1,000 steps) or Balanced (8,000 steps), then reconstruct.
   MapAnything, SAM 3.1, planar surfaces, gsplat fitting and preview export run
   sequentially in isolated processes. The interface polls persistent status.
4. Inspect the CAD or photographic view, using source cameras, orbit, pan,
   zoom and reset. CAD has depth-tested surfaces, optional observed samples and
   a separate lime SO-101 URDF hierarchy. Photographic inspection shows splats.
   Both views use the same cameras and explicit coordinate frame. They are
   separate inspection views: mixed mesh/splat occlusion is not implemented.
5. Adjust the table, walls, robot placement and illustrative joint angles.
   Drag table corners to resize its constrained rectangle. Table edits require
   **Rebuild edited surfaces** before saving. Robot/layer/scale edits are instant
   composition changes. Mask overlays show per-photo semantic masks; draw a
   polygon and apply it to replace a category's mask for that source image.
6. Keep relative scale, or enter a measured table width. A measurement establishes
   scale only; robot-base alignment remains approximate. No marker texture from
   the reconstruction is used as a physical calibration observation.
7. Save an immutable version, then activate that version in the main workspace.
   Draft changes and failed/cancelled rebuilds leave the active version intact.
   Reopening the app loads saved assets without running the models.

Original sources, selected frame hashes/timestamps, preprocessing transforms,
model revisions, prompts, edits, surface assumptions and settings are retained.
Export SPLAT, PLY, GLB, camera transforms, videos or the version's workspace ZIP.
The ZIP includes registered visual assets and the robot URDF/STLs; original
private source files remain in the workspace's managed source directory.

## Voxel working area

The CAD editor also supports drawing a tabletop footprint and dragging it into a
voxel volume, anchored to an original-image ArUco observation. See [voxel working areas](voxel-working-area.md) for people/hand RGB monitoring, simulated intrusion,
freshness rules and the remaining physical-calibration requirements.

## Runtimes and storage

The standard-library HTTP process imports no CUDA models. Runtime overrides:

| Setting | Default interpreter |
| --- | --- |
| `PATHFINDER_GEOMETRY_PYTHON` | `gaussian_studio/.depth-venv/bin/python` |
| `PATHFINDER_SAM_PYTHON` | `~/src/sam3-venv/bin/python` |
| `PATHFINDER_GAUSSIAN_PYTHON` | `workspace_splat/.venv/bin/python` |

The geometry environment also performs CPU plane fitting and GLB export
(`trimesh`). The existing requirements files under `gaussian_studio/` describe
these environments; they must stay separate. The runtime panel checks paths,
pinned weights/source caches, the DINO Torch Hub cache and CUDA availability.
Setup is offline and does not download missing weights or require API keys.

Pinned MapAnything weights: `00f9c245bbcb60522d1ed7f9e9d88462c6e3f38a`;
source: `3d10cf7a3016fc0f9bb13a071ee66c47b10be0d9`.
SAM 3.1: `daa63191845a41281374e725f4c9e51c7a824460`.
SAM 3D Objects, VGGT and HyDen are not dependencies of this flow.

Storage defaults to git-ignored `recordings/workspaces/`; override with
`--workspace-data`. Each workspace owns sources, immutable stage attempts,
private logs, atomic job status and saved version manifests. Public asset IDs
resolve only to registered files in that workspace. Videos support byte ranges.
Uploads stream in bounded chunks (2 GiB/file, 4 GiB/workspace, 200 sources).
The shared `recordings/.gpu.lock` lease also covers SAM Prompt's inference.

Each worker has a recorded PID, Linux process-start identity and owned process
group. On server restart a surviving worker is reattached; a dead/interrupted
worker becomes explicitly failed. Cancellation signals only that verified group,
waits for all its processes and releases the lease. Failed stages can be retried;
validated completed stages are reused. Incomplete/corrupt archives are rejected.

Dependency rules:

| Change | Recomputed |
| --- | --- |
| Sources/sampling/selected views | Affected ingestion and downstream inference |
| Segmentation prompts or polygons | Masks, surfaces, appearance and previews |
| Table/wall parameters or texture reference | Surfaces, appearance and previews |
| Appearance steps | Appearance and previews |
| Preview orbit/path options | Previews |
| Robot placement/joints, layers, measured scale | Composition only |

Known failures show actionable messages: missing cache/runtime, GPU OOM,
insufficient surface support or interrupted setup. Review masks and overlapping
views when plane support is poor; use fewer views/lower resolution for OOM.
Raw upstream output remains in private per-attempt logs.

## Frames and rendering limits

Table-aligned application coordinates use Z up. The manifest records
`canonical_from_map`, scaled `workspace_from_map`, Gaussian normalisation and
`workspace_from_robot_base`. Source camera coordinates are OpenCV (+X right,
+Y down, +Z forward), with world-to-camera extrinsics. The camera JSON retains
full-image intrinsics and pixel preprocessing transforms:
`K_model = image_transform @ K_full`.

Gaussian composition applies
`p_workspace = T_workspace_from_map * (radius * p_gaussian + centre)`
to centres, with corresponding size and quaternion changes. Saved version edits
do not alter live ArUco calibration or command physical motion.

The native photographic viewer uses degree-zero colour from SPLAT. Trained PLY
and CUDA preview videos retain degree-one spherical harmonics, so appearance can
differ. The planar proxies fill some unseen regions, are thin surfaces and are
not validated solid collision models. Observed objects can contain duplicated or
missing geometry. The robot pose is illustrative. Training-view or masked PSNR
is not held-out quality or measured geometric accuracy.

## Validation

```sh
workspace_splat/.venv/bin/python -m pytest gaussian_studio/tests workspace_splat/tests tests -q
node --test tests/workspaces/math.test.mjs workspace_splat/tests/viewer.test.mjs
```

On 2 November: 300 Python tests and both JavaScript test files passed. Tests use
fake workers for lifecycle cases and real CPU media ingestion. Geometry tests
cover camera inversion/crop transforms, Gaussian rotation/scale, constrained
rectangle edits, masks, cache dependencies and persistence. A separate real
local CUDA run imported the four example photos through the main GUI, ran
MapAnything and SAM 3.1, fitted the background for 8,000 steps (68 seconds for
fitting), exported videos, and reached Ready. A table edit reused geometry and
masks and completed its 1,000-step downstream rebuild in 24 seconds. Save,
activation, reopen and robot composition-only changes were exercised in the GUI.
A sampled-video GUI run (1 fps, four-frame budget) also reached Ready in 59 seconds;
this used an MP4 made from the same four photos, not an independent scene.
A real preview worker survived a server restart, cancelled in 0.1 seconds and
released the GPU lease while preserving the active saved version. Timings apply
to this capture and machine, not arbitrary captures. Additional real tabletop
scenes, larger view sets and physical calibration remain unvalidated.

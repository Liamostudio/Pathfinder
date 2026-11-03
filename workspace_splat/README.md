# Pathfinder workspace Gaussian pipeline

Local photographs, video files or existing camera `FrameSource` adapters → CPU COLMAP camera/structure reconstruction → CUDA-trained anisotropic 3D Gaussians → `.splat` / 3DGS `.ply` → browser GUI.

This is an actual multi-view fitted Gaussian scene, not a depth-extruded photograph or a point-cloud file labelled as a splat. Camera recovery failures stop the pipeline rather than substituting invented camera poses.

## Start the GUI

From the repository root:

```sh
workspace_splat/.venv/bin/python workspace_splat/start.py
```

Open **http://127.0.0.1:8790/**. The launcher starts a detached process, performs an HTTP health check and reuses a running service. Logs are in `workspace_splat/server.log`.

The supplied four photos have already been processed into the **workspace-example** capture. Choose it in the library, orbit with a drag, zoom with the wheel, pan with Shift-drag and select the original camera views. Training previews and export links appear below the viewer.

For another scan, choose photographs or a video and click **Build Gaussian workspace**. The GUI uploads only to the local service. It reports reconstruction/training stages, permits cancellation, and publishes a scene only after fitting and export succeed. The GUI accepts files up to 256 MiB each, 2 GiB total per capture; use the CLI for larger videos. One training job owns the workspace trainer's GPU lock at a time. Cancellation terminates the worker; it does not publish an unfinished scan. Detached workers can be cancelled after a server restart, using verified process identity rather than trusting a saved PID. An active detached job also prevents a second GUI job from starting.

The simulation GUI now includes an expandable **Workspace scan · Gaussian splats** panel beneath its telemetry. Start both services and open the panel. The latest trained scan loads into the embedded viewer. Scans are photographic appearance layers; the simulated arm still uses its existing MuJoCo scene.

## Installation

The optional runtime is already installed in `workspace_splat/.venv`; core project dependencies were not changed. Verified here: Python 3.14.4, PyTorch 2.14.1+cu130, pycolmap 4.2.1, gsplat 1.5.3, RTX 5070 Laptop (sm_120), local CUDA 13.0 compiler.

Recreate it on this Ubuntu machine with:

```sh
python3 workspace_splat/bootstrap.py --cuda-compiler --local-python-headers
```

The bootstrap reuses the repository `.venv`'s PyTorch through a `.pth` file when present, installs the isolated requirements, and optionally installs CUDA compiler wheels. The Ubuntu/Debian header option downloads and unpacks the matching development package into `.cache`; it does not install system packages or require sudo. If the project environment has no PyTorch, the dependency resolver installs it; verify that it supports your NVIDIA GPU before training.

`backend.py` merges pip's split CUDA components, supplies local Python headers, creates temporary library aliases to accommodate the space in `Meta Hackathon`, and adapts gsplat's extension call to PyTorch 2.14's added SYCL argument. It does not modify PyTorch/gsplat source. The first CUDA extension compilation can take several minutes. Compiled kernels are cached under `/tmp/pathfinder-splat-extensions`; a reboot or temporary-file cleanup may require rebuilding them.

COLMAP uses CPU feature extraction/matching and eight threads. Gaussian fitting requires CUDA and fails clearly when it is unavailable. Within the Codex filesystem/network sandbox the GPU may be hidden: run GPU jobs in a normal local terminal. The local backend uses no model downloads, paid API, cloud reconstruction or serial hardware access. The optional hosted backend is described below.

## CLI

A complete run, accepting a folder or a mixture of image/video paths:

```sh
workspace_splat/.venv/bin/python -m workspace_splat run /path/to/photos /path/to/clip.mp4 \
  --output workspace_splat/data/my-workspace \
  --sample-fps 2 --max-frames 160 --max-edge 1280 \
  --steps 2500 --train-edge 640 --max-gaussians 40000
```

Output must be new or empty for ingest. Individual stages can be run and retried independently:

```sh
workspace_splat/.venv/bin/python -m workspace_splat ingest /path/to/video.mov \
  --output workspace_splat/data/my-workspace --sample-fps 2
workspace_splat/.venv/bin/python -m workspace_splat reconstruct workspace_splat/data/my-workspace
workspace_splat/.venv/bin/python -m workspace_splat train workspace_splat/data/my-workspace --steps 2500
```

Training retries start from the saved sparse reconstruction; `checkpoint.npz` is an export of final optimiser parameters, not a resumable optimiser-state checkpoint. A completed GUI capture is immutable; create a new capture to rebuild it.

Accepted stills: JPG, PNG, WebP, TIFF and BMP. Videos are decoded by OpenCV, including MP4/MOV when the installed codec supports them. EXIF orientation is applied, metadata is stripped from normalised JPEGs, exact duplicates are removed, dimensions/sharpness are reported, and video sampling is capped. Existing Recorder timestamp sidecars (`clip.json`) are read alongside video files. Features/poses are estimated from the normalised frames, and training uses COLMAP's undistorted images/intrinsics.

For a source already implementing the project's BGR `FrameSource` protocol:

```python
from workspace_splat.ingest import ingest_frames
from lookandpick.adapters.camera import ReplayCamera
from pathlib import Path

source = ReplayCamera(Path('recordings/walk-around.mp4'))
ingest_frames(source, Path('workspace_splat/data/recorded-workspace'), max_frames=120)
```

Move the camera through different viewpoints while recording. A stationary webcam stream cannot reconstruct unseen surfaces merely by supplying more identical views. `ingest_frames` preserves strictly increasing timestamps and BGR colour conventions and closes the iterator when done.

## Data/products

Captures are private and git-ignored under `workspace_splat/data/`. Input room photographs, source clips, COLMAP databases, sparse models, logs and trained results are not automatically added to the public repository.

Each capture has:

- `capture.json`, `images/`: normalised inputs and source/timestamp/sharpness metadata.
- `colmap.db`, `reconstruction/`, `undistorted/`: feature matches, calibrated poses and sparse points.
- `initial.npz`, `reconstruction.json`: filtered seed geometry and camera/reprojection report.
- `status.json`, `worker.log` (GUI jobs): progress or a clear failure reason.
- `output/scene.splat`: 32-byte records: float32 XYZ, float32 XYZ standard deviations, uint8 RGBA, uint8 quaternion **w,x,y,z**.
- `output/scene.ply`: binary little-endian 3DGS PLY with SH degree 0 colours, opacity logits, log scales and normalised quaternions.
- `output/scene.json`: camera poses/K, bounds, normalisation, fit history, budgets, warnings and training-view metrics.
- `output/gaussians.npz`: readable arrays for means, scales, quaternions, RGB and opacity.
- `output/previews/`: actual fitted renders at the recovered cameras.

The trainer optimises positions, anisotropic scales, rotations, opacity and degree-0 colour with differentiable gsplat rasterisation, L1/SSIM loss and split/clone/prune densification. The configured Gaussian cap bounds growth. The browser projects anisotropic covariance, sorts back-to-front and alpha-composites Gaussian billboards using local WebGL 2 code; it has no CDN/runtime dependency.

## GUI integration

The standalone viewer is intentionally embeddable:

```html
<iframe src="http://127.0.0.1:8790/viewer?scene=workspace-example"
        title="Workspace Gaussian scan"></iframe>
```

Omit `?scene=` to load the newest trained capture. The service permits framing by the local simulation (8766) and Deck (8765) origins. A parent page must permit `frame-src http://127.0.0.1:8790` in its CSP; the simulation server has that specific allowance. Deck's backend/renderer has not been changed.

To integrate directly into another page served by the same scan service:

```javascript
import { GaussianViewer } from './viewer.js';
const viewer = new GaussianViewer(document.querySelector('canvas'));
await viewer.load('/scenes/workspace-example');
viewer.capturedView(3);
```

API: `GET /api/captures`, `GET /api/captures/<id>`, `POST /api/captures {}`, `POST /api/upload/<id>` (raw bytes, percent-encoded `X-Filename`), `POST /api/run {id, steps}`, `POST /api/cancel {id}`. Products are served from `/scenes/<id>/scene.splat`, `scene.ply`, `scene.json` and `previews/NNN.jpg`. Only explicit products/input JPEGs are served; arbitrary paths, logs and source uploads are not exposed. Host/Origin checks restrict the service to local clients.

## Capture guidance and limits

Keep the workspace static: arm, tripod, marker, calibration board and props should stay in the same positions throughout. Walk slowly around the table, keeping strong consecutive overlap. Take high and low passes, avoid motion blur/glare and do not change digital zoom during a sequence. Aim for 40–120 sharp views with the table filling much of each frame. Include textured stationary surroundings; blank walls produce few reliable features.

The four example photos are a sparse proof of the pipeline. The checkerboard changes location, and some surroundings are seen only once. Captured views fit well, while orbiting reveals holes, floaters and stretched surfaces. More views are needed before calling this a complete workspace scan. Training-view PSNR is not a held-out reconstruction-quality metric.

The captured arm is baked into the static scene. For a movable digital arm over a scanned background, capture the empty workspace separately or add arm masking/removal in a future capture pass. This version does not segment/remove the arm, infer hidden geometry, train a dynamic/4D scene, or fuse captures automatically.

The coordinate frame is **normalised COLMAP at arbitrary scale**, not the ArUco metric table frame. `scene.json` records the reversible translation/scale from the COLMAP reconstruction. ArUco scale alignment and registration to the real robot are not implemented. The scan is not an occupancy map, collision mesh or source of robot safety measurements.

## Verification

```sh
workspace_splat/.venv/bin/python -m pytest workspace_splat/tests -q
node workspace_splat/tests/viewer.test.mjs
```

Tests cover ingestion, deduplication, video timestamps/sampling, FrameSource BGR conversion, standard export maths/binary layouts, invalid parameters, local service/upload rules and reconstruction failure on textureless input. The real four-photo run also exercised CPU COLMAP, CUDA forward/backward fitting, densification and export. Browser checks cover loading and orbiting the trained scene; the simulation embeds the same viewer.

See `example-report.json` for reproducible settings and measured outputs of the supplied example. It intentionally contains no source photographs or absolute paths.

## Open source used

[COLMAP/PyCOLMAP](https://colmap.github.io/pycolmap/index.html) supplies camera/structure reconstruction (BSD licence); [gsplat](https://github.com/nerfstudio-project/gsplat) supplies differentiable CUDA Gaussian rendering and densification (Apache 2.0); [PyTorch](https://pytorch.org/) supplies optimisation; OpenCV, Pillow and SciPy handle images and seed geometry. Gaussian projection/compositing follows the [gsplat rendering conventions](https://docs.gsplat.studio/main/apis/rasterization.html). New capture orchestration, training loop, exporters, GUI, WebGL viewer and tests were written during the hackathon.

## Meta SAM 3D Objects through a hosted API

In **Reconstruction**, select **Meta SAM 3D / fal.ai API**. Enter your **fal** key in the password field and click **Use key for this session**. Pick a source image and describe the object to reconstruct (for example `table` or `robot arm`). Tick the acknowledgement and click **Send selected image to SAM 3D**. This sends **one selected image** to fal.ai; other uploaded views stay local. On an existing completed capture, a separate result capture is created so the local multi-view scan is preserved.

This uses [fal's hosted SAM 3D Objects endpoint](https://fal.ai/models/fal-ai/sam-3/3d-objects/api). It requires fal billing/key access; a Meta Model API key and its hackathon credit cannot be used here. Meta's [public model catalogue](https://dev.meta.ai/docs/models) lists SAM 3.1 segmentation, not a hosted SAM 3D endpoint. The model itself is [Meta SAM 3D Objects](https://github.com/facebookresearch/sam-3d-objects).

The local server can load `FAL_KEY` from its process environment or the repository's git-ignored `.env` using the existing project env loader. A key entered in the GUI is a memory-only session override. Neither key is written to capture files, browser storage or job logs, and neither is returned by the settings API. The worker receives it via stdin, rather than command-line arguments or an inherited `FAL_KEY`. Restarting the server reloads a saved `FAL_KEY`; clearing the session key does not remove the private configuration entry, revoke the key at fal or terminate an already-submitted request.

A remote submission uses a base64 JPEG, then polls the [fal queue API](https://fal.ai/docs/documentation/model-apis/inference/queue). There is no automatic submission retry after a connection failure, because acceptance may be ambiguous. Cancellation requests provider cancellation; processing/billing may already have happened. On completion the Gaussian PLY is downloaded to the local capture, converted to the existing viewer format, and labelled as a **generated object**, not a fitted multi-view workspace.

The importer requires finite standard 3DGS fields. It retains the original response in private `output/source-sam3d.ply`; the portable viewer export uses degree-0 colour and caps output at 500,000 Gaussians by opacity. Signed artifact URLs and the key are not written to scene metadata. Only HTTPS fal queue/CDN origins are accepted, and the download carries no API key. No configurable third-party proxy or arbitrary callback URL is used.

SAM 3D infers hidden object geometry from a single view; it does not merge the photographs like COLMAP/gsplat. Generated results have an inspection camera, not a recovered input pose or metric robot calibration. The GUI acknowledgement explicitly identifies fal as the destination and payer. The local backend remains the default.

**Recommended host: fal.ai.** Its published price on 31 October 2026 is [$0.02 per generation](https://fal.ai/models/fal-ai/sam-3/3d-objects), and the documented Gaussian PLY output plugs into this importer. Use it for single-image object prediction; retain the local COLMAP/gsplat path for a multi-view workspace scan. See [the audit report](AUDIT-2026-10-31.md) for fixes and remaining verification limits.

Additional API: `GET /api/providers` returns only availability/configured flags; `POST /api/providers/key {provider:"sam3d-fal", api_key:"..."}` sets or clears the session key. Remote runs use `POST /api/run {id, backend:"sam3d-fal", image, prompt, allow_external_upload:true}`. Missing acknowledgement/key is rejected before worker launch.

Verification covers mocked queue/result/PLY import, cancellation, credential redaction, one-image ingest, key storage/clearing and HTTP consent enforcement. **No paid live inference has been verified:** a real fal key and an explicit user submission are still needed. Do not enter keys in chat; use the local GUI's password field.

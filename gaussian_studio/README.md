# Splat Studio

A local desktop app for turning photographs and videos into actual 3D Gaussian splats. It opens its own GTK window with an embedded WebKit renderer, native file pickers and native export save dialogs. It does not launch your browser. It has its own process and capture library and does not require the robot or simulation app. It reuses the tested `workspace_splat` reconstruction, export and WebGL rendering modules.

## Start

Open **Splat Studio** from your application menu, or double-click the generated `gaussian_studio/Splat Studio.desktop` launcher. From the repository root you can also run:

```sh
workspace_splat/.venv/bin/python gaussian_studio/start.py
```

The command opens a normal desktop window. Reopening the launcher focuses the existing app. The window starts its own loopback backend on an OS-assigned port, verifies its identity, and stops that backend when you quit. No URL needs to be opened. Private inputs, trained scenes and provider artifacts remain in `gaussian_studio/data/` (git-ignored). Existing Splat Studio captures are retained.

Keyboard shortcuts: **Ctrl+Q** quits, **Ctrl+R** reloads the interface, **F11** toggles full screen. The title bar includes a reload button. Logs: `gaussian_studio/desktop.log` and `desktop-backend.log`.

Closing the app clears its in-memory provider keys. A saved server-side `FAL_KEY` loads again on the next launch; session overrides do not persist. An already-running reconstruction continues in its detached worker and can be inspected or cancelled when the app is reopened. Use **Stop build** before quitting if you want to stop that reconstruction too. The app only stops its own service; another running interface is unaffected.

The Ubuntu desktop runtime uses system Python with GTK 3, PyGObject and WebKitGTK 4.1, already installed on this machine. CUDA processing uses the separate Gaussian virtual environment. No new desktop dependency was installed. To recreate the desktop runtime on another Ubuntu installation, the relevant packages are `python3-gi` and `gir1.2-webkit2-4.1`.

Install or refresh the per-user application-menu launcher after moving the checkout:

```sh
/usr/bin/python3 gaussian_studio/install_desktop.py
```

For a foreground desktop process or another capture library:

```sh
/usr/bin/python3 gaussian_studio/desktop.py --data /path/to/captures
```

Optional browser serving remains available only when explicitly requested:

```sh
workspace_splat/.venv/bin/python gaussian_studio/start.py --browser
# Or foreground backend only:
workspace_splat/.venv/bin/python -m gaussian_studio --serve --port 8791 --data /path/to/captures
```

The isolated Gaussian runtime is already installed on this machine. To recreate it, use `python3 workspace_splat/bootstrap.py --cuda-compiler --local-python-headers`; see the [pipeline installation notes](../workspace_splat/README.md). Local fitting requires a compatible NVIDIA CUDA GPU. The UI and external-provider paths do not require a local GPU.

## Use

1. Drop images or videos into Source material, choose multiple files, or add a folder. Selections accumulate; remove individual files or clear the list as needed. All supported still images are processed, with no application-level image-count or aggregate upload limit. Uploads stream to disk; each file can be up to 8 GiB. Disk, RAM, capture quality and reconstruction time remain practical limits.
2. Select an engine and quality. Local reconstruction needs at least two distinct overlapping images, or a moving-camera video. It recovers cameras with CPU COLMAP and fits anisotropic Gaussians with CUDA gsplat. A single photo can use a predictive provider. Exact duplicate frames are removed. Video sampling rate and frame budget are explicit; Unlimited removes the video-frame cap.
3. Create a splat. Progress reports preparation, camera recovery, fitting and export. Stop build terminates the worker; stop upload aborts the active upload and keeps completed uploads for retry. A completed scene can be rebuilt at another quality; a new capture preserves the previous scene.
4. Orbit by dragging, pan using the Pan button or Shift-drag, and zoom with the wheel or visible +/− controls. Auto orbit, reset, original camera viewpoints and PNG snapshots are available. The focused viewer also supports arrows, +/− and Home. Double-click resets the camera.
5. Export `.splat`, standard 3DGS `.ply`, or JSON scene metadata. A standalone view is available at `/viewer?scene=<capture-id>`.

The supplied `example-scan` is a copy of the existing four-photo trained example. It is a sparse preview with gaps away from the photographed views. More training cannot recover surfaces that were never observed.

## Quality and estimated duration

| Preset | Training steps | Gaussian cap | Longest training edge |
| --- | ---: | ---: | ---: |
| Quick draft | 1,000 | 25,000 | 512 px |
| Balanced | 2,500 | 40,000 | 640 px |
| High detail | 6,000 | 80,000 | 960 px |
| Ultra-high detail | 20,000 | 160,000 | 1,280 px |

Ultra-high increases fitting duration, spatial detail and output budget. The cap is a maximum; densification may produce fewer Gaussians. Training images are loaded through an eight-image CPU cache, with only the current target transferred to the GPU, instead of storing every input image in GPU memory.

The estimated build range includes camera solving, fitting and export. It uses source-image counts, video duration readable by the renderer and the selected sampling budget. Completed local fits calibrate the fitting estimate; otherwise it starts from a rough GPU baseline. Upload time and first-time CUDA compilation are additional. Unknown video duration uses an explicitly labelled frame-budget approximation. During fitting, after 100 steps, the UI estimates time remaining from measured throughput and refreshes with progress. Scene complexity, image coverage, GPU load and densification can change the actual duration. Provider queue/runtime is provider-dependent and is labelled accordingly.

## External model providers

The desktop backend uses the project's existing env loader and reads `FAL_KEY` from the process environment or the repository root's private `.env` file. Process environment values take precedence. The `.env` file is git-ignored and must remain untracked, owned by the current user, and mode 600. The approved fal connection is stored there; it survives app restarts. No other env values are replaced. See [fal's setup guide](https://fal.ai/get-started.md).

When a fal key is configured, the desktop UI selects **fal.ai / Meta SAM 3D Objects** on launch. This does not submit a job: source selection and the upload/billing acknowledgement are still required. The configured model remains `fal-ai/sam-3/3d-objects`; Meshy has not been selected or integrated.

Open **Provider settings** to inspect the configured flag or enter a session override. The viewer never receives the saved key. GUI-entered keys stay in local server memory and are never written to `.env` or browser storage. Neither saved nor session keys enter capture files, process arguments, job logs or returned API responses. Workers receive the key over stdin; `FAL_KEY` is removed from the child environment. **Clear session key** disables fal for the current server session; it does not delete the saved `FAL_KEY`, revoke the key at fal or undo an already accepted request. To permanently remove the saved key, remove its entry from the private server configuration and revoke it in the fal dashboard.

The existing standard-library HTTP adapter is retained, as permitted by fal's guide; no fal CLI, MCP or new SDK is needed. The worker records the fal request ID as soon as submission succeeds. Ambiguous submissions are not retried. HTTP 403 errors distinguish exhausted credits from budget, archived-account and other account locks, without returning provider response bodies or credentials. Setup does not perform a billable model request. Account credits are managed at [fal credits](https://fal.ai/dashboard/usage-billing/credits).

- **fal.ai / Meta SAM 3D Objects:** built-in integration for [the documented Gaussian-output endpoint](https://fal.ai/models/fal-ai/sam-3/3d-objects/api). Sends one selected image or sampled video frame, predicts an object, and imports the returned Gaussian PLY. It uses fal billing; Meta Model API keys are incompatible. Multiple input views are not fused by this model. [Queue lifecycle](https://fal.ai/docs/documentation/model-apis/inference/queue).
- **Custom HTTP provider:** enter a public HTTPS endpoint, optional model ID, and Bearer API key. Supports services or adapters implementing the contract below, including multi-view reconstruction from all normalized images/video frames. A generic key alone cannot make an unrelated provider API compatible.

Select the engine and acknowledge its upload destination before submitting. Local mode makes no cloud request. Paid live inference has not been exercised; provider tests use mocked responses and real Gaussian PLY fixtures.

### Splat Studio HTTP v1 adapter contract

All URLs must use public HTTPS on port 443. Polling and result URLs must stay on the configured endpoint's origin; redirects and private-network destinations are refused. Only that origin receives the Bearer key. Signed URLs are not persisted in metadata or logs. DNS addresses are checked before requests; deploy against a provider you trust.

The app POSTs a streamed ZIP to the configured endpoint:

```http
POST /v1/splats
Authorization: Bearer YOUR_KEY
Content-Type: application/zip
Content-Length: <archive bytes>
```

The ZIP contains `images/00000.jpg`, additional normalized JPEGs, and `capture.json`:

```json
{
  "version": 1,
  "model": "your-model-id",
  "images": [
    {"name": "00000.jpg", "source": "photo.jpg", "timestamp_s": null, "width": 1280, "height": 960}
  ]
}
```

Extra per-image capture metadata may be present. The model ID may be empty. The original video is not transmitted: sampled normalized frames are transmitted.

Return either:

- HTTP 200 with a standard Gaussian PLY body (`Content-Type: application/octet-stream`), or
- HTTP 202 with `Content-Type: application/json` and `{"status_url":"https://your-provider.com/v1/jobs/123"}`.

The app polls `GET status_url` every two seconds with Bearer authentication. Return JSON `{"status":"queued"}` or `{"status":"processing"}` while active; return `{"status":"completed","ply_url":"https://your-provider.com/v1/results/123.ply"}` when done. Returning a PLY body directly from the status endpoint also completes the job. A failed or unknown state stops processing. Implement `DELETE status_url` to cancel a submitted job. The app requests cancellation after an interruption, polling error or 30-minute timeout. Remote billing/processing may already have occurred. Initial submissions are never automatically retried after connection failures.

Result downloads use the same origin and authentication. Returned PLY must contain finite standard 3DGS position, SH DC colour, opacity logit, log-scale and quaternion fields. Mesh-only and ordinary point-cloud PLY files are rejected. Maximum artifact size: 256 MiB. The original PLY is retained privately; the viewer/portable exports use degree-0 colour and up to 500,000 Gaussians. External results have an inspection camera, rather than recovered or calibrated camera poses.

## Experimental multi-view depth reconstruction

`depth_estimate.py` runs [Depth Anything 3](https://github.com/ByteDance-Seed/Depth-Anything-3) locally in a separate Python 3.12 CUDA environment. It estimates depth and confidence for overlapping views jointly, then exports raw arrays, cameras, colour previews, a filtered coloured point cloud and partial GLB surface mesh. These exports are geometry prototypes, not Gaussian splats; the existing Gaussian viewer does not import them. The default `depth-anything/DA3-LARGE-1.1` weights are pinned to commit `0e109ae307c5982f319a67cf6f9f99ccdc0ec97c`. These weights carry **CC BY-NC 4.0**, so this prototype is not a commercially unrestricted model choice.

The isolated `.depth-venv` and model cache are already installed on this machine. To recreate the environment, create a Python 3.12 venv at `gaussian_studio/.depth-venv`, install compatible CUDA PyTorch/torchvision (this machine uses the official `https://download.pytorch.org/whl/cu130` index), install `requirements-depth.txt`, and install the official Depth Anything 3 source with `pip install --no-deps -e /path/to/Depth-Anything-3`. The tested source checkout is cached at `gaussian_studio/.cache/Depth-Anything-3-main`. This depth-only path does not require the optional DA3 Gaussian head or gsplat package. Initial setup downloads code, dependencies and weights; source photographs remain local.

From the repository root, choose a new output directory:

```sh
gaussian_studio/.depth-venv/bin/python -m gaussian_studio.depth_estimate \
  gaussian_studio/data/<capture-id> --output gaussian_studio/exports/my-depth --mode joint
# After COLMAP reconstruction, condition on the recovered cameras and undistorted images:
gaussian_studio/.depth-venv/bin/python -m gaussian_studio.depth_estimate \
  gaussian_studio/data/<capture-id> --output gaussian_studio/exports/my-posed-depth --mode posed
```

`--resolution 504` is the default longest processing edge; higher resolutions use more GPU memory. `--postprocess-only` rebuilds previews and geometry from saved predictions without another model run. `report.json` records the model, revision, timing, image names, geometry counts and consistency checks. The point-cloud PLY deliberately has no Gaussian attributes. Geometry retains pixels above the 40th confidence percentile that agree with at least one other view within 8% relative depth, accounting for occlusion. Triangles spanning large depth jumps are excluded. This check measures internal agreement, not physical accuracy; overlapping surfaces and duplicated samples remain.

The existing four-photo capture produced 372,193 supported samples and 180,466 triangles with COLMAP conditioning, using approximately 3.1 GiB peak allocated GPU memory. Inference and prediction saving took 1.28 seconds after loading, excluding installation, model loading and geometry export. The joint camera/depth alternative produced 306,584 samples and 147,559 triangles. Both outputs are in the ignored `gaussian_studio/exports/` directory. Neither output has calibrated metres, a robot-frame transform, unseen geometry, or a validated collision mesh. Moving props in the input views also break the static-scene assumption. A practical workspace should fit stable planes, calibrate scale from a known dimension, align the robot frame, and keep editable/collision geometry separate from its visual appearance.

### Depth-guided Gaussian experiment

`depth_splat.py` creates a separate capture and fits actual Gaussians with the existing CUDA trainer. It adds up to 60,000 spatially distributed, cross-view-supported depth samples to the sparse initialization, then uses the estimated optical-Z depths as a robust log-depth loss (weight 0.15), plus a small opacity coverage loss (0.02). It retains the baseline normalization so outputs can be compared from identical cameras. Depth supervision uses the predictor's camera intrinsics to account for cropping and resizing; unrelated camera frames are rejected. Regular RGB-only training remains the default.

```sh
workspace_splat/.venv/bin/python -m gaussian_studio.depth_splat \
  gaussian_studio/data/<baseline-capture> --depth gaussian_studio/exports/my-posed-depth \
  --output gaussian_studio/data/<new-depth-capture>
workspace_splat/.venv/bin/python -m gaussian_studio.compare_splats \
  gaussian_studio/data/<baseline-capture> gaussian_studio/data/<new-depth-capture> \
  --depth gaussian_studio/exports/my-posed-depth --output gaussian_studio/exports/my-comparison
```

The new fit inherits the baseline step count, Gaussian cap, RGB resolution and random seed. It preserves the baseline capture and outputs. This is a combined dense-initialization/depth-loss experiment, not an ablation separating those two effects. The comparison exports an identical-camera turntable video, orbit and training-view contact sheets, training-view PSNR, coverage, and depth agreement on a common supported mask. Agreement with the depths used for training is not independent geometric validation. Four source views cannot establish held-out appearance accuracy or recover unseen surfaces. Gaussian outputs can be opened from the new capture in Splat Studio and exported as standard PLY or SPLAT.

The completed `depth-guided-20261101-20k` experiment used the four existing images, 20,000 steps, a 160,000-Gaussian cap and 1,280-pixel RGB training. It took 496.46 seconds, versus 429 seconds for the baseline. Mean training-view PSNR changed from 31.60 to 28.88 dB; median relative disagreement with the guiding depth maps on common supported pixels changed from 59.64% to 3.01%. This is a mixed result: the splat follows the depth layout much more closely while matching the source RGB images less closely, and large artifacts persist on the full orbit. It is not a validated workspace reconstruction. Comparison images, a decoded/validated 12-second 1920x792 MP4 (288 frames at 24 fps), and the full metrics are in `exports/depth-guided-20k-comparison/`.

## Staged Meta workspace reconstruction

The local experiment in `exports/meta-workspace-20261101/` runs the existing four photographs through a new pipeline. It preserves earlier captures. All model inference is local; source photographs are not sent to a provider. The user has deferred physical measurements, so every geometry export remains in **relative, unverified scale**.

1. `meta_reconstruct.py` runs the Apache-licensed MapAnything checkpoint, jointly estimating cameras and depths. The official source is pinned at `3d10cf7a3016fc0f9bb13a071ee66c47b10be0d9`; weights at `00f9c245bbcb60522d1ed7f9e9d88462c6e3f38a`. Four-view inference took 1.4 seconds after loading, with 5,435 MiB peak allocated GPU memory, and retained 359,780 cross-view-supported samples. This is not independent accuracy validation. Install the official source in the Python 3.12 depth environment with `--no-deps -e <checkout>` and the additional `requirements-meta.txt` inference dependencies. Optional upstream GUI/evaluation packages are not used.
2. `meta_masks.py` uses the existing `~/src/sam3-venv` and cached SAM 3.1 checkpoint (`daa63191845a41281374e725f4c9e51c7a824460`). Run this standalone file with that environment's Python and `-I`. Masks are independently estimated per photo; no cross-photo instance identity is asserted. Additional robot prompts resolved missing masks. `mask_video.py` exports the review video. Paper and marker holes inside the table mask are filled for texture eligibility, while explicit robot and equipment masks remain excluded.
3. `workspace_surfaces.py` fits a rectangular planar tabletop and a dominant planar wall. Near-duplicate wall sheets are removed. The rectangular footprint, planar infill, and nearest-observed-colour texture completion are explicit assumptions. Robot, tripod and chair geometry remains a separate partial observed layer. Exports include coloured GLB meshes, PLY point clouds, arrays and reports. These are not validated collision meshes.
4. `surface_splat.py` fits appearance with fixed surface centres and normals, bounded in-plane Gaussian scales, learned opacity, and degree-one spherical-harmonic colour. It avoids free geometry growth. The final `data/meta-surfaces-textured-20261101` capture has 139,872 Gaussians trained for 8,000 steps in 62.09 seconds. Metrics cover only the static table/wall pixels supported by the proxy, about 40–54% of each input image, and cannot be compared directly with previous full-image PSNR. The standard PLY retains full SH; the native SPLAT viewer uses degree-zero colours. Early and pre-texture-correction fits are retained.
5. `robot_layer.py` imports the existing SO-101 URDF/STL assets without arm drivers or hardware access, performs forward kinematics at an illustrative joint configuration, and exports a separate CAD layer and composed GLB. Placement and scale come from a rough depth-based heuristic. The robot pose was **not recovered from the photos**. Calibration and robot-frame alignment remain required. SAM 3D Objects completion was not run: the documented reference setup requires 32 GB VRAM, versus this laptop's 8 GB.

`stage_video.py` uses a shared camera sweep between the four MapAnything viewpoints, explicitly labelling untrained point-kernel geometry versus trained Gaussian appearance. `--orbit` exports a separate full 360 diagnostic that exposes unseen/missing regions. Object point clouds and known CAD layers receive distinct captions. `manifest.json` describes every video, provenance and limitations. `all-stage-videos.zip` contains all MP4s and the manifest. The raw-versus-layered comparison compares **MapAnything geometry with the later layered result**; it does not compare against the old COLMAP fit, whose camera-centre registration differed by about 32% of the MapAnything camera spread.

Example reproduction, choosing new destinations:

```sh
gaussian_studio/.depth-venv/bin/python -m gaussian_studio.meta_reconstruct \
  gaussian_studio/data/<source> --output gaussian_studio/exports/<run>/01-mapanything
~/src/sam3-venv/bin/python -I gaussian_studio/meta_masks.py \
  gaussian_studio/data/<source> --output gaussian_studio/exports/<run>/02-masks --robot-prompts
gaussian_studio/.depth-venv/bin/python -m gaussian_studio.workspace_surfaces \
  --geometry gaussian_studio/exports/<run>/01-mapanything --masks gaussian_studio/exports/<run>/02-masks \
  --output gaussian_studio/exports/<run>/03-surfaces
workspace_splat/.venv/bin/python -m gaussian_studio.surface_splat \
  gaussian_studio/data/<source> --geometry gaussian_studio/exports/<run>/01-mapanything \
  --surfaces gaussian_studio/exports/<run>/03-surfaces --output gaussian_studio/data/<new-capture>
workspace_splat/.venv/bin/python -m gaussian_studio.stage_video \
  --geometry gaussian_studio/exports/<run>/01-mapanything --trained gaussian_studio/data/<new-capture> \
  --output gaussian_studio/exports/<run>/final.mp4 --title 'Surface-constrained workspace'
```

## Verification

To render a complete turntable directly from a trained local capture, install the optional encoder with `workspace_splat/.venv/bin/python -m pip install -r gaussian_studio/requirements-export.txt`, then run:

```sh
workspace_splat/.venv/bin/python -m gaussian_studio.turntable gaussian_studio/data/<capture-id> \
  --output /path/to/turntable.mp4 --seconds 12 --fps 30 --width 1920 --height 1080
```

This renders the actual trained Gaussians on CUDA and exports H.264 MP4 with a seamless full-orbit camera path. Unobserved surfaces and reconstruction artifacts remain visible; the renderer does not invent missing geometry. Existing output files are preserved.

```sh
workspace_splat/.venv/bin/python -m pytest gaussian_studio/tests workspace_splat/tests -q
/path/to/node --test gaussian_studio/tests/quality.test.mjs workspace_splat/tests/viewer.test.mjs
# Native-window integration check (requires an active desktop session):
/usr/bin/python3 gaussian_studio/tests/desktop_smoke.py
```

Tests cover all stills beyond the old 160-image cap, unlimited ingestion, streamed and interrupted uploads, credential redaction/clearing, provider ZIP payloads, synchronous/asynchronous Gaussian imports, cancellation, destination restrictions, Ultra-high parameter forwarding, rebuild preservation, estimates and viewer transforms. A real four-image run through the new UI recovered four cameras, fitted Gaussians and exported both formats. A separate short CUDA smoke test verifies lazy-target training and export. No paid external run is included.

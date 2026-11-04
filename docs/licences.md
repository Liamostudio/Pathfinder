# Third-party licences and model provenance

Checked **4 November 2026** by reading repository manifests/imports, retained notices and upstream pages through web search/retrieval. This inventory covers the named models, direct requirements in `pyproject.toml`, `simulation/requirements.txt`, `workspace_splat/requirements.txt`, `gaussian_studio/requirements-{meta,depth,export}.txt`, the vision tool, known hardware support libraries, desktop bindings and bundled assets. Optional/developer-only paths are identified: being installed or listed does not mean a model was used in the submitted demo. This is not a complete transitive binary SBOM or a finding that every distribution is compliant.

**No model weights are included in Git.** Tracked-file inspection found no `.pt`, `.pth`, `.safetensors`, `.task`, `.tflite` or `.onnx` files. Model caches and ignored `weights/` stay local. Source imports and package installs do not transfer ownership of those materials. The two font files and SO-101 URDF/meshes are deliberate third-party assets with retained notices. No repository-wide licence for Pathfinder's original code was found: **Liam must choose one**; upstream licences do not make that choice for him.

“Verified” means the linked upstream text/card was readable on this check, not that every installed wheel and transitive component was audited. Unless a revision is explicitly given, upstream links follow the current branch. **Unverified** means no readable authoritative terms for that exact material were established; earlier README assertions are not treated as verification.

## Attribution key

These are practical summaries; follow the linked full licence for any redistribution:

- **A — Apache 2.0:** retain copyright, licence and applicable NOTICE attribution; identify modifications. Credit the named upstream authors. This does not grant trademark rights.
- **B — BSD:** retain copyright, conditions and disclaimer in source and binary documentation; observe the relevant non-endorsement clause where present.
- **M — MIT:** retain copyright and permission notice with copies/substantial portions. Pillow's MIT-CMU text also requires notices in supporting documentation and restricts promotional use of names.
- **G — GPL:** retain notices/licence and meet applicable corresponding-source and copyleft obligations when conveying covered software; attribution alone is insufficient. Distribution/combination implications require review before shipping a bundled application.
- **L — LGPL / mixed:** preserve notices and meet the applicable library/source/relinking requirements when distributing; check the actual build and included components.
- **O — OFL:** distribute fonts with copyright and OFL text; preserve applicable reserved-name conditions. Existing local font notices are retained.
- **U — unverified:** attribution/redistribution requirements have not been established; do not assume a permissive grant.

## Models, APIs and robot assets

| Name | What for / actual use | Licence and verification source | Weights included? | Attribution required / outstanding check |
| --- | --- | --- | --- | --- |
| Meta SAM 3.1, via SAM 3 source | Local segmentation/tracking; workspace masks and slower live people/hands | **SAM License**, verified [upstream licence](https://github.com/facebookresearch/sam3/blob/main/LICENSE); [3.1 model card](https://huggingface.co/facebook/sam3.1) points to SAM 3 and gated access | **No**, local gated cache | Pathfinder acknowledges use of the **SAM Materials** in README. Licence requires acknowledgement for published research results and a licence copy with redistributed SAM Materials/derivatives; observe its other use restrictions. This is not Apache 2.0. |
| MapAnything source | Local workspace reconstruction | Apache 2.0, [source licence](https://github.com/facebookresearch/map-anything/blob/main/LICENSE) verified | N/A; external source, not vendored | A; credit Meta/MapAnything authors; upstream asks for paper citation. |
| `facebook/map-anything-apache` | Cached geometry checkpoint; not the separately licensed research variant | Apache 2.0 verified on [official model card](https://huggingface.co/facebook/map-anything-apache). Exact cached revision provenance below | **No**, ignored cache | A. Exact revision not independently re-fetched on this check; retain the 3 Nov evidence distinction below. |
| MediaPipe Hand Landmarker full bundle (palm detector + landmarks) | Fast hand gate, CPU; also vision tool | Apache 2.0 verified in Google's [Hand Tracking model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Hand%20Tracking%20%28Lite_Full%29%20with%20Fairness%20Oct%202021.pdf), linked by the [official task guide](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker) | **No**, `hand_landmarker.task` downloaded separately | A; credit Google MediaPipe. Code and model terms were checked separately. |
| EfficientDet-Lite0 float16, version 1 | Person boxes only in RGB monitoring/vision tool; not the SAM Deck feed | **Unverified for exact downloaded weights.** [Google's task guide](https://ai.google.dev/edge/mediapipe/solutions/vision/object_detector) identifies the float16 model but its page footer licenses documentation/code samples, not necessarily weights. [TensorFlow model listing](https://www.kaggle.com/models/tensorflow/efficientdet/tfLite/lite0-detection-metadata/1) returned no readable terms | **No**, `efficientdet_lite0.tflite` downloaded separately | U. Earlier project docs say Apache 2.0; this check did not establish that grant for the exact MediaPipe-hosted float16 bundle. Obtain its authoritative model licence before redistributing. |
| MediaPipe Pose Landmarker lite | Optional 33-point person skeletons in Pathfinder Vision, not the hero demo | Apache 2.0 verified in the [official pose model card](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20BlazePose%20GHUM%203D.pdf), linked for lite/full/heavy by the [task guide](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker) | **No**, optional `.task` outside Git | A; credit Google MediaPipe. Model terms checked separately from code. |
| Muse Spark 1.3 (`muse-spark-1.3`) | Hosted orchestrator API; real simulator runs recorded in PROGRESS | Hosted service, not a source/weights licence. **Exact applicable API terms unverified**; [Meta developer portal](https://dev.meta.ai/) / `docs/model-access.md` identify access, not a redistribution grant | **No**, API only | U for service-specific attribution/usage terms; clearly name Meta/Muse Spark. Do not call it Spark Open or claim open weights. |
| Muse Voice Transcribe 1.0 | Hosted speech API; real glasses speech run recorded 4 Nov | Hosted service; **exact applicable API terms unverified** ([portal](https://dev.meta.ai/)) | **No**, API only | U for contractual attribution; clearly name Meta/Muse Voice Transcribe. |
| Meta Wearables DAT for iOS 1.0.1 | Companion registration/device status; glasses microphone path uses iOS audio | [Upstream LICENSE](https://github.com/facebook/meta-wearables-dat-ios/blob/main/LICENSE) names **Meta Wearables Developer Terms**, not an OSI licence. Exact pinned 1.0.1 terms **unverified**: tag retrieval failed | N/A; SDK linked through Swift Package Manager, not vendored | [Developer terms](https://wearables.developer.meta.com/terms) and AUP govern use; full obligations/attribution for pinned SDK remain U. Credit Meta; do not describe the SDK as Apache. |
| SO-101 URDF + 13 meshes, TheRobotStudio/SO-ARM100 | Visual robot and MuJoCo model | Apache 2.0 verified at [pinned source revision](https://github.com/TheRobotStudio/SO-ARM100/blob/a758567c3978dfeefe282ede0500085a48fe8f78/LICENSE) | No learned weights; **URDF/meshes included** | A; preserve `simulation/assets/so101/LICENSE.upstream` and `SOURCE.json` (revision `a758567c3978dfeefe282ede0500085a48fe8f78`, `Simulation/SO101`). Runtime lime material/contact/base changes are project code. |
| Depth Anything 3 source | Optional Splat Studio depth prototype, outside main demo path | Apache 2.0 [source licence](https://github.com/ByteDance-Seed/Depth-Anything-3/blob/main/LICENSE), verified | N/A; source cache excluded | A; variants' weights must be checked separately. |
| `depth-anything/DA3-LARGE-1.1` | Pinned optional developer-tool depth checkpoint | Apache 2.0 in [model card at pinned `0e109ae307c5982f319a67cf6f9f99ccdc0ec97c`](https://huggingface.co/depth-anything/DA3-LARGE-1.1/blob/0e109ae307c5982f319a67cf6f9f99ccdc0ec97c/README.md), verified | **No**, ignored cache | A according to this pinned upstream card. **Conflict:** `gaussian_studio/README.md` calls the same checkpoint CC BY-NC 4.0. Cached file/card correspondence has not been re-audited; flag for owner reconciliation, do not generalise to all DA3 variants. |
| SAM 3D Objects | Optional hosted-object adapter, mocked/tested; no live paid inference acceptance in PROGRESS | **SAM License**, [upstream licence](https://github.com/facebookresearch/sam-3d-objects/blob/main/LICENSE) verified. Hosted provider's separate service terms **unverified** | **No**, optional remote API | Acknowledge SAM Materials; retain licence if redistributing them/derivatives. Do not represent this as the local multi-view scan or a verified demo model call. |

### Cached MapAnything checkpoint: resolving the 3 Nov concern

`docs/integration-2026-11-03.md` said the cached licence had not been checked. The later **3 Nov “Codex integration baseline” PROGRESS entry** and [dependency provenance](dependency-provenance.md) record a subsequent check: cache path `gaussian_studio/.cache/huggingface/models--facebook--map-anything-apache/snapshots/00f9c245bbcb60522d1ed7f9e9d88462c6e3f38a/`, containing `config.json` and `model.safetensors`, with no cached licence/card. That check reports public revision metadata with the same SHA and `cardData.license: apache-2.0`; source revision `3d10cf7a3016fc0f9bb13a071ee66c47b10be0d9` had Apache source terms.

On 4 Nov the official **Apache-variant card** was readable and confirmed Apache 2.0. Attempts to retrieve the [exact revision metadata](https://huggingface.co/api/models/facebook/map-anything-apache/revision/00f9c245bbcb60522d1ed7f9e9d88462c6e3f38a) and revision README failed through the web tool, so the exact cache-to-revision correspondence is **not independently reverified tonight**; it rests on the explicit 3 Nov provenance, not a new hash inspection. No models were loaded. The separate research checkpoint is not covered by this Apache-variant statement.

## Core, reconstruction and hardware libraries

“N/A” in the weights column means library code, with no library-supplied model weights committed here. Package binaries/caches are not vendored. The licences below do not cover every bundled codec, BLAS/CUDA component or transitive dependency in a wheel.

| Name | What for | Licence / upstream verification | Weights included? | Attribution |
| --- | --- | --- | --- | --- |
| MediaPipe | CPU Tasks inference | Apache 2.0, [LICENSE](https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE), verified | N/A; models above separate | A |
| MuJoCo (requirements: 3.17.0) | Simulated dynamics/rendering | Apache 2.0, [LICENSE](https://github.com/google-deepmind/mujoco/blob/main/LICENSE), verified upstream | N/A | A |
| gsplat (1.5.3) | Gaussian rasterisation/densification | Apache 2.0, [version licence](https://github.com/nerfstudio-project/gsplat/blob/v1.5.3/LICENSE), verified | N/A | A |
| COLMAP / PyCOLMAP (4.2.1) | Optional classical reconstruction | BSD 3-Clause, [upstream licence](https://github.com/colmap/colmap/blob/main/LICENSE), verified; exact 4.2.1 tag/binary dependencies unverified | N/A | B; upstream explicitly says dependencies may affect the resulting build licence |
| PyTorch | Tensor inference/optimisation | BSD-style, [LICENSE](https://github.com/pytorch/pytorch/blob/main/LICENSE), verified | N/A; no pretrained weights from package committed | B; retain bundled third-party notices too |
| torchvision | Vision/model runtime dependency | BSD 3-Clause, [LICENSE](https://github.com/pytorch/vision/blob/main/LICENSE), verified | N/A | B; pretrained weights, if selected later, need separate review |
| NumPy | Arrays/geometry | BSD 3-Clause, [LICENSE](https://github.com/numpy/numpy/blob/main/LICENSE.txt), verified | N/A | B |
| SciPy | Spatial operations/rotation | BSD 3-Clause, [LICENSE](https://github.com/scipy/scipy/blob/main/LICENSE.txt), verified | N/A | B |
| OpenCV / opencv-contrib-python / opencv-python-headless | Capture, calibration, image/video processing | Apache 2.0 current core [LICENSE](https://github.com/opencv/opencv/blob/4.x/LICENSE), verified; exact wheels/contrib/codec notices unverified | N/A | A for core; preserve wheel-specific third-party notices |
| Pillow | Image import/export | MIT-CMU, [LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE), verified | N/A | M, including supporting-documentation notice |
| plyfile (1.1.5) | PLY import/export | **GPLv3**, [pinned COPYING](https://github.com/dranjan/python-plyfile/blob/v1.1.5/COPYING), verified | N/A | **G**, review distribution implications; not an Apache dependency |
| ninja (1.13.2) | CUDA extension build helper | Apache 2.0, [COPYING](https://github.com/ninja-build/ninja/blob/master/COPYING), verified upstream | N/A | A |
| pytest | Tests | MIT, [LICENSE](https://github.com/pytest-dev/pytest/blob/main/LICENSE), verified | N/A | M |
| filelock | Training cache/compile locking | MIT, [LICENSE](https://github.com/tox-dev/filelock/blob/main/LICENSE), verified | N/A | M |
| LeRobot (installed 0.6.1) | Commissioning and follower bus support | Apache 2.0 main project, [LICENSE](https://github.com/huggingface/lerobot/blob/main/LICENSE), verified; exact installed optional dependency set unverified | **No**, no LeRobot policy weights | A; do not infer terms for every motor SDK from LeRobot's licence |
| pyserial | Hardware serial transport | BSD 3-Clause, [LICENSE](https://github.com/pyserial/pyserial/blob/master/LICENSE.txt), verified | N/A | B |
| deepdiff | LeRobot configuration support | MIT, [LICENSE](https://github.com/seperman/deepdiff/blob/master/LICENSE), verified | N/A | M |
| draccus | LeRobot argument/configuration dependency | MIT identified by [upstream repository](https://github.com/marin-community/draccus) (redirect from `dlwh/draccus`); full licence file retrieval failed, exact installed version terms **unverified** | N/A | M per upstream identification; retain notices; verify exact installed distribution |
| feetech-servo-sdk / `scservo_sdk` | Low-level commissioning bus dependency | **Unverified** exact installed distribution and licence. Multiple similarly named repositories exist; [candidate Python SDK](https://github.com/vassar-robotics/feetech-servo-sdk) is not assumed to match the installed package | N/A | U; inspect installed distribution provenance before redistribution |
| cuRobo | Planned only, **NOT integrated** | Apache 2.0, [LICENSE](https://github.com/NVlabs/curobo/blob/main/LICENSE), verified | N/A | A if distributed; not credited as our runtime planner |

## Supporting requirements and optional developer tools

These packages are explicitly listed in the depth/Meta/export requirements. Some are imported by upstream models rather than project code; no claim is made that every optional package executed during the demo. **All weights columns below are N/A; no weights are committed.**

| Name | What for | Licence / upstream verification | Weights included? | Attribution |
| --- | --- | --- | --- | --- |
| uniception (0.1.7) | MapAnything inference support | BSD 3-Clause, verified [upstream LICENSE](https://github.com/castacks/UniCeption/blob/main/LICENSE); [author-published package page](https://pypi.org/project/uniception/) identifies this source and 0.1.7 | N/A | B |
| hydra-core | Model configuration | MIT, [LICENSE](https://github.com/facebookresearch/hydra/blob/main/LICENSE), verified | N/A | M |
| natsort | Input ordering | MIT, [LICENSE](https://github.com/SethMMorton/natsort/blob/main/LICENSE), verified | N/A | M |
| orjson | Model serialisation support | Apache 2.0 option verified in [LICENSE-APACHE](https://github.com/ijl/orjson/blob/master/LICENSE-APACHE); other licence options not audited | N/A | A for this option |
| pillow-heif | Optional HEIC import; temporary converter in 4 Nov re-scan | BSD 3-Clause, [LICENSE](https://github.com/bigcat88/pillow_heif/blob/master/LICENSE.txt), verified; bundled libheif/codecs unverified | N/A | B; codec notices separate |
| python-box | Model configuration dictionaries | MIT identified in [upstream source header](https://github.com/cdgriffith/Box/blob/master/box/box.py); full licence file retrieval failed | N/A | M per source header; full distribution terms unverified |
| jaxtyping | Tensor annotation support | MIT, [LICENSE](https://github.com/patrick-kidger/jaxtyping/blob/main/LICENSE), verified | N/A | M |
| scikit-learn | Geometry/model dependency | BSD 3-Clause, [COPYING](https://github.com/scikit-learn/scikit-learn/blob/main/COPYING), verified | N/A | B |
| minio (Python client) | Upstream model dependency, not a deployed storage service here | Apache 2.0, [LICENSE](https://github.com/minio/minio-py/blob/master/LICENSE), verified | N/A | A; this is not a claim about MinIO server licensing |
| torchmetrics | Model metrics dependency | Apache 2.0, [LICENSE](https://github.com/Lightning-AI/torchmetrics/blob/master/LICENSE), verified | N/A | A |
| termcolor | Model console output | MIT, [COPYING](https://github.com/termcolor/termcolor/blob/main/COPYING.txt), verified | N/A | M |
| timm | Vision-model modules | Apache 2.0, [LICENSE](https://github.com/huggingface/pytorch-image-models/blob/main/LICENSE), verified | N/A | A; independently selected pretrained weights need their own check |
| imageio-ffmpeg | Optional MP4 encoder wrapper | BSD 2-Clause wrapper, [LICENSE](https://github.com/imageio/imageio-ffmpeg/blob/main/LICENSE), verified | N/A | B; bundled FFmpeg is separately licensed below |
| FFmpeg executable | Video encoding through optional wrapper | [Official licensing page](https://ffmpeg.org/legal.html): LGPL 2.1+ generally; GPL applies with optional GPL components. **Exact bundled binary licence/build unverified** | N/A | L/G according to actual build; retain matching source/notices if redistributed |
| einops | Tensor reshaping | MIT, [LICENSE](https://github.com/arogozhnikov/einops/blob/main/LICENSE), verified | N/A | M |
| OmegaConf | Depth configuration | BSD 3-Clause, [LICENSE](https://github.com/omry/omegaconf/blob/master/LICENSE), verified | N/A | B |
| addict | Depth configuration | MIT, [LICENSE](https://github.com/mewwts/addict/blob/master/LICENSE), verified | N/A | M |
| huggingface_hub | Model retrieval/cache | Apache 2.0, [LICENSE](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE), verified | N/A | A; each retrieved model has separate terms |
| safetensors | Model storage/loading | Apache 2.0, [LICENSE](https://github.com/huggingface/safetensors/blob/main/LICENSE), verified | N/A | A |
| trimesh | Optional depth surface export/robot meshes | MIT, [LICENSE](https://github.com/mikedh/trimesh/blob/main/LICENSE.md), verified | N/A | M |
| imageio | Depth/video dependency | BSD 2-Clause, [LICENSE](https://github.com/imageio/imageio/blob/master/LICENSE), verified | N/A | B |
| moviepy (1.0.3) | Optional depth environment video support | MIT, verified [version licence](https://github.com/Zulko/moviepy/blob/v1.0.3/LICENCE.txt) | N/A | M; do not infer codec terms from wrapper terms |
| evo | Optional depth environment trajectory/evaluation dependency | **GPLv3**, [LICENSE](https://github.com/MichaelGrupp/evo/blob/master/LICENSE), verified upstream; exact installed build unverified | N/A | **G**; no claim of demo-time use |
| Matplotlib | Optional depth previews/dependency | Matplotlib licence, [LICENSE](https://github.com/matplotlib/matplotlib/blob/main/LICENSE/LICENSE), verified | N/A | Retain licence/copyright; summarise changes to Matplotlib if distributing a derivative |
| PyGObject (`gi`) | Splat Studio desktop bindings | LGPL 2.1+, verified in [official overview](https://pygobject.gnome.org/index.html) | N/A | L |
| GTK 3 / GDK | Desktop window | **Exact installed terms unverified**: [upstream COPYING](https://gitlab.gnome.org/GNOME/gtk/-/blob/gtk-3-24/COPYING) retrieval failed | N/A | U for this build; retain distribution notices |
| GLib / GIO | Desktop event loop/files | LGPL 2.1-or-later, [COPYING pointer](https://github.com/GNOME/glib/blob/main/COPYING), verified upstream | N/A | L |
| WebKitGTK / WebKit | Embedded desktop browser | Mixed LGPL/BSD, verified [official licensing overview](https://webkit.org/licensing-webkit/); exact installed component set unverified | N/A | L/B as applicable; preserve per-component notices |
| Space Grotesk font | Bundled UI typography | SIL OFL 1.1, [upstream OFL](https://github.com/floriankarsten/space-grotesk/blob/master/OFL.txt), verified | N/A; font included | O; retain `lookandpick/web/assets/fonts/SpaceGrotesk-LICENSE.txt` |
| Space Mono font | Bundled UI typography | SIL OFL 1.1, [upstream OFL](https://github.com/googlefonts/spacemono/blob/main/OFL.txt), verified | N/A; font included | O; retain `lookandpick/web/assets/fonts/SpaceMono-LICENSE.txt` |

Python/its standard library, Node (test runner), Apple SwiftUI/AVFoundation/Security frameworks and operating-system graphics libraries are platform prerequisites, not vendored project libraries. Their exact installed SDK/runtime distribution terms and transitive components are **unverified** in this document. `torchaudio` is explicitly excluded by `requirements-meta.txt`; its [upstream BSD 2-Clause licence](https://github.com/pytorch/audio/blob/main/LICENSE) was checked, but it is not counted as demo tooling. Glimmer, Spark Open, Wonder, Image and V-JEPA 2 were not used; they are not models whose weights this project distributes.

## Owner follow-up

1. Choose the original-code licence. Review `plyfile`'s GPLv3 and optional `evo`/desktop/codec distribution implications before selecting terms for a bundled release; do not assume that adding an Apache root licence resolves dependencies.
2. Resolve EfficientDet-Lite0's exact float16 weight terms; complete Meta API/DAT pinned terms, exact draccus distribution, Feetech SDK and installed desktop/codec checks marked **unverified** above. Keep weights outside Git.
3. Reconcile the pinned DA3 card versus `gaussian_studio/README.md`; retain cached evidence before changing its documented grant. Re-fetch/archive MapAnything's exact-revision licence metadata when available.
4. This pass changes only the five files authorised in the brief. Existing source notices, upstream code, weights and developer-tool README are not modified. This inventory records evidence and gaps; it does not claim legal clearance of the complete application.

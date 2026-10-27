"""Record a short webcam clip, track a text prompt with SAM 3.1, play back with masks.

Run with the scratch venv (see PROGRESS.md):
  QT_QPA_PLATFORM=xcb PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    ~/src/sam3-venv/bin/python -I scripts/sam3_live.py --prompt hand --seconds 4
  ... --video path.mp4 --prompt bed      # use a file instead of the webcam
In the window: q or Esc quits. Clips are saved under /tmp-style scratch (--out), never the repo.
Workarounds for upstream facebookresearch/sam3 are the same as scripts/sam3_smoke.py.
"""
import argparse, glob, shutil, tempfile, time
import cv2, numpy as np, torch
from sam3.model_builder import build_sam3_predictor

ap = argparse.ArgumentParser()
ap.add_argument("--prompt", required=True)
ap.add_argument("--video", help="video file instead of webcam")
ap.add_argument("--cam", type=int, default=0)
ap.add_argument("--seconds", type=float, default=4)
ap.add_argument("--max-frames", type=int, default=60)
ap.add_argument("--out", help="write annotated mp4 here")
ap.add_argument("--no-window", action="store_true")
a = ap.parse_args()

tmp = tempfile.mkdtemp(prefix="sam3live_")
frames = []
if a.video:
    cap = cv2.VideoCapture(a.video)
    fps_in = cap.get(cv2.CAP_PROP_FPS) or 15
    while len(frames) < a.max_frames:
        ok, f = cap.read()
        if not ok: break
        frames.append(cv2.resize(f, (640, int(640 * f.shape[0] / f.shape[1]))))
else:
    cap = cv2.VideoCapture(a.cam)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    fps_in = 15
    print(f"Recording {a.seconds}s from camera {a.cam} in 2s ...", flush=True); time.sleep(2)
    t0 = time.time()
    while time.time() - t0 < a.seconds and len(frames) < a.max_frames:
        ok, f = cap.read()
        if not ok: raise SystemExit("camera read failed")
        frames.append(f); time.sleep(1 / fps_in)
cap.release()
print(f"{len(frames)} frames", flush=True)
for i, f in enumerate(frames):
    cv2.imwrite(f"{tmp}/{i:05d}.jpg", f)

ck = glob.glob("~/.cache/huggingface/hub/models--facebook--sam3.1/snapshots/*/sam3.1_multiplex.pt".replace("~", __import__("os").path.expanduser("~")))[0]
p = build_sam3_predictor(checkpoint_path=ck, version="sam3.1", use_fa3=False, use_rope_real=True,
                         max_num_objects=4, async_loading_frames=False)
p.model.detector.backbone.vision_backbone.trunk.bfloat16()
p.model.detector.backbone.language_backbone.encoder.bfloat16()
p.model.batched_grounding_batch_size = 1
p.model.postprocess_batch_size = 1
_i = p.model.init_state
p.model.init_state = lambda **kw: _i(**{k: v for k, v in kw.items() if k != "offload_state_to_cpu"})

sid = p.handle_request({"type": "start_session", "resource_path": tmp, "offload_video_to_cpu": True})["session_id"]
p.handle_request({"type": "add_prompt", "session_id": sid, "frame_index": 0, "text": a.prompt})
masks = {}
t = time.time()
for out in p.handle_stream_request({"type": "propagate_in_video", "session_id": sid}):
    o = out.get("outputs") or {}
    m = o.get("out_binary_masks")
    m = np.zeros((0, 1, 1), bool) if m is None else np.asarray(m.cpu() if hasattr(m, "cpu") else m)
    masks[out.get("frame_index", len(masks))] = (m, np.asarray(o.get("out_obj_ids", [])))
torch.cuda.synchronize()
dt = time.time() - t
found = sum(1 for m, _ in masks.values() if m.ndim >= 3 and m.shape[0] > 0)
print(f"tracked {len(masks)} frames in {dt:.1f}s ({len(masks)/dt:.1f} fps), frames with a mask: {found}, peak VRAM {torch.cuda.max_memory_allocated()/1e9:.2f} GB", flush=True)

colours = [(0, 255, 0), (255, 128, 0), (0, 128, 255), (255, 0, 255)]
def draw(i):
    img = frames[i].copy()
    m, ids = masks.get(i, (np.zeros((0,)), []))
    for k in range(m.shape[0] if m.ndim >= 3 else 0):
        mk = cv2.resize(m[k].astype(np.uint8), (img.shape[1], img.shape[0])) > 0
        img[mk] = (0.5 * img[mk] + 0.5 * np.array(colours[k % 4])).astype(np.uint8)
    cv2.putText(img, f'"{a.prompt}"  objs={m.shape[0] if m.ndim >= 3 else 0}  frame {i}', (8, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return img
if a.out:
    h, w = frames[0].shape[:2]
    vw = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), fps_in, (w, h))
    for i in range(len(frames)): vw.write(draw(i))
    vw.release(); print("saved", a.out)
if not a.no_window:
    while True:
        for i in range(len(frames)):
            cv2.imshow("SAM 3.1 (q to quit)", draw(i))
            if cv2.waitKey(int(1000 / fps_in)) & 0xFF in (ord("q"), 27):
                raise SystemExit
shutil.rmtree(tmp, ignore_errors=True)

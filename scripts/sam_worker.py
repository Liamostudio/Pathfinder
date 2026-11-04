"""Local SAM 3.1 worker: a text prompt and a frame in, small detections out. Run it with the SAM scratch environment:

  PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True ~/src/sam3-venv/bin/python scripts/sam_worker.py            # serve on 127.0.0.1:8767
  PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True ~/src/sam3-venv/bin/python scripts/sam_worker.py --selftest ~/src/sam3/assets/images/groceries.jpg --prompt banana

  POST /segment  {"image_b64": <jpeg>, "prompt": "red cube", "max_objects": 5, "points": 0}
                 -> {"detections": [{"score","box","centroid","bottom","area", "points": [[x, y], ...] (only if "points" > 0, at most 400)}],
                     "image": {"width","height"}, "latency_ms"}
  GET  /health   -> {"ok": true, "loaded": bool, "model": "sam3.1"}

A still frame is a one-frame session for SAM's predictor (the API scripts/sam3_live.py uses for video); the detections are add_prompt's answer.
Loopback only, one request at a time on the main thread, bodies capped. While it runs it HOLDS the shared GPU lease (lookandpick/gpu.py), so the workspace
reconstruction waits for it instead of running the 8 GB card out of memory; stop the worker to give the GPU back.
No camera, no motor, no network beyond 127.0.0.1. Workarounds for upstream facebookresearch/sam3 are the same as scripts/sam3_smoke.py.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 12_000_000


def _load(name: str, relative: str):
    """Import a stdlib/numpy-only repo module by path, so this script needs no installed package."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class SamModel:
    """The resident SAM 3.1 predictor. `segment` is serialised by a lock: the GPU does one thing at a time."""

    def __init__(self, checkpoint: Path | None):
        import torch
        if not torch.cuda.is_available():
            raise SystemExit("SAM 3.1 needs a CUDA GPU and none is available")
        checkpoints = sorted((Path.home() / ".cache/huggingface/hub/models--facebook--sam3.1/snapshots").glob("*/sam3.1_multiplex.pt"))
        ck = checkpoint.expanduser() if checkpoint else (checkpoints[-1] if checkpoints else None)
        if ck is None or not ck.is_file():
            raise SystemExit("SAM 3.1 checkpoint not found; pass --checkpoint /path/to/sam3.1_multiplex.pt")
        from sam3.model_builder import build_sam3_predictor
        self.torch, self.lock = torch, threading.Lock()
        started = time.perf_counter()
        p = build_sam3_predictor(checkpoint_path=str(ck), version="sam3.1", use_fa3=False, use_rope_real=True, max_num_objects=4,
                                 multiplex_count=16, async_loading_frames=False)
        p.model.detector.backbone.vision_backbone.trunk.bfloat16()
        p.model.detector.backbone.language_backbone.encoder.bfloat16()
        p.model.batched_grounding_batch_size = 1
        p.model.postprocess_batch_size = 1
        init = p.model.init_state
        p.model.init_state = lambda **kw: init(**{k: v for k, v in kw.items() if k != "offload_state_to_cpu"})
        self.predictor = p
        torch.cuda.synchronize()
        print(f"SAM 3.1 loaded in {time.perf_counter() - started:.1f} s, VRAM {torch.cuda.memory_allocated() / 1e9:.2f} GB", flush=True)

    def segment(self, jpeg: bytes, prompt: str, max_objects: int) -> tuple[dict, tuple[int, int]]:
        import cv2
        import numpy as np
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("cannot decode the image")
        height, width = image.shape[:2]
        with self.lock, tempfile.TemporaryDirectory(prefix="samworker_") as folder:
            cv2.imwrite(f"{folder}/00000.jpg", image)
            p = self.predictor
            sid = p.handle_request({"type": "start_session", "resource_path": folder, "offload_video_to_cpu": True})["session_id"]
            try:
                # For a single frame the detections are in add_prompt's own answer; propagating a one-frame video returns nothing.
                reply = p.handle_request({"type": "add_prompt", "session_id": sid, "frame_index": 0, "text": prompt})
                outputs = reply.get("outputs") or {}
                self.torch.cuda.synchronize()
            finally:
                p.handle_request({"type": "close_session", "session_id": sid})
        return outputs, (width, height)


def run_segment(model, masks_module, jpeg: bytes, prompt: str, max_objects: int, points: int = 0) -> dict:
    started = time.perf_counter()
    outputs, size = model.segment(jpeg, prompt, max_objects)
    found = masks_module.detections_from_outputs(outputs, size, sample_points=points)[:max_objects]
    return {"detections": [{"score": d.score, "box": list(d.box), "centroid": list(d.centroid), "bottom": list(d.bottom), "area": d.area,
                            **({"points": [list(p) for p in d.points]} if points else {})} for d in found],
            "image": {"width": size[0], "height": size[1]}, "latency_ms": round((time.perf_counter() - started) * 1000)}


def make_handler(model, masks_module):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _local(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return self.client_address[0] in ("127.0.0.1", "::1") and host in ("127.0.0.1", "localhost", "[::1]")

        def do_GET(self):
            if not self._local():
                return self._send(403, {"error": "local requests only"})
            if self.path == "/health":
                return self._send(200, {"ok": True, "loaded": model is not None, "model": "sam3.1"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._local():
                return self._send(403, {"error": "local requests only"})
            if self.path != "/segment":
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_BODY:
                    return self._send(413, {"error": "body missing or too large"})
                body = json.loads(self.rfile.read(length))
                prompt = str(body["prompt"]).strip()
                if not prompt or len(prompt) > 80:
                    return self._send(400, {"error": "prompt must be 1 to 80 characters"})
                jpeg = base64.b64decode(body["image_b64"], validate=True)
                limit = max(1, min(10, int(body.get("max_objects", 5))))
                sample = max(0, min(400, int(body.get("points", 0))))
                self._send(200, run_segment(model, masks_module, jpeg, prompt, limit, sample))
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                self._send(400, {"error": "bad request"})
            except Exception as exc:                                      # a model failure must not kill the server
                traceback.print_exc(file=sys.stderr)
                self._send(500, {"error": f"segmentation failed: {type(exc).__name__}"})
    return Handler


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--checkpoint", type=Path)
    ap.add_argument("--selftest", type=Path, help="segment this image once, print the detections and exit (no server)")
    ap.add_argument("--prompt", default="object")
    ap.add_argument("--no-gpu-lease", action="store_true", help="do not hold the shared GPU lease (risk: out of memory next to reconstruction)")
    args = ap.parse_args(argv)
    masks = _load("sam_masks", "lookandpick/adapters/sam_masks.py")
    lease = None
    if not args.no_gpu_lease:
        gpu = _load("gpu_lease_module", "lookandpick/gpu.py")
        lease = gpu.gpu_lease("sam-worker")
        print("Waiting for the GPU lease (the reconstruction may be using the GPU)...", flush=True)
        lease.__enter__()
    try:
        model = SamModel(args.checkpoint)
        if args.selftest:
            reply = run_segment(model, masks, args.selftest.read_bytes(), args.prompt, 5)
            print(json.dumps(reply, indent=1))
            return
        # One thread on purpose: PyTorch keeps autocast/inference settings per thread, so the model must be called from the thread
        # that built it, and the GPU does one request at a time anyway.
        server = HTTPServer(("127.0.0.1", args.port), make_handler(model, masks))
        print(f"SAM worker on http://127.0.0.1:{args.port}  (Ctrl-C to stop and release the GPU)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        if lease is not None:
            lease.__exit__(None, None, None)


if __name__ == "__main__":
    main()

"""SAM 3.1 smoke test for the 8 GB RTX 5070. Run with ~/src/sam3-venv (see PROGRESS.md).
Run: PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True ~/src/sam3-venv/bin/python -I scripts/sam3_smoke.py
Workarounds for upstream facebookresearch/sam3: bf16 on the two big modules, grounding batch size 1,
drop offload_state_to_cpu from init_state, multiplex_count must stay 16."""
import argparse
from pathlib import Path
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--checkpoint", type=Path)
    ap.add_argument("--video", type=Path, default=Path.home() / "src/sam3/assets/videos/bedroom.mp4")
    ap.add_argument("--prompt", default="person")
    ap.add_argument("--max-frames", type=int, default=40)
    a = ap.parse_args()
    if a.max_frames <= 0:
        ap.error("--max-frames must be positive")
    checkpoints = sorted((Path.home() / ".cache/huggingface/hub/models--facebook--sam3.1/snapshots").glob("*/sam3.1_multiplex.pt"))
    ck = a.checkpoint.expanduser() if a.checkpoint else (checkpoints[-1] if checkpoints else None)
    if ck is None or not ck.is_file():
        ap.error("checkpoint not found; pass --checkpoint /path/to/sam3.1_multiplex.pt")
    vid = a.video.expanduser()
    if not vid.is_file():
        ap.error("video not found; pass --video /path/to/clip.mp4")
    import torch
    if not torch.cuda.is_available():
        raise SystemExit("SAM 3.1 requires a CUDA GPU; none is available in this session")
    from sam3.model_builder import build_sam3_predictor
    t=time.perf_counter()
    p = build_sam3_predictor(checkpoint_path=str(ck), version="sam3.1", use_fa3=False, use_rope_real=True, max_num_objects=4, multiplex_count=16, async_loading_frames=False)
    p.model.detector.backbone.vision_backbone.trunk.bfloat16(); p.model.detector.backbone.language_backbone.encoder.bfloat16()
    p.model.batched_grounding_batch_size=1; p.model.postprocess_batch_size=1
    _i=p.model.init_state
    p.model.init_state=lambda **kw:_i(**{k:v for k,v in kw.items() if k!="offload_state_to_cpu"})
    torch.cuda.synchronize(); print("load s", round(time.perf_counter()-t,1), "VRAM GB", round(torch.cuda.max_memory_allocated()/1e9,2), flush=True)
    sid = p.handle_request({"type":"start_session","resource_path":str(vid),"offload_video_to_cpu":True})["session_id"]
    print("after start_session GB",round(torch.cuda.memory_allocated()/1e9,2),flush=True)
    try:
        p.handle_request({"type":"add_prompt","session_id":sid,"frame_index":0,"text":a.prompt})
        n=0; ts=[]
        t0=time.perf_counter()
        for out in p.handle_stream_request({"type":"propagate_in_video","session_id":sid}):
            torch.cuda.synchronize(); ts.append(time.perf_counter())
            if n==0: print("KEYS", list(out.keys()), flush=True)
            n+=1
            if n in (1,15,30): print("frame",n,"n_objs", len((out.get("outputs") or {}).get("out_obj_ids", [])), flush=True)
            if n >= a.max_frames: break
        d = [b-a for a,b in zip(ts, ts[1:])]
        steady = d[9:]  # skip the first ten frames; report only intervals actually measured
        if ts:
            fps = f"{len(steady) / sum(steady):.2f}" if steady and sum(steady) > 0 else "not enough frames"
            print("first frame s", round(ts[0]-t0, 1), "steady fps (after frame 10)", fps,
                  "peak VRAM GB", round(torch.cuda.max_memory_allocated()/1e9, 2))
        else:
            raise RuntimeError("SAM returned no frames")
    finally:
        p.handle_request({"type": "close_session", "session_id": sid})


if __name__ == "__main__":
    main()

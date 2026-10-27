"""SAM 3.1 smoke test for the 8 GB RTX 5070. Run with ~/src/sam3-venv (see PROGRESS.md).
Run: PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True ~/src/sam3-venv/bin/python -I scripts/sam3_smoke.py
Workarounds for upstream facebookresearch/sam3: bf16 on the two big modules, grounding batch size 1,
drop offload_state_to_cpu from init_state, multiplex_count must stay 16."""
import glob, time, torch
from sam3.model_builder import build_sam3_predictor
ck = glob.glob("/home/liam-o-neill/.cache/huggingface/hub/models--facebook--sam3.1/snapshots/*/sam3.1_multiplex.pt")[0]
vid = "/home/liam-o-neill/src/sam3/assets/videos/bedroom.mp4"
t=time.time()
p = build_sam3_predictor(checkpoint_path=ck, version="sam3.1", use_fa3=False, use_rope_real=True, max_num_objects=4, multiplex_count=16, async_loading_frames=False)
p.model.detector.backbone.vision_backbone.trunk.bfloat16(); p.model.detector.backbone.language_backbone.encoder.bfloat16()
p.model.batched_grounding_batch_size=1; p.model.postprocess_batch_size=1
_i=p.model.init_state
p.model.init_state=lambda **kw:_i(**{k:v for k,v in kw.items() if k!="offload_state_to_cpu"})
torch.cuda.synchronize(); print("load s", round(time.time()-t,1), "VRAM GB", round(torch.cuda.max_memory_allocated()/1e9,2), flush=True)
sid = p.handle_request({"type":"start_session","resource_path":vid,"offload_video_to_cpu":True})["session_id"]
print("after start_session GB",round(torch.cuda.memory_allocated()/1e9,2),flush=True)
p.handle_request({"type":"add_prompt","session_id":sid,"frame_index":0,"text":"person"})
n=0; ts=[]
t0=time.time()
for out in p.handle_stream_request({"type":"propagate_in_video","session_id":sid}):
    torch.cuda.synchronize(); ts.append(time.time())
    if n==0: print("KEYS", list(out.keys()), flush=True)
    n+=1
    if n in (1,15,30): print("frame",n,"n_objs", len(out.get("out_obj_ids",[])), flush=True)
    if n>=40: break
d=[b-a for a,b in zip(ts,ts[1:])]
print("first frame s", round(ts[0]-t0,1), "steady fps (frames 10-40)", round(30/sum(d[9:]),2), "peak VRAM GB", round(torch.cuda.max_memory_allocated()/1e9,2))

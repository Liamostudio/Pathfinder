"""Segment workspace components with the existing local SAM 3.1 runtime.

Run this file with ~/src/sam3-venv/bin/python -I. No service or key is used.
"""
import argparse
import json
from pathlib import Path
import time
import numpy as np
from PIL import Image


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--checkpoint',type=Path);parser.add_argument('--prompts',type=Path)
    parser.add_argument('--robot-prompts',action='store_true',help='Try additional text prompts for the arm')
    args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output directory')
    checkpoints=sorted((Path.home()/'.cache/huggingface/hub/models--facebook--sam3.1/snapshots').glob('*/sam3.1_multiplex.pt'))
    if args.checkpoint:checkpoints=[args.checkpoint]
    if not checkpoints or not checkpoints[-1].is_file():raise RuntimeError('Local SAM 3.1 checkpoint not found')
    import torch
    from sam3.model_builder import build_sam3_predictor
    from sam3.model.sam3_image_processor import Sam3Processor
    torch.set_num_threads(8);started=time.monotonic()
    p=build_sam3_predictor(checkpoint_path=str(checkpoints[-1]),version='sam3.1',use_fa3=False,
                          use_rope_real=True,max_num_objects=4,multiplex_count=16,async_loading_frames=False)
    p.model.tracker.cpu()
    detector=p.model.detector
    detector.backbone.vision_backbone.trunk.bfloat16()
    detector.backbone.language_backbone.encoder.bfloat16()
    processor=Sam3Processor(detector,confidence_threshold=.35)
    args.output.mkdir(parents=True)
    names=[i['name'] for i in json.loads((args.capture/'capture.json').read_text())['images']]
    categories={'table':'table','wall':'wall','robot':'robot arm','tripod':'tripod','chair':'chair'}
    configured=json.loads(args.prompts.read_text()) if args.prompts else None
    stats=[]
    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
        for name in names:
            with Image.open(args.capture/'images'/name) as image:image=image.convert('RGB')
            state=processor.set_image(image);masks={};scores={}
            for category,prompt in categories.items():
                masks[category]=np.zeros((image.height,image.width),bool);attempts=[]
                prompts=configured[category] if configured else [prompt] if category!='robot' or not args.robot_prompts else ['robot','robotic arm','green robot']
                for prompt in prompts:
                    processor.reset_all_prompts(state)
                    result=processor.set_text_prompt(prompt=prompt,state=state)
                    m=result['masks'].detach().cpu().numpy().astype(bool)
                    score=result['scores'].float().detach().cpu().numpy()
                    if len(m):masks[category]|=m.reshape((-1,image.height,image.width)).any(0)
                    attempts.append({'prompt':prompt,'instances':len(m),'scores':score.tolist()})
                scores[category]={'attempts':attempts,'pixels':int(masks[category].sum())}
            np.savez_compressed(args.output/(Path(name).stem+'.npz'),**masks)
            stats.append({'name':name,'categories':scores})
            print(json.dumps(stats[-1]),flush=True)
            del state,result;torch.cuda.empty_cache()
    report={'model':'facebook/sam3.1','checkpoint_revision':checkpoints[-1].parent.name,'prompts':configured or categories,'images':stats,
            'seconds':round(time.monotonic()-started,2),'peak_gpu_mb':round(torch.cuda.max_memory_allocated()/1024**2,1),
            'notes':['Independent per-image semantic masks; no cross-photo instance identity claim.','No manual corrections applied.']}
    (args.output/'report.json').write_text(json.dumps(report,indent=2))


if __name__=='__main__':main()

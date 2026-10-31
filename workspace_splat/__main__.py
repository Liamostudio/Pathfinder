"""python -m workspace_splat {ingest,reconstruct,train,run,serve} ..."""
import argparse
import json
import os
import time
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('ingest','run'):
        p=sub.add_parser(name);p.add_argument('inputs',nargs='+',type=Path);p.add_argument('--output',type=Path,required=True)
        p.add_argument('--sample-fps',type=float,default=2);p.add_argument('--max-frames',type=int,default=160)
        p.add_argument('--max-edge',type=int,default=1280)
        if name=='run':
            p.add_argument('--steps',type=int,default=2500);p.add_argument('--max-gaussians',type=int,default=40000);p.add_argument('--train-edge',type=int,default=640)
    for name in ('reconstruct','train'):
        p=sub.add_parser(name);p.add_argument('capture',type=Path)
        if name=='train':
            p.add_argument('--steps',type=int,default=2500);p.add_argument('--max-gaussians',type=int,default=40000);p.add_argument('--train-edge',type=int,default=640)
    p=sub.add_parser('serve');p.add_argument('--port',type=int,default=8790);p.add_argument('--data',type=Path,default=Path(__file__).with_name('data'))
    args=parser.parse_args()
    if args.command=='serve':
        from .server import serve
        return serve(args.data,args.port)
    capture=getattr(args,'output',getattr(args,'capture',None))
    started=time.time()
    def progress(stage,info):
        payload={'stage':stage,'worker_pid':os.getpid(),'updated_at':time.time(),'started_at':started,**info}
        print(json.dumps(payload,allow_nan=False),flush=True)
        if capture.exists():
            temp=capture/'status.json.tmp';temp.write_text(json.dumps(payload,indent=2));temp.replace(capture/'status.json')
    try:
        if args.command in ('ingest','run'):
            from .ingest import ingest
            progress('ingesting',{'message':'Normalising photographs/video frames'})
            result=ingest(args.inputs,capture,max_edge=args.max_edge,fps=args.sample_fps,max_frames=args.max_frames)
            progress('ingested',{'frames':len(result['images']),'warnings':result['warnings']})
        if args.command in ('reconstruct','run'):
            from .reconstruct import reconstruct
            reconstruct(capture,progress=progress)
        if args.command in ('train','run'):
            from .train import train
            train(capture,steps=args.steps,max_gaussians=args.max_gaussians,max_edge=args.train_edge,progress=progress)
    except Exception as exc:
        progress('failed',{'error':f'{type(exc).__name__}: {exc}'})
        raise

if __name__=='__main__':main()

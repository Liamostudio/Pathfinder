"""One background capture job. Logs are private alongside the dataset."""
import argparse
import os
from pathlib import Path
import json
import time
import tempfile
import shutil
import sys
import signal


def adopt_capture(staged,capture):
    """Publish a normalised dataset; retries replace any interrupted image move."""
    if (capture/'images').exists():shutil.rmtree(capture/'images')
    (staged/'images').replace(capture/'images')
    (staged/'capture.json').replace(capture/'capture.json')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--backend',choices=['local','sam3d-fal'],default='local')
    parser.add_argument('--steps',type=int,default=2500);parser.add_argument('--uploads',action='store_true')
    args=parser.parse_args();capture=args.capture;started=time.time()
    def progress(stage,info):
        payload={'stage':stage,'worker_pid':os.getpid(),'updated_at':time.time(),'started_at':started,**info}
        temp=capture/'status.json.tmp';temp.write_text(json.dumps(payload,indent=2));temp.replace(capture/'status.json')
        print(json.dumps(payload),flush=True)
    try:
        credentials={}
        if args.backend=='sam3d-fal':
            credentials=json.loads(sys.stdin.buffer.read(16385))
            signal.signal(signal.SIGTERM,lambda *_:sys.exit(143))
        if args.uploads:
            from .ingest import ingest
            progress('ingesting',{'message':'Normalising uploaded frames'})
            inputs=[capture/'uploads']
            if args.backend=='sam3d-fal' and credentials.get('image'):
                selected=credentials['image']
                if not isinstance(selected,str) or Path(selected).name!=selected:raise ValueError('Invalid selected image')
                if (capture/'uploads'/selected).is_file():inputs=[capture/'uploads'/selected]
            with tempfile.TemporaryDirectory(dir=capture) as folder:
                ingest(inputs,Path(folder)/'capture',min_images=1 if args.backend=='sam3d-fal' else 2)
                adopt_capture(Path(folder)/'capture',capture)
        if args.backend=='sam3d-fal':
            from .remote import generate
            generate(capture,credentials.get('api_key'),image_name=credentials.get('image',''),prompt=credentials.get('prompt','table'),progress=progress)
            return
        from .reconstruct import reconstruct
        reconstruct(capture,progress=progress)
        from .train import train
        train(capture,steps=args.steps,progress=progress)
    except Exception as exc:
        progress('failed',{'error':f'{type(exc).__name__}: {exc}'})
        raise

if __name__=='__main__':main()

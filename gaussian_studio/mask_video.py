"""Export inspectable SAM masks as a labelled source/overlay video."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
from .stage_video import video_writer,frame_with_label


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path);parser.add_argument('--masks',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    names=[i['name'] for i in json.loads((args.capture/'capture.json').read_text())['images']]
    colours={'wall':(170,110,240),'table':(70,170,250),'chair':(240,80,160),'tripod':(250,160,40),'robot':(110,250,70)}
    encoder=video_writer(args.output,1280,560,24)
    sheet=Image.new('RGB',(1280,560*len(names)))
    try:
        for row,name in enumerate(names):
            with Image.open(args.capture/'images'/name) as image:rgb=np.array(image.convert('RGB'))
            overlay=rgb.copy()
            with np.load(args.masks/(Path(name).stem+'.npz'),allow_pickle=False) as masks:
                for category,colour in colours.items():
                    mask=masks[category];overlay[mask]=np.uint8(.52*overlay[mask]+.48*np.array(colour))
            pair=np.concatenate([np.array(Image.fromarray(a).resize((640,480),Image.Resampling.LANCZOS)) for a in (rgb,overlay)],axis=1)
            frame=frame_with_label(pair,'Stage 2 | SAM 3.1 masks | source photo and semantic overlay',
                f'{name} | table blue / wall purple / robot green / tripod orange / chair pink')
            sheet.paste(frame,(0,row*560))
            for _ in range(48):encoder.stdin.write(frame.tobytes())
        encoder.stdin.close()
        if encoder.wait()!=0:raise RuntimeError('Encoder failed')
    except BaseException:
        encoder.kill();encoder.wait();args.output.unlink(missing_ok=True);raise
    sheet.save(args.output.with_suffix('.jpg'),quality=94)
    print(json.dumps({'video':str(args.output),'frames':48*len(names)}),flush=True)


if __name__=='__main__':main()

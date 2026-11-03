"""CPU stage entry point, run in the existing image-processing runtime."""
import argparse
from pathlib import Path
from .repository import read_json

def main():
    parser=argparse.ArgumentParser();parser.add_argument('request',type=Path);parser.add_argument('--mask-edits',action='store_true');parser.add_argument('--overlays',action='store_true');a=parser.parse_args();r=read_json(a.request)
    if a.overlays:
        from .geometry import mask_overlays
        return mask_overlays(Path(r['capture']),Path(r['masks']),Path(r['masks']))
    if a.mask_edits:
        from .geometry import apply_masks
        return apply_masks(Path(r['directory']),r['edits'])
    from workspace_splat.ingest import ingest
    s=r['settings'];ingest(r['sources'],r['output'],max_edge=s['max_edge'],fps=s['video_fps'],max_frames=s['video_frames'],all_images=True)

if __name__=='__main__':main()

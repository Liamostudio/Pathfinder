"""Pinned local model registry and bounded subprocess preflight; no server torch imports."""
import json
import os
from pathlib import Path
import subprocess
from .schema import MAP_REVISION,MAP_SOURCE,SAM_REVISION,PIPELINE
ROOT=Path(__file__).resolve().parents[2]

class Runtimes:
    def __init__(self,config=None):
        config=config or {}
        self.paths={name:str(Path(config.get(name,os.environ.get('PATHFINDER_'+name.upper(),default))).expanduser().absolute()) for name,default in {
            'geometry_python':ROOT/'gaussian_studio/.depth-venv/bin/python',
            'sam_python':Path.home()/'src/sam3-venv/bin/python',
            'gaussian_python':ROOT/'workspace_splat/.venv/bin/python'}.items()}
        self.map_source=ROOT/'gaussian_studio/.cache'/('map-anything-'+MAP_SOURCE)
        self.map_checkpoint=ROOT/'gaussian_studio/.cache/huggingface/models--facebook--map-anything-apache/snapshots'/MAP_REVISION
        self.sam_checkpoint=Path.home()/'.cache/huggingface/hub/models--facebook--sam3.1/snapshots'/SAM_REVISION/'sam3.1_multiplex.pt'
        self.dino_cache=Path.home()/'.cache/torch/hub/facebookresearch_dinov2_main'

    def check(self):
        issues=[];runtimes={}
        for name,path in self.paths.items():
            exists=Path(path).is_file();runtimes[name]={'available':exists,'setting':'PATHFINDER_'+name.upper()}
            if not exists:issues.append(f'Missing {name}; configure PATHFINDER_{name.upper()} with the local interpreter path.')
        for name,path in [('MapAnything weights',self.map_checkpoint/'model.safetensors'),('MapAnything source',self.map_source/'mapanything'),('SAM 3.1 weights',self.sam_checkpoint),('DINO Torch Hub source',self.dino_cache/'hubconf.py')]:
            # Upstream checkpoints may use pytorch_model.bin rather than safetensors.
            exists=path.exists() or (name=='MapAnything weights' and any(self.map_checkpoint.glob('*.bin')))
            if not exists:issues.append(f'{name} missing from the pinned local cache. Restore the tested cached runtime; setup will not download models.')
        gpu=None
        if runtimes['geometry_python']['available']:
            try:
                probe=subprocess.run([self.paths['geometry_python'],'-c','import torch,json,importlib.util; print(json.dumps({"cuda":torch.cuda.is_available(),"gpu":torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,"surface_export":bool(importlib.util.find_spec("trimesh"))}))'],capture_output=True,text=True,timeout=20)
                gpu=json.loads(probe.stdout.strip().splitlines()[-1])
                if not gpu.get('surface_export'):issues.append('Install the existing surface-export requirements in the geometry runtime (trimesh is missing).')
                if not gpu['cuda']:issues.append('CUDA is unavailable to the geometry runtime. Start Pathfinder in the local GPU session.')
            except (OSError,ValueError,IndexError,subprocess.TimeoutExpired):issues.append('Could not inspect CUDA in the geometry runtime. Check the isolated environment.')
        return {'pipeline_version':PIPELINE,'runtimes':runtimes,'gpu':gpu,'issues':issues,'available':not issues,
                'max_joint_views':4,'offline':True,'models':{'geometry':{'id':'facebook/map-anything-apache','revision':MAP_REVISION,'source_revision':MAP_SOURCE},'segmentation':{'id':'facebook/sam3.1','revision':SAM_REVISION}},
                'fixture_available':(ROOT/'gaussian_studio/data/meta-surfaces-textured-20261101/output/scene.json').is_file()}

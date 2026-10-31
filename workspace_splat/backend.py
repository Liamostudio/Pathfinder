"""Configure pip CUDA and local headers before loading gsplat's CUDA extension."""
import inspect
import os
from pathlib import Path
import site
import sys
import tempfile


def configure_cuda():
    os.environ['PATH']=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH']
    os.environ.setdefault('MAX_JOBS','2')
    os.environ.setdefault('TORCH_EXTENSIONS_DIR',str(Path(tempfile.gettempdir())/'pathfinder-splat-extensions'))
    version=f'python{sys.version_info.major}.{sys.version_info.minor}'
    candidates=[Path(p)/'nvidia/cu13' for p in site.getsitepackages()]
    candidates.append(Path(__file__).resolve().parents[1]/f'.venv/lib/{version}/site-packages/nvidia/cu13')
    if not os.environ.get('CUDA_HOME') and any((p/'bin/nvcc').is_file() for p in candidates):
        merged=Path(__file__).with_name('.cuda');merged.mkdir(exist_ok=True)
        for base in candidates:
            if not base.exists():continue
            for directory in ('bin','include','lib','lib64','nvvm'):
                source=base/directory
                if not source.exists():continue
                dest=merged/directory;dest.mkdir(exist_ok=True)
                for entry in source.iterdir():
                    target=dest/entry.name
                    if not target.exists():target.symlink_to(entry,target_is_directory=entry.is_dir())
        if not (merged/'lib64').exists() and (merged/'lib').exists():
            (merged/'lib64').symlink_to(merged/'lib',target_is_directory=True)
        for name in ('lib','lib64'):
            library=merged/name/'libcudart.so'
            if not library.exists() and (merged/name/'libcudart.so.13').exists():library.symlink_to('libcudart.so.13')
        link=Path(tempfile.gettempdir())/f'pathfinder-splat-cuda-{os.getuid()}'
        if not link.exists():link.symlink_to(merged.resolve(),target_is_directory=True)
        os.environ['CUDA_HOME']=str(link)
        os.environ['PATH']=str(link/'bin')+os.pathsep+os.environ['PATH']
    headers=Path(__file__).with_name('.cache')/'python-dev/extracted'
    if (headers/f'usr/include/{version}/Python.h').exists():
        link=Path(tempfile.gettempdir())/f'pathfinder-python-dev-{os.getuid()}'
        if not link.exists():link.symlink_to(headers.resolve(),target_is_directory=True)
        os.environ['CPATH']=os.pathsep.join([str(link/f'usr/include/{version}'),str(link/'usr/include'),os.environ.get('CPATH','')])
    import torch
    import torch.utils.cpp_extension as extension
    if torch.cuda.is_available():
        major,minor=torch.cuda.get_device_capability()
        os.environ.setdefault('TORCH_CUDA_ARCH_LIST',f'{major}.{minor}')
    # PyTorch 2.14 leaves library search paths unquoted; this repo path has a space.
    library=Path(torch.__file__).parent/'lib'
    link=Path(tempfile.gettempdir())/f'pathfinder-torch-lib-{os.getuid()}'
    if not link.exists():link.symlink_to(library.resolve(),target_is_directory=True)
    extension.TORCH_LIB_PATH=str(link)
    # gsplat 1.5 calls the pre-SYCL positional signature. Adapt without editing the package.
    if 'with_sycl' in inspect.signature(extension._jit_compile).parameters:
        original=extension._jit_compile
        def compatible_jit(*args,**kwargs):
            if len(args)==8:args=(*args[:4],None,*args[4:])
            kwargs.setdefault('with_sycl',None)
            return original(*args,**kwargs)
        extension._jit_compile=compatible_jit

#!/usr/bin/env python3
"""Install the optional scan runtime without changing the project's core dependencies."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cuda-compiler',action='store_true',help='Install pip CUDA 13 compiler components')
    parser.add_argument('--local-python-headers',action='store_true',help='Unpack matching Ubuntu/Debian Python headers locally; no sudo')
    args=parser.parse_args()
    environment=ROOT/'.venv'
    if not (environment/'bin/python').exists():venv.EnvBuilder(with_pip=True).create(environment)
    python=environment/'bin/python'
    version=f'python{sys.version_info.major}.{sys.version_info.minor}'
    project_site=ROOT.parent/f'.venv/lib/{version}/site-packages'
    if (project_site/'torch').is_dir():
        site_path=environment/f'lib/{version}/site-packages';site_path.mkdir(parents=True,exist_ok=True)
        (site_path/'project-torch.pth').write_text(str(project_site)+'\n')
    subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True)
    if args.cuda_compiler:
        subprocess.run([str(python),'-m','pip','install','cuda-toolkit[nvcc,cudart,cccl]==13.0.3'],check=True)
    if args.local_python_headers:
        if not shutil.which('apt') or not shutil.which('dpkg-deb'):raise SystemExit('Local header download requires apt and dpkg-deb. Install your interpreter’s development headers instead.')
        cache=ROOT/'.cache/python-dev';cache.mkdir(parents=True,exist_ok=True)
        subprocess.run(['apt','download',f'lib{version}-dev'],cwd=cache,check=True)
        packages=sorted(cache.glob(f'lib{version}-dev_*.deb'),key=lambda p:p.stat().st_mtime)
        subprocess.run(['dpkg-deb','-x',str(packages[-1]),str(cache/'extracted')],check=True)
    print(f'Ready. Start with: "{python}" "{ROOT / "start.py"}"')

if __name__=='__main__':main()

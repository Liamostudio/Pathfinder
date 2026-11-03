#!/usr/bin/env python3
"""Install a per-user application-menu entry and a checkout-local launcher."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
APP_ID='io.pathfinder.SplatStudio'

def exec_quote(value):
    # Desktop Entry Exec uses its own quoting rules, rather than shell quoting.
    value=str(value).replace('%','%%')
    for character in ('\\','"','`','$'):value=value.replace(character,'\\'+character)
    return '"'+value+'"'

def desktop_entry(root=ROOT,python='/usr/bin/python3'):
    root=Path(root).resolve()
    return '\n'.join([
        '[Desktop Entry]','Version=1.0','Type=Application','Name=Splat Studio',
        'Comment=Create and explore Gaussian splats from photographs and video',
        f'Exec={exec_quote(python)} {exec_quote(root/"start.py")}',
        f'Path={root.parent}',f'Icon={root/"assets/splat-studio.svg"}',
        'Terminal=false','StartupNotify=true',f'StartupWMClass={APP_ID}',
        'Categories=Graphics;3DGraphics;','Keywords=Gaussian;Splat;3D;Reconstruction;',
        f'X-Splat-Studio-Checkout={root}','',
    ])

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--applications-dir',type=Path)
    args=parser.parse_args()
    directory=args.applications_dir or Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'applications'
    directory.mkdir(parents=True,exist_ok=True)
    text=desktop_entry();installed=directory/(APP_ID+'.desktop')
    for path in (installed,ROOT/'Splat Studio.desktop'):
        if path.exists() and f'X-Splat-Studio-Checkout={ROOT}' not in path.read_text():
            raise SystemExit(f'A different launcher already exists at {path}; it was not changed.')
    for path in (installed,ROOT/'Splat Studio.desktop'):
        path.write_text(text);path.chmod(0o755)
    update=shutil.which('update-desktop-database')
    if update:subprocess.run([update,str(directory)],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    print('Installed Splat Studio in your application menu.')
    print('Local launcher: '+str(ROOT/'Splat Studio.desktop'))

if __name__=='__main__':main()

#!/usr/bin/env python3
"""Launch the native desktop app. Browser serving is an explicit optional mode."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import shutil
import sys

ROOT=Path(__file__).resolve().parent

def desktop_python():
    candidates=list(dict.fromkeys(['/usr/bin/python3',sys.executable,shutil.which('python3')]))
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():continue
        check=subprocess.run([candidate,'-c',"import gi; gi.require_version('Gtk','3.0'); gi.require_version('WebKit2','4.1'); from gi.repository import Gtk, WebKit2"],
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        if check.returncode==0:return candidate
    raise SystemExit('GTK desktop bindings are missing. Install python3-gi and gir1.2-webkit2-4.1 for the system Python, then launch again.')

def launch_desktop(data,port=0):
    python=desktop_python()
    with (ROOT/'desktop.log').open('ab') as log:
        subprocess.Popen([python,str(ROOT/'desktop.py'),'--data',str(Path(data).resolve()),'--port',str(port)],
                         cwd=ROOT.parent,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    print('Opened Splat Studio desktop application.')

def launch_browser_service(data,port=8791):
    url=f'http://127.0.0.1:{port}'
    def ready():
        try:
            with urllib.request.urlopen(url+'/api/health',timeout=2) as response:
                return json.load(response).get('app')=='splat-studio'
        except (OSError,ValueError):return False
    if ready():print('Splat Studio is running: '+url);return
    python=ROOT.parent/'workspace_splat/.venv/bin/python'
    if not python.is_file():raise SystemExit('Install the Gaussian runtime first: python3 workspace_splat/bootstrap.py --cuda-compiler --local-python-headers')
    with (ROOT/'server.log').open('ab') as log:
        process=subprocess.Popen([str(python),'-u','-m','gaussian_studio','--serve','--port',str(port),'--data',str(Path(data).resolve())],
                                 cwd=ROOT.parent,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    for _ in range(100):
        if ready():
            (ROOT/'server.pid').write_text(str(process.pid)+'\n');print('Splat Studio: '+url);return
        if process.poll() is not None:raise SystemExit('Splat Studio exited; see gaussian_studio/server.log')
        time.sleep(.2)
    process.terminate();raise SystemExit('Startup failed; see gaussian_studio/server.log')

def main():
    parser=argparse.ArgumentParser(description='Open Splat Studio in its own desktop window')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    parser.add_argument('--browser',action='store_true',help='Optional: start a browser-accessible service instead of the desktop app')
    args=parser.parse_args()
    if args.browser:return launch_browser_service(args.data,args.port or 8791)
    return launch_desktop(args.data,args.port)

if __name__=='__main__':main()

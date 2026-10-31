#!/usr/bin/env python3
"""Start the Gaussian workspace lab independently of a chat/terminal session."""
from pathlib import Path
import json
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parent
URL='http://127.0.0.1:8790'

def ready():
    try:
        with urllib.request.urlopen(URL+'/api/captures',timeout=2) as response:return isinstance(json.load(response),list)
    except (OSError,ValueError):return False

def main():
    if ready():print('Workspace lab already running: '+URL);return
    python=ROOT/'.venv/bin/python'
    if not python.is_file():raise SystemExit('Install workspace_splat/requirements.txt in workspace_splat/.venv first.')
    with (ROOT/'server.log').open('ab') as log:
        process=subprocess.Popen([str(python),'-u','-m','workspace_splat','serve'],cwd=ROOT.parent,
                                 stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
    for _ in range(100):
        if ready():
            (ROOT/'server.pid').write_text(str(process.pid)+'\n');print('Workspace Gaussian lab: '+URL);return
        if process.poll() is not None:raise SystemExit('Workspace lab exited; see workspace_splat/server.log')
        time.sleep(.2)
    process.terminate();raise SystemExit('Workspace lab failed its startup health check')

if __name__=='__main__':main()

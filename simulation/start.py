#!/usr/bin/env python3
"""Start the local simulator independently of the invoking terminal/chat session."""
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import json

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8766'


def ready():
    try:
        with urllib.request.urlopen(URL + '/api/state', timeout=2) as response:
            state = json.load(response)
            return state.get('mode') == 'simulation' and state.get('robot') == 'SO-101'
    except (OSError, ValueError):
        return False


def main():
    if ready():
        print(f'Simulation already running: {URL}')
        return
    python = ROOT / '.venv/bin/python'
    if not python.exists():
        raise SystemExit('Install this folder’s requirements into .venv first; see README.md.')
    log_path = ROOT / 'server.log'
    with log_path.open('ab') as log:
        child = subprocess.Popen([str(python), '-u', str(ROOT / 'run.py')],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if ready():
            (ROOT / 'server.pid').write_text(str(child.pid) + '\n')
            print(f'Simulation running independently: {URL}\nPID: {child.pid}\nLog: {log_path}')
            return
        if child.poll() is not None:
            raise SystemExit(f'Simulator exited ({child.returncode}); see {log_path}')
        time.sleep(.2)
    child.terminate()
    raise SystemExit(f'Simulator did not start within 20 seconds; see {log_path}')


if __name__ == '__main__':
    main()

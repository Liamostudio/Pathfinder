"""One advisory GPU lease shared by local app features and reconstruction workers."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'recordings/.gpu.lock'

@contextmanager
def gpu_lease(owner, *, cancelled=lambda: False, waiting=lambda: None):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a+') as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if cancelled():raise InterruptedError('Cancelled while waiting for the GPU')
                waiting();time.sleep(.25)
        try:
            handle.seek(0);handle.truncate()
            json.dump({'pid':os.getpid(),'owner':owner},handle);handle.flush()
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

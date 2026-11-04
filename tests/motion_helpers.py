"""Helpers for tests of a motion that waits for a hand to move away."""
import threading
import time


def run_in_thread(fn):
    out = []
    thread = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    thread.start()
    return thread, out


def waits_then_cancel(tools, fn, delay=0.3):
    """Run `fn` (a pick or place) on a thread, check it is still WAITING after `delay` (it did not give up), then cancel and return its result."""
    thread, out = run_in_thread(fn)
    time.sleep(delay)
    assert thread.is_alive(), f"gave up instead of waiting: {out}"
    tools.cancel()
    thread.join(3)
    assert not thread.is_alive(), "still running after cancel"
    return out[0]

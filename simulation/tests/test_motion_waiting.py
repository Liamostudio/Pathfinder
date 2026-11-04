"""Task 1 (docs/motion-tasks-2026-11-04.md), simulator side: a pause at zero speed is not a stalled move; a stall still times out; cancel does not latch."""
import threading
import time

import numpy as np
import pytest

from sim_env.engine import Simulation


@pytest.fixture
def sim():
    s = Simulation(realtime=True)
    yield s
    s.close()


def small_step(sim):
    q = sim.data.qpos[sim.qids].copy()
    q[0] += 0.1
    return q


def run(sim, values, timeout):
    out = []
    thread = threading.Thread(target=lambda: out.append(sim._move_joints(values, 1.0, timeout=timeout)), daemon=True)
    thread.start()
    return thread, out


def test_time_at_zero_speed_does_not_count_towards_the_move_timeout(sim):
    sim.set_speed_scale(0)                                   # the hand watchdog holds the arm
    thread, out = run(sim, small_step(sim), timeout=0.5)
    time.sleep(1.3)                                          # well past the timeout
    assert thread.is_alive() and not out
    sim.set_speed_scale(1)                                   # the hand leaves
    thread.join(10)
    assert out == [True] and not sim.halted and not sim.safety.stopped


def test_a_move_that_makes_no_progress_while_allowed_still_times_out(sim):
    sim.faults.add('arm_stall')                              # a real fault: allowed to move, does not
    started = time.monotonic()
    thread, out = run(sim, small_step(sim), timeout=0.5)
    thread.join(5)
    assert out == [False] and time.monotonic() - started < 3 and sim.reason == 'command timed out'


def test_cancel_ends_a_paused_move_without_latching(sim):
    sim.set_speed_scale(0)
    thread, out = run(sim, small_step(sim), timeout=5)
    time.sleep(0.3)
    sim.cancel()
    thread.join(3)
    assert out == [False] and not sim.halted and not sim.safety.stopped and sim.reason == 'cancelled'
    sim.set_speed_scale(1)
    assert sim._move_joints(small_step(sim), 1.0, timeout=5) is True      # still usable straight away


def test_the_simulator_stops_at_ten_centimetres(sim):
    assert sim.safety.stop_distance_m == 0.10 and sim.simulated

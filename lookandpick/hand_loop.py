"""Benchmark hand detection or run a recorded clip through the SIMULATED watchdog.

python -m lookandpick.hand_loop --video clip.mp4 --model weights/hand_landmarker.task
Add --table-calibration table.json --hand-height 0.08 to run the calibrated sim gate.
Never opens a camera, serial port or real arm. Model download is explicit and separate.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import time


def benchmark(source, detector, max_frames):
    timings, hand_frames = [], 0
    frames = source.frames()
    try:
        for i, (_, frame) in enumerate(frames):
            started = time.perf_counter()
            detections = detector.detect(frame, i * 34)
            timings.append((time.perf_counter() - started) * 1000)
            hand_frames += bool(detections)
            if len(timings) >= max_frames:
                break
    finally:
        frames.close()
        detector.close()
    if not timings:
        raise RuntimeError('No frames decoded')
    measured = timings[5:] or timings
    return {'mode':'recorded detector benchmark; no arm', 'frames':len(timings),
            'frames_with_hands':hand_frames, 'warmup_frames':min(5,len(timings)-len(measured)),
            'first_ms':round(timings[0],2), 'mean_ms':round(statistics.mean(measured),2),
            'p95_ms':round(sorted(measured)[math.ceil(.95*len(measured))-1],2),
            'inference_fps':round(1000/statistics.mean(measured),2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--max-frames', type=int, default=120)
    parser.add_argument('--table-calibration', type=Path)
    parser.add_argument('--hand-height', type=float)
    parser.add_argument('--hz', type=float, default=30)
    parser.add_argument('--timeout-ms', type=float, default=150)
    args = parser.parse_args()
    if args.max_frames <= 0 or not math.isfinite(args.hz) or not 1 <= args.hz <= 30 or not math.isfinite(args.timeout_ms) or not 0 < args.timeout_ms <= 1000:
        parser.error('Invalid frame count, rate or timeout')
    if args.table_calibration and (args.hand_height is None or not math.isfinite(args.hand_height) or args.hand_height < 0):
        parser.error('Calibrated simulation requires an explicit, nonnegative --hand-height in metres')
    from .adapters.camera import ReplayCamera
    from .adapters.hands import MediaPipeHandDetector
    source = ReplayCamera(args.video)
    if not args.table_calibration:
        detector = MediaPipeHandDetector(args.model)
        print(json.dumps(benchmark(source,detector,args.max_frames),indent=2))
        return
    import numpy as np
    from .calibration import TableCalibration
    from .adapters.fake import SimArm
    from .hand_safety import HandDetectionLoop, HandSafetyMonitor
    from .safety import Safety
    from .world import WorldState
    data = json.loads(args.table_calibration.read_text())
    calibration = TableCalibration(np.array(data['camera_matrix']),np.array(data['rvec']),np.array(data['tvec']),
                                   None if data.get('dist') is None else np.array(data['dist']))
    detector = MediaPipeHandDetector(args.model)

    class LimitedSource:
        def frames(self):
            frames = source.frames()
            try:
                for i, item in enumerate(frames):
                    yield item
                    if i + 1 >= args.max_frames:
                        break
            finally:
                frames.close()

    arm = SimArm()
    monitor = HandSafetyMonitor(WorldState(),Safety(),arm,lambda: (arm.position,(.25,.1,0)),
                                timeout_s=args.timeout_ms/1000)
    loop = HandDetectionLoop(LimitedSource(),detector,calibration,monitor,hand_height=args.hand_height,target_hz=args.hz)
    try:
        loop.start()
        while loop._thread.is_alive():
            print(json.dumps(monitor.snapshot()),flush=True)
            time.sleep(.2)
        print(json.dumps({'mode':'simulation only','frames':loop.frames_processed,**monitor.snapshot()},indent=2))
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == '__main__':
    main()

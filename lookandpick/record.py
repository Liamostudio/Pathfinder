"""Record from a live camera: python -m lookandpick.record <index> <name> [seconds]  (files go to recordings/)."""
import argparse
import math
import time

from .adapters.camera import LiveCamera, Recorder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', type=int)
    parser.add_argument('name')
    parser.add_argument('seconds', type=float, nargs='?', default=20.0)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('seconds must be positive and finite')
    rec = Recorder("recordings", args.name)
    frames = LiveCamera(args.index, args.name).frames()
    end = time.monotonic() + args.seconds
    try:
        for t, frame in frames:
            rec.write(t, frame)
            if time.monotonic() >= end:
                break
    finally:
        frames.close()
        rec.close()
    if not rec.frame_count:
        raise RuntimeError('No frames recorded')
    print(f"saved {rec.path}")


if __name__ == "__main__":
    main()

"""Record from a live camera: python -m lookandpick.record <index> <name> [seconds]  (files go to recordings/)."""
import sys
import time

from .adapters.camera import LiveCamera, Recorder


def main() -> None:
    index, name = int(sys.argv[1]), sys.argv[2]
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
    rec, end = Recorder("recordings", name), time.time() + seconds
    for t, frame in LiveCamera(index, name).frames():
        rec.write(t, frame)
        if t >= end:
            break
    rec.close()
    print(f"saved {rec.path}")


if __name__ == "__main__":
    main()

"""List serial-port paths, or explicitly read SO-101 ID/voltage/temperature registers.

Default opens nothing. --port opens only that port for reads; no motion/configuration writes.
Run only at the bench. Example: python scripts/so101_check.py --port /dev/ttyACM0
"""
import argparse
import glob
import json

from lookandpick.adapters.so101_health import ReadOnlySO101Health


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port')
    args = parser.parse_args()
    if not args.port:
        print(json.dumps({'ports': sorted(set(glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*'))),
                          'opened': False, 'note': 'No port opened. Pass --port explicitly for read-only health.'}, indent=2))
        return
    reader = ReadOnlySO101Health(args.port)
    try:
        reader.open()
        rows = reader.read()
        print(json.dumps({'port':args.port, 'read_only':True, 'motors':rows}, indent=2))
        if any(row['errors'] for row in rows):
            raise SystemExit(1)
    finally:
        reader.close()


if __name__ == '__main__':
    main()

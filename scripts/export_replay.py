#!/usr/bin/env python3
"""Export a privacy-filtered, backend-free Deck take (standard library only)."""
import argparse
import ipaddress
import json
import math
from pathlib import Path
import re
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 32 * 1024 * 1024
PRIVATE_KEY = re.compile(r'key|token|secret|password|authorization|cookie|image|photo|audio|video|base64|path|filename|email|phone', re.I)
PRIVATE_TEXT = re.compile(r'(?:sk[-_][\w-]{8,}|Bearer\s+\S+|-----BEGIN|data:|https?://|file:|[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:/|[A-Z]:\\)(?:home|Users|tmp|etc|var|private|mnt|media)[/\\]|(?:api[_ -]?key|token|secret|password)[\"\']?\s*[:=]|\b[A-Za-z0-9_+/=-]{40,}\b|(?:\+\d[\d ()-]{9,}\d))', re.I)


def number(value, where, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{where}: expected a finite number')
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f'{where}: number out of range')
    return round(value, 3)


def text(value, where):
    if not isinstance(value, str) or len(value) > 16000:
        raise ValueError(f'{where}: expected text, at most 16000 characters')
    if PRIVATE_TEXT.search(value):
        raise ValueError(f'{where}: possible private data; remove it before exporting')
    # Reject all absolute file paths, including arbitrary Unix/Windows locations.
    if re.search(r'(?<![\w])(?:/[\w.-]+/[^\s"<>]+|[A-Za-z]:[\\/]|~/|\\\\)', value):
        raise ValueError(f'{where}: file path refused')
    return re.sub(r'(?<![\w.])[-+]?\d+\.\d{4,}(?:[eE][+-]?\d+)?',
                  lambda match: str(number(float(match.group()), where)), value)


def clean(value, where='content', depth=0):
    """Keep structured call text, removing private fields before serialisation."""
    if depth > 15:
        raise ValueError(f'{where}: nested too deeply')
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return number(value, where)
    if isinstance(value, str):
        return text(value, where)
    if isinstance(value, list):
        return [clean(v, where, depth+1) for v in value]
    if isinstance(value, dict):
        return {text(k, where): clean(v, where, depth+1) for k, v in value.items() if not PRIVATE_KEY.search(k)}
    raise ValueError(f'{where}: invalid value')


def obj(value, where):
    if not isinstance(value, dict):
        raise ValueError(f'{where}: expected an object')
    return value


def array(value, where):
    if not isinstance(value, list):
        raise ValueError(f'{where}: expected an array')
    return value


def vec(value, where):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f'{where}: expected three coordinates')
    return [number(v, where, -100, 100) for v in value]


def export_take(raw):
    raw = obj(raw, 'take')
    if raw.get('schema_version') != 1 or isinstance(raw.get('schema_version'), bool):
        raise ValueError('schema_version: expected 1')
    frames = array(raw.get('frames'), 'frames')
    if not frames:
        raise ValueError('frames: at least one frame is required')
    out = {'schema_version': 1, 'mode': text(raw.get('mode', ''), 'mode'),
           'title': text(raw.get('title', 'Recorded simulation run'), 'title'), 'frames': [], 'events': [],
           'limitations': [text(v, 'limitations') for v in array(raw.get('limitations', []), 'limitations')]}
    if out['mode'].lower() not in ('sim', 'simulation', 'simulation replay'):
        raise ValueError('mode: only simulation takes may use this player’s simulation banner')
    previous = -1
    for i, f in enumerate(frames):
        obj(f, f'frame {i}')
        t = number(f.get('t'), f'frame {i}.t', 0)
        if t < previous:
            raise ValueError('frames: timestamps must be ordered')
        previous = t
        scene = obj(f.get('scene'), f'frame {i}.scene')
        s = {'arm_position': vec(scene.get('arm_position'), 'arm_position'), 'objects': [], 'hands': []}
        for kind in ('objects', 'hands'):
            for item in array(scene.get(kind, []), kind):
                obj(item, kind)
                entry = {'id': text(str(item.get('id', '')), 'id'), 'position': vec(item.get('position'), kind)}
                if kind == 'objects':
                    if not isinstance(item.get('held', False), bool):
                        raise ValueError('held: expected a boolean')
                    entry.update(label=text(item.get('label', 'Object'), 'label'), held=item.get('held', False))
                else:
                    entry['radius_m'] = number(item.get('radius_m', .06), 'radius_m', 0, 10)
                s[kind].append(entry)
        gate = obj(f.get('hand_gate') or {}, 'hand_gate')
        g = {'state': text(gate.get('state', 'unknown'), 'gate state')}
        for key in ('scale', 'nearest_m', 'hands', 'fps'):
            if gate.get(key) is not None:
                g[key] = number(gate[key], key, 0, 1 if key == 'scale' else None)
        if not isinstance(f.get('stopped', False), bool):
            raise ValueError('stopped: expected a boolean')
        frame = {'t': t, 'scene': s, 'hand_gate': g, 'stopped': f.get('stopped', False),
                 'speed_scale': number(f.get('speed_scale', 1), 'speed_scale', 0, 1), 'joints': {}}
        for name, j in obj(f.get('joints', {}), 'joints').items():
            if name in ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper'):
                frame['joints'][name] = {'position': number(obj(j, name).get('position'), name)}
        if f.get('stop'):
            stop = obj(f['stop'], 'stop')
            kind = stop.get('kind', 'unknown')
            if kind not in ('person', 'fault', 'unknown'):
                raise ValueError('stop.kind: expected person, fault or unknown')
            frame['stop'] = {'kind': kind, 'reason': text(stop.get('reason', ''), 'stop reason'),
                             'recovering_in_s': None if stop.get('recovering_in_s') is None else number(stop['recovering_in_s'], 'recovery', 0),
                             'resets_left': number(stop.get('resets_left', 0), 'resets_left', 0)}
        if f.get('route'):
            route = obj(f['route'], 'route')
            frame['route'] = {'kind': text(route.get('kind', ''), 'route kind'),
                              'waypoints': [vec(p, 'route waypoint') for p in array(route.get('waypoints', []), 'waypoints')]}
        # Enforce >=50 ms spacing after rounding; retain safety changes in the next eligible frame.
        if not out['frames'] or t - out['frames'][-1]['t'] >= .05 - 1e-9:
            out['frames'].append(frame)
    for e in array(raw.get('events', []), 'events'):
        obj(e, 'event')
        out['events'].append({'t': number(e.get('t'), 'event.t', 0), 'source': text(e.get('source', 'Unknown'), 'source'),
                              'input': clean(e.get('input', ''), 'event input'), 'output': clean(e.get('output', ''), 'event output'),
                              'status': text(e.get('status', 'ok'), 'status'),
                              'latency_ms': number(e.get('latency_ms', 0), 'latency_ms', 0),
                              'cost_usd': number(e.get('cost_usd') or 0, 'cost_usd', 0)})
    out['events'].sort(key=lambda e: e['t'])
    if out['events'] and out['events'][-1]['t'] > out['frames'][-1]['t'] + .051:
        raise ValueError('events: timestamp beyond recorded frames')
    return out


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Deck redirects are refused')


def read_deck(url):
    p = urllib.parse.urlsplit(url)
    try:
        loopback = ipaddress.ip_address(p.hostname or '').is_loopback
    except ValueError:
        loopback = p.hostname == 'localhost'
    if not loopback or p.scheme != 'http' or p.username or p.password or p.query or p.fragment or p.path not in ('', '/', '/api/replay'):
        raise ValueError('--deck-url must be a loopback HTTP Deck URL, without credentials')
    if p.port is None or p.port < 8780:
        raise ValueError('--deck-url must use a test port of 8780 or above')
    target = urllib.parse.urlunsplit((p.scheme, p.netloc, '/api/replay', '', ''))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(target, timeout=15) as response:
        data = response.read(MAX_BYTES+1)
    if len(data) > MAX_BYTES:
        raise ValueError('take exceeds 32 MiB')
    return json.loads(data)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('take', nargs='?', type=Path)
    source.add_argument('--deck-url')
    p.add_argument('--output', type=Path, default=ROOT/'replay_site/data/take.json')
    args = p.parse_args()
    try:
        if args.take and args.take.stat().st_size > MAX_BYTES:
            raise ValueError('take exceeds 32 MiB')
        raw = json.loads(args.take.read_text()) if args.take else read_deck(args.deck_url)
        data = export_take(raw)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Complete validation before touching the destination.
        temp = args.output.with_suffix('.json.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n')
        temp.replace(args.output)
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as exc:
        p.exit(2, f'Export refused: {exc}\n')
    models = sorted({e['source'] for e in data['events'] if any(n in e['source'].lower() for n in ('muse', 'sam', 'model', 'glimmer'))})
    duration = data['frames'][-1]['t'] - data['frames'][0]['t']
    print(f"{duration:.3f}s · {len(data['frames'])} frames · {len(data['events'])} events · models: {', '.join(models) or 'none'} · total ${sum(e['cost_usd'] for e in data['events']):.3f}")


if __name__ == '__main__':
    main()

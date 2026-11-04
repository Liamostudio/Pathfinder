"""All inputs are fabricated; no cameras, model API or hardware."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('export_replay', ROOT/'scripts/export_replay.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def take():
    return {'schema_version': 1, 'mode': 'simulation replay', 'title': 'Fake take',
            'frames': [{'t': i/25, 'scene': {'arm_position': [0, .123456, .1], 'objects': [], 'hands': []},
                        'hand_gate': {'state': 'clear', 'scale': 1}, 'speed_scale': 1} for i in range(26)],
            'events': [{'t': .5, 'source': 'Scripted model / no API', 'input': 'pick red cube',
                        'output': 'Done', 'latency_ms': 0, 'cost_usd': 0}], 'limitations': ['Fake data only']}


class ExportTests(unittest.TestCase):
    def test_fake_take_rounding_and_rate(self):
        data = exporter.export_take(take())
        self.assertEqual(data['frames'][0]['scene']['arm_position'][1], .123)
        self.assertTrue(all(b['t']-a['t'] >= .05-1e-9 for a, b in zip(data['frames'], data['frames'][1:])))
        self.assertEqual(data['events'][0]['source'], 'Scripted model / no API')

    def test_tool_call_text_numbers_are_rounded(self):
        raw = take()
        raw['events'][0]['input'] = 'place({"loc": [0.123456, -0.987654, 0.02]})'
        self.assertEqual(exporter.export_take(raw)['events'][0]['input'], 'place({"loc": [0.123, -0.988, 0.02]})')

    def test_discarded_frames_are_still_validated(self):
        raw = take()
        raw['frames'][1]['scene']['objects'] = [{'id':'x','label':'sk-planted-fake-secret','position':[0,0,0]}]
        with self.assertRaisesRegex(ValueError, 'private data'):
            exporter.export_take(raw)

    def test_secret_fields_stripped(self):
        raw = take()
        raw['api_key'] = 'sk-fake-do-not-publish-123456'
        raw['frames'][0]['scene']['image_bytes'] = 'private'
        raw['events'][0]['output'] = {'answer': 'Done', 'pairing_token': 'fake-token'}
        result = json.dumps(exporter.export_take(raw))
        self.assertNotIn('private', result)
        self.assertNotIn('token', result)
        self.assertNotIn('sk-fake', result)

    def test_private_free_text_refused(self):
        for value in ['sk-fake-do-not-publish-123456', 'Bearer abcdefghijkl', '/home/liam/private.jpg',
                      '/opt/personal/frame.png', 'C:\\Pictures\\face.jpg', 'me@example.com',
                      'data:image/png;base64,abc', 'https://example.com/frame', 'api_key=fake', '"token": "planted-fake"', 'A'*60]:
            raw = take()
            raw['events'][0]['output'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'private data|path refused'):
                exporter.export_take(raw)

    def test_malformed(self):
        for change, message in [({'frames': []}, 'at least one'), ({'schema_version': 2}, 'schema_version'), ({'mode':'real arm, not sim'}, 'mode'),
                                ({'frames': [{'t': 0, 'scene': {}}]}, 'coordinates')]:
            raw = take(); raw.update(change)
            with self.assertRaisesRegex(ValueError, message):
                exporter.export_take(raw)
        raw = take(); raw['frames'][1]['t'] = -1
        with self.assertRaisesRegex(ValueError, 'out of range'):
            exporter.export_take(raw)
        raw = take(); raw['frames'][0]['speed_scale'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'finite number'):
            exporter.export_take(raw)

    def test_stop_and_route_survive(self):
        raw = take()
        raw['frames'][0].update(stop={'kind': 'fault', 'reason': 'Camera stalled', 'recovering_in_s': 1.23456},
                                 route={'kind': 'around', 'waypoints': [[.1, .2, .3]]})
        frame = exporter.export_take(raw)['frames'][0]
        self.assertEqual(frame['stop']['recovering_in_s'], 1.235)
        self.assertEqual(frame['route']['kind'], 'around')

    def test_external_sources_refused(self):
        for url in ['https://example.com', 'http://192.168.1.1:8780', 'http://127.0.0.1:8765',
                    'http://user:password@127.0.0.1:8780', 'http://127.0.0.1:8780/other']:
            with self.assertRaisesRegex(ValueError, 'loopback|test port'):
                exporter.read_deck(url)

    def test_cli_clear_error_preserves_destination(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'bad.json'; target = Path(tmp)/'out.json'
            source.write_text('{"schema_version":1,"frames":[]}'); target.write_text('unchanged')
            result = subprocess.run([sys.executable, str(ROOT/'scripts/export_replay.py'), str(source), '--output', str(target)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('frames: at least one frame', result.stderr)
            self.assertEqual(target.read_text(), 'unchanged')


if __name__ == '__main__':
    unittest.main()

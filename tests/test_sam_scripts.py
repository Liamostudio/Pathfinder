"""Check script I/O and predictor protocol with fake inference, without a GPU."""
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def sam_fixture(tmp_path, monkeypatch):
    cv2 = pytest.importorskip('cv2')
    np = pytest.importorskip('numpy')
    checkpoint = tmp_path / 'checkpoint.pt'
    checkpoint.touch()
    video = tmp_path / 'clip.mp4'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 15, (64, 48))
    assert writer.isOpened()
    for _ in range(2):
        writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    writer.release()

    class Predictor:
        def __init__(self):
            module = SimpleNamespace(bfloat16=lambda: None)
            backbone = SimpleNamespace(vision_backbone=SimpleNamespace(trunk=module),
                                       language_backbone=SimpleNamespace(encoder=module))
            self.model = SimpleNamespace(detector=SimpleNamespace(backbone=backbone),
                                         init_state=lambda **kwargs: None)
            self.closed = False
            self.fail = False

        def handle_request(self, request):
            if request['type'] == 'start_session':
                self.resource = Path(request['resource_path'])
                return {'session_id': 'test'}
            if request['type'] == 'close_session':
                self.closed = True

        def handle_stream_request(self, request):
            if self.fail:
                raise RuntimeError('synthetic inference failure')
            for i in range(2):
                yield {'frame_index': i, 'outputs': {'out_obj_ids': [42],
                       'out_binary_masks': np.ones((1, 48, 64), dtype=bool)}}

    predictor = Predictor()
    cuda = SimpleNamespace(is_available=lambda: True, synchronize=lambda: None,
                           max_memory_allocated=lambda: 1e9, memory_allocated=lambda: 1e9)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=cuda))
    monkeypatch.setitem(sys.modules, 'sam3', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'sam3.model_builder', SimpleNamespace(
        build_sam3_predictor=lambda **kwargs: predictor))
    return video, checkpoint, predictor


def run_script(monkeypatch, name, args):
    script = Path(__file__).resolve().parents[1] / 'scripts' / name
    monkeypatch.setattr(sys, 'argv', [str(script), *map(str, args)])
    runpy.run_path(str(script), run_name='__main__')


def test_preview_writes_video_and_cleans_up(tmp_path, monkeypatch, capsys, sam_fixture):
    video, checkpoint, predictor = sam_fixture
    output = tmp_path / 'preview.mp4'
    run_script(monkeypatch, 'sam3_live.py', ['--video', video, '--checkpoint', checkpoint,
               '--prompt', 'cube', '--no-window', '--out', output])
    assert 'frames with a mask: 2' in capsys.readouterr().out
    assert predictor.closed
    assert not predictor.resource.exists()
    import cv2
    cap = cv2.VideoCapture(str(output))
    try:
        assert cap.isOpened() and cap.read()[0]
    finally:
        cap.release()


def test_preview_cleans_up_after_inference_failure(monkeypatch, sam_fixture):
    video, checkpoint, predictor = sam_fixture
    predictor.fail = True
    with pytest.raises(RuntimeError, match='synthetic inference failure'):
        run_script(monkeypatch, 'sam3_live.py', ['--video', video, '--checkpoint', checkpoint,
                   '--prompt', 'cube', '--no-window'])
    assert predictor.closed
    assert not predictor.resource.exists()


def test_smoke_reports_nested_object_ids(monkeypatch, capsys, sam_fixture):
    video, checkpoint, predictor = sam_fixture
    run_script(monkeypatch, 'sam3_smoke.py', ['--video', video, '--checkpoint', checkpoint,
               '--prompt', 'cube', '--max-frames', '2'])
    output = capsys.readouterr().out
    assert 'frame 1 n_objs 1' in output
    assert 'not enough frames' in output
    assert predictor.closed

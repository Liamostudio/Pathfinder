"""Exercise capture failure and actual MP4 recording/replay without a webcam."""
import json
from types import SimpleNamespace

import pytest

from lookandpick.adapters import camera


@pytest.fixture
def vision():
    pytest.importorskip('cv2')
    return pytest.importorskip('numpy')


def test_record_replay_round_trip(tmp_path, vision):
    stamps = [1000.0, 1000.04, 1000.09]
    recorder = camera.Recorder(tmp_path, 'synthetic')
    for i, stamp in enumerate(stamps):
        recorder.write(stamp, vision.full((48, 64, 3), i * 80, dtype=vision.uint8))
    recorder.close()
    frames = list(camera.ReplayCamera(recorder.path).frames())
    assert [t for t, _ in frames] == stamps
    assert len(frames) == recorder.frame_count == 3
    assert all(frame.shape == (48, 64, 3) for _, frame in frames)
    assert frames[0][1].mean() < frames[1][1].mean() < frames[2][1].mean()


def test_capture_failure_is_explicit_and_releases_camera(monkeypatch):
    class Capture:
        released = False

        def isOpened(self): return True
        def set(self, *args): pass
        def read(self): return False, None
        def release(self): self.released = True

    cap = Capture()
    cv = SimpleNamespace(VideoCapture=lambda _: cap, CAP_PROP_FRAME_WIDTH=1,
                         CAP_PROP_FRAME_HEIGHT=2, CAP_PROP_FPS=3)
    monkeypatch.setattr(camera, '_cv2', lambda: cv)
    with pytest.raises(RuntimeError, match='stopped delivering'):
        next(camera.LiveCamera(0, 'test').frames())
    assert cap.released


def test_missing_video_is_an_error(tmp_path, vision):
    with pytest.raises(RuntimeError, match='Cannot open video'):
        list(camera.ReplayCamera(tmp_path / 'missing.mp4').frames())


def test_failed_writer_is_an_error(tmp_path, monkeypatch, vision):
    class Writer:
        released = False

        def isOpened(self): return False
        def release(self): self.released = True

    writer = Writer()
    monkeypatch.setattr(camera, '_cv2', lambda: SimpleNamespace(
        VideoWriter=lambda *args: writer, VideoWriter_fourcc=lambda *args: 0))
    recorder = camera.Recorder(tmp_path, 'bad')
    with pytest.raises(RuntimeError, match='Cannot create video'):
        recorder.write(0, vision.zeros((48, 64, 3), dtype=vision.uint8))
    recorder.close()
    assert writer.released
    assert not recorder.path.with_suffix('.json').exists()


def test_mismatched_timestamps_are_rejected(tmp_path, vision):
    recorder = camera.Recorder(tmp_path, 'mismatch')
    recorder.write(123, vision.zeros((48, 64, 3), dtype=vision.uint8))
    recorder.close()
    recorder.path.with_suffix('.json').write_text(json.dumps([123, 124]))
    with pytest.raises(RuntimeError, match='timestamp sidecar'):
        list(camera.ReplayCamera(recorder.path).frames())


def test_recording_validates_frames_and_preserves_existing_files(tmp_path, vision):
    recorder = camera.Recorder(tmp_path, 'test')
    recorder.write(10, vision.zeros((48, 64, 3), dtype=vision.uint8))
    with pytest.raises(ValueError, match='timestamps'):
        recorder.write(float('nan'), vision.zeros((48, 64, 3), dtype=vision.uint8))
    with pytest.raises(ValueError, match='size changed'):
        recorder.write(11, vision.zeros((24, 32, 3), dtype=vision.uint8))
    recorder.close()
    with pytest.raises(FileExistsError):
        camera.Recorder(tmp_path, 'test')
    with pytest.raises(RuntimeError, match='closed'):
        recorder.write(12, vision.zeros((48, 64, 3), dtype=vision.uint8))


@pytest.mark.parametrize('name', ['', '..', '../escape', '/tmp/escape'])
def test_recording_name_cannot_escape_directory(tmp_path, name):
    with pytest.raises(ValueError, match='filename'):
        camera.Recorder(tmp_path, name)

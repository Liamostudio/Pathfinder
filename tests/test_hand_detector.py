import sys
from types import SimpleNamespace

import pytest

from lookandpick.adapters.hands import MediaPipeHandDetector


def test_optional_model_file_is_required_before_import(tmp_path):
    with pytest.raises(FileNotFoundError):
        MediaPipeHandDetector(tmp_path/'missing.task')


def test_detector_converts_bgr_and_returns_pixel_landmarks(tmp_path,monkeypatch):
    np=pytest.importorskip('numpy')
    model=tmp_path/'model.task'
    model.touch()
    calls=[]

    class Detector:
        def detect_for_video(self,image,stamp):
            calls.append((image,stamp))
            return SimpleNamespace(hand_landmarks=[[SimpleNamespace(x=.5,y=.25) for _ in range(21)]])
        def close(self): calls.append('closed')

    mp=SimpleNamespace(Image=lambda **kwargs:kwargs['data'],ImageFormat=SimpleNamespace(SRGB='RGB'),
        tasks=SimpleNamespace(BaseOptions=lambda **kwargs:kwargs,vision=SimpleNamespace(
            HandLandmarkerOptions=lambda **kwargs:kwargs,RunningMode=SimpleNamespace(VIDEO='VIDEO'),
            HandLandmarker=SimpleNamespace(create_from_options=lambda options:Detector()))))
    monkeypatch.setitem(sys.modules,'mediapipe',mp)
    detector=MediaPipeHandDetector(model)
    frame=np.zeros((100,200,3),dtype=np.uint8)
    frame[:,:,0]=17
    hands=detector.detect(frame,0)
    assert hands[0].pixels[0]==(100,25)
    assert calls[0][0][0,0,2]==17
    with pytest.raises(ValueError,match='timestamps'): detector.detect(frame,0)
    detector.close()
    assert calls[-1]=='closed'
    with pytest.raises(RuntimeError,match='closed'): detector.detect(frame,1)

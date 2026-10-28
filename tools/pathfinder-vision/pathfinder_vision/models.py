"""Model files: where they live, and an explicit downloader (the app never downloads on its own)."""
from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE = "https://storage.googleapis.com/mediapipe-models"


@dataclass(frozen=True)
class ModelFile:
    key: str
    filename: str
    url: str
    description: str


MODELS = {
    "hands": ModelFile("hands", "hand_landmarker.task",
                       f"{BASE}/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
                       "MediaPipe Hand Landmarker (21 landmarks per hand)"),
    "person-box": ModelFile("person-box", "efficientdet_lite0.tflite",
                            f"{BASE}/object_detector/efficientdet_lite0/float16/1/efficientdet_lite0.tflite",
                            "MediaPipe EfficientDet-Lite0 (COCO; only the 'person' class is kept)"),
    "person-skeleton": ModelFile("person-skeleton", "pose_landmarker_lite.task",
                                 f"{BASE}/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
                                 "MediaPipe Pose Landmarker lite (33 body landmarks per person)"),
}


def default_models_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "pathfinder-vision" / "models"


def model_path(key: str, models_dir: Path) -> Path:
    return models_dir / MODELS[key].filename


def download(keys, models_dir: Path, say=print) -> None:
    models_dir.mkdir(parents=True, exist_ok=True)
    for key in keys:
        m = MODELS[key]
        target = model_path(key, models_dir)
        if target.is_file() and target.stat().st_size > 0:
            say(f"{m.filename}: already present ({target.stat().st_size:,} bytes)")
            continue
        say(f"{m.filename}: downloading from {m.url}")
        tmp = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(m.url, timeout=60) as response, open(tmp, "wb") as out:
            out.write(response.read())
        tmp.replace(target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        say(f"{m.filename}: {target.stat().st_size:,} bytes, SHA-256 {digest}")

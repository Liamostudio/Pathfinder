"""Seeded camera/detector faults for exercising existing watchdogs."""
from dataclasses import dataclass
import time
import numpy as np


@dataclass(frozen=True)
class CameraFaults:
    seed: int = 0
    drop_probability: float = 0
    noise_sigma: float = 0
    exposure: float = 1
    latency_s: float = 0
    freeze_after: int | None = None
    black_after: int | None = None


class FaultCamera:
    def __init__(self, source, faults=CameraFaults()):
        if not 0 <= faults.drop_probability <= 1 or not 0 <= faults.latency_s <= 2 or faults.noise_sigma < 0 or faults.exposure < 0:
            raise ValueError('Invalid camera fault parameters')
        self.source,self.faults,self.name=source,faults,source.name

    def frames(self):
        rng=np.random.default_rng(self.faults.seed)
        frozen=None
        iterator=self.source.frames()
        try:
            for i,(stamp,frame) in enumerate(iterator):
                if rng.random() < self.faults.drop_probability:
                    continue
                if self.faults.freeze_after is not None and i >= self.faults.freeze_after and frozen is not None:
                    yield frozen[0],frozen[1].copy() # repeated timestamp must fail the safety loop
                    continue
                image=np.clip(frame.astype(float)*self.faults.exposure + rng.normal(0,self.faults.noise_sigma,frame.shape),0,255).astype(np.uint8)
                if self.faults.black_after is not None and i >= self.faults.black_after:
                    image[:]=0
                if self.faults.latency_s:
                    time.sleep(self.faults.latency_s)
                frozen=(stamp,image.copy())
                yield stamp,image
        finally:
            if hasattr(iterator,'close'):
                iterator.close()


class FaultDetector:
    def __init__(self, detector, *, crash_after=None, stall_after=None, stall_s=.3, lost_frames=(), ghost_hands=()):
        self.detector,self.crash_after,self.stall_after=detector,crash_after,stall_after
        self.stall_s,self.lost_frames=stall_s,set(lost_frames)
        self.ghost_hands=tuple(ghost_hands)
        self.calls=0

    def detect(self, frame, timestamp_ms):
        index=self.calls
        self.calls+=1
        if self.crash_after is not None and index >= self.crash_after:
            raise RuntimeError('Injected detector crash')
        if self.stall_after is not None and index >= self.stall_after:
            time.sleep(self.stall_s)
        if index in self.lost_frames:
            return []
        return self.detector.detect(frame,timestamp_ms) + list(self.ghost_hands)

    def close(self):
        self.detector.close()

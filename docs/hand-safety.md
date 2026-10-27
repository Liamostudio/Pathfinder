# Hand detection and continuous safety — simulation only

Implemented from `codex-brief-hand-and-arm.md`, without changing calibration, intrinsics or
the existing calibration scripts. This is a tested development loop, not physical safety acceptance.

## Detector and setup

`HandDetector` and `PixelHand` live in `adapters/base.py`. `MediaPipeHandDetector` lazily loads
MediaPipe Tasks Hand Landmarker in CPU VIDEO mode. It converts BGR to RGB, checks timestamp
ordering and returns 21 pixel landmarks per detected hand. `FakeHandDetector` replaces it in
tests. No SAM dependency or model call is involved.

```sh
pip install -e ".[dev,hands]"
mkdir -p weights
curl --fail --location \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task \
  --output weights/hand_landmarker.task
python -m lookandpick.hand_loop --video recordings/example.mp4 \
  --model weights/hand_landmarker.task --max-frames 120
```

Weights remain git-ignored. Verified model SHA-256:
`fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1` (7,819,105 bytes).
The tested environment was Python 3.14.4, MediaPipe 1.1.1, numpy 2.5.4 and OpenCV 5.0.0.
It was installed separately in `/tmp/lookandpick-hands-venv`; the project's `.venv` was not
changed to install MediaPipe. The verified model and input clips are scratch files, not in Git.

## Projection and the monitor

`project_hands` calls the supplied calibration's `pixel_to_table(pixel, z=hand_height)` on
each landmark. It encloses the projected points with a sphere and adds a configurable 3 cm
development uncertainty margin. The optional `table_to_arm` callback applies a measured
marker/table-to-arm transform. Identity is appropriate only when those frames coincide.
There is no inferred metric depth: height is an explicit assumption. MediaPipe's relative z
and its hand-centred world coordinates are not used as camera-to-table distance.

`HandSafetyMonitor` starts with allowed speed zero. A successful fresh observation containing
no hands is distinct from no observation. It updates world-state hands and applies the
existing speed-and-separation check against the current path and the hand envelope.

- A nearby hand slows; entering the minimum distance pauses; leaving can resume.
- The watchdog polls independently at 100 Hz, with a default freshness deadline of 150 ms.
- No initial result, stalled capture, stalled inference, malformed geometry/timing, source
  failure or end-of-recording stops and latches. A late result cannot re-enable motion.
- A manual stop bypasses path evaluation. A failed monitor cannot be rearmed by clearing
  `Safety` alone: construct a new monitor and arm after investigating the fault.
- Source timestamps must advance, including during replay. Freshness uses local monotonic
  receipt time; replay-relative and wall-clock timestamps are not mixed into watchdog age.
- Capture/inference run on a separate worker. Decision application is serialised so an older
  clear observation cannot overwrite a newer blocked result.

Use `MonitoredTracker(objects_tracker, monitor)` when building `Tools`, so its refresh does
not overwrite the monitor's hands with another tracker's empty list. Use a `SpeedControlledArm`
whose speed setter actually affects ongoing motion. `SimArm` now implements the setter and
serialises motion/gripping/stop. A zero scale blocks its gripper too. All monitor callbacks
(path, speed setter, halt) must be local and bounded; there is no guarantee against an
unresponsive hardware bus or a blocked path callback.

For a recorded, calibrated simulation:

```sh
python -m lookandpick.hand_loop --video recordings/example.mp4 \
  --model weights/hand_landmarker.task --table-calibration calibration/table.json \
  --hand-height 0.08 --hz 30 --timeout-ms 150
```

The table JSON contains `camera_matrix` (3×3), `rvec`, `tvec`, and optional `dist`, using
`TableCalibration`'s world-to-camera convention. The CLI always uses `SimArm` and a test path
from its position to `(0.25, 0.10, 0)`; it never opens a camera or a serial port. The benchmark
mode needs no table JSON. A real table calibration and arm-frame transform must be measured,
not copied from a synthetic example. No detector is wired into the GUI webcam preview yet.

## Evidence and limits

Measured locally on CPU, after five warm-up frames:

| Input | Frames | Detection | Mean inference | p95 inference |
| --- | --- | --- | --- | --- |
| Black 640×480 frames | 45 | no hands | 11.88 ms | 13.87 ms |
| Official MediaPipe `thumb_up.jpg`, resized and encoded as a 640×480 replay | 60 | hand in 60/60 frames | 21.40 ms | 28.41 ms |

The second measurement corresponds to 46.7 inference frames/s; it excludes camera capture,
calibration projection and command dispatch. The actual detector also completed 30 frames
in the paced monitor with a synthetic table calibration and latched on end-of-source.
This supports further 15–30 Hz testing, not a live-camera or worst-case latency guarantee.

Full suite: 99 tests pass (33 new hand/arm cases). With optional vision/hardware imports blocked,
68 pass and 14 skip. Tests cover approach/leave, envelope distance, real calibration API projection, frame offsets,
startup/no-result timeout, blocked inference, repeated timestamps, exceptions, manual stop,
late results, old/new decision ordering, monitored tool refreshes and actual ReplayCamera I/O.

Not verified: live-camera accuracy, moving/occluded/multiple hands, missed detections,
hardware stopping time, appropriate real hand height/uncertainty, robot-link swept geometry,
or the physical 10/10 hand-stop acceptance test. The existing straight-segment arm geometry
and thresholds remain development approximations. A source emitting old buffered images
with new timestamps can still appear fresh; the capture provider must guarantee new frames.

Primary references: [MediaPipe Python guide](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/python),
[model and model card](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker#models),
[official test inputs](https://github.com/google-ai-edge/mediapipe/blob/master/mediapipe/tasks/python/test/vision/hand_landmarker_test.py).

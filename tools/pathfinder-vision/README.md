# Pathfinder Vision

A small standalone camera viewer for testing and filming. The window always says **what is running**
(each detector, its model, where it runs, how long it takes) and **what it is detecting right now**
(each hand or person, with its score), and what it is *not* doing.

- **Hands:** MediaPipe Hand Landmarker, 21 points per hand.
- **People:** off, boxes (MediaPipe EfficientDet-Lite0, person class only) or skeletons (MediaPipe Pose
  Landmarker lite, 33 body points). Press `P` to cycle.
- Everything runs on the **CPU**; no network after installation, and no data leaves the machine.

## Install and run

```sh
./install.sh                       # once; needs the internet
pathfinder-vision --list-cameras
pathfinder-vision --camera 2       # e.g. the Insta360 Ace Pro
pathfinder-vision --camera 2 --people skeletons --width 640 --height 360
```

Keys: `H` hands on/off, `P` people off, boxes or skeletons, `R` start/stop recording, `S` screenshot, `Q` quit.
Recordings (the whole window, panel included, so the footage shows what was running) and screenshots go to
`~/Videos/pathfinder-vision/`.

## Cost

Measured numbers appear live in the panel. On the author's laptop (CPU only, 640x360, empty scene): hands about 18 ms per frame on their own; adding boxes costs about 28 ms and skeletons about 22 ms more (likely higher with a person in view). People detection costs extra CPU on top of hands, so the people
detector runs every 2nd frame by default (`--people-every N`); its boxes or skeleton then update at half the
camera rate while hands stay at full rate. USB 2 cameras are only fast at low resolution: 640x360 is the
default for that reason.

## Limits

MediaPipe is trained mostly on upright hands and people facing a camera. Hands seen edge-on, gloved or
motion-blurred, and people seen from above or partly out of frame, will be missed or mislabelled. Boxes and
skeletons are 2D image positions only: there is no distance, so this does not make anything safe by itself.

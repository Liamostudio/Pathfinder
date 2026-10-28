"""Drawing: the camera view with labelled detections, and a panel that says what is running and what it sees."""
from __future__ import annotations

import numpy as np

HAND_COLOUR = (0, 200, 255)     # BGR: amber
PERSON_COLOUR = (90, 220, 90)   # BGR: green
PANEL_BG = (28, 28, 30)
TEXT = (235, 235, 235)
DIM = (150, 150, 155)
FONT = 0  # cv2.FONT_HERSHEY_SIMPLEX


def colour_for(kind: str):
    return HAND_COLOUR if kind == "hand" else PERSON_COLOUR


def _text(cv2, img, s, x, y, scale=0.5, colour=TEXT, thick=1):
    cv2.putText(img, s, (x, y), FONT, scale, colour, thick, cv2.LINE_AA)


def draw_detections(cv2, img, detections, sx: float, sy: float):
    for d in detections:
        col = colour_for(d.kind)
        for a, b in d.connections:
            if a < len(d.points) and b < len(d.points):
                cv2.line(img, (int(d.points[a][0] * sx), int(d.points[a][1] * sy)),
                         (int(d.points[b][0] * sx), int(d.points[b][1] * sy)), col, 2, cv2.LINE_AA)
        for x, y in d.points:
            cv2.circle(img, (int(x * sx), int(y * sy)), 3, col, -1, cv2.LINE_AA)
        x0, y0, x1, y1 = int(d.box[0] * sx), int(d.box[1] * sy), int(d.box[2] * sx), int(d.box[3] * sy)
        cv2.rectangle(img, (x0, y0), (x1, y1), col, 2)
        label = d.label + (f"  {d.score:.2f}" if d.score is not None else "")
        (tw, th), _ = cv2.getTextSize(label, FONT, 0.55, 1)
        cv2.rectangle(img, (x0, max(0, y0 - th - 8)), (x0 + tw + 8, max(th + 8, y0)), col, -1)
        _text(cv2, img, label, x0 + 4, max(th + 2, y0 - 5), 0.55, (0, 0, 0))


def panel_lines(state: dict) -> list[tuple[str, tuple, float]]:
    """(text, colour, scale) lines for the panel. Pure data, so it can be tested without a display."""
    cam = state["camera"]
    lines = [("PATHFINDER VISION", TEXT, 0.8),
             (f"Camera: {cam['name']}   {cam['width']}x{cam['height']}   {state['fps']:.1f} fps", TEXT, 0.5),
             (f"Pipeline: {state['pipeline_ms']:.0f} ms per frame, all on the CPU", DIM, 0.45),
             ("", TEXT, 0.4), ("RUNNING", DIM, 0.5)]
    for d in state["detectors"]:
        on = d["status"] == "on"
        lines.append((f"[{d['hotkey']}] {d['title']}: {d['status'].upper()}", HAND_COLOUR if on and d["kind"] == "hand" else
                      PERSON_COLOUR if on else DIM, 0.55))
        lines.append((f"     {d['what']}", TEXT if on else DIM, 0.42))
        if on:
            every = f", every {d['every']} frames" if d.get("every", 1) > 1 else ""
            lines.append((f"     {d['backend']}{every}   {d['ms']:.1f} ms   {d['count']} found", DIM, 0.42))
        elif d.get("note"):
            lines.append((f"     {d['note']}", (80, 80, 240), 0.42))
    lines += [("", TEXT, 0.4), ("DETECTING NOW", DIM, 0.5)]
    found = state["detections"]
    if not found:
        lines.append(("  nothing in view", DIM, 0.5))
    for d in found[:8]:
        lines.append((f"  {d.label}" + (f"   score {d.score:.2f}" if d.score is not None else ""), colour_for(d.kind), 0.5))
    lines += [("", TEXT, 0.4), ("NOT RUNNING: faces, gestures, objects, tracking IDs, depth.", DIM, 0.42)]
    return lines


DD_TOP, DD_H, ITEM_H = 48, 30, 28


def dropdown_geometry(view_w: int, panel_w: int, n_items: int) -> dict:
    """Rectangles (x0, y0, x1, y1) of the webcam dropdown box and, when open, its items. Shared with the mouse handler."""
    x0, x1 = view_w + 16, view_w + panel_w - 16
    box = (x0, DD_TOP, x1, DD_TOP + DD_H)
    items = [(x0, DD_TOP + DD_H + i * ITEM_H, x1, DD_TOP + DD_H + (i + 1) * ITEM_H) for i in range(n_items)]
    return {"box": box, "items": items}


def inside(rect, x: int, y: int) -> bool:
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def render(cv2, frame, detections, state: dict, view_w: int = 1024, panel_w: int = 480):
    """The full window: camera view on the left, panel on the right. Returns a BGR image."""
    h, w = frame.shape[:2]
    view_h = int(round(view_w * h / w))
    view = cv2.resize(frame, (view_w, view_h), interpolation=cv2.INTER_LINEAR)
    draw_detections(cv2, view, detections, view_w / w, view_h / h)
    kinds = sorted({d["kind"] for d in state["detectors"] if d["status"] == "on"})
    banner = "DETECTING: " + (" + ".join(k for k in (d["title"].lower() for d in state["detectors"] if d["status"] == "on")) or "nothing (all detectors off)")
    cv2.rectangle(view, (0, 0), (view_w, 30), (0, 0, 0), -1)
    _text(cv2, view, banner, 10, 21, 0.6, TEXT)
    if state.get("recording"):
        cv2.circle(view, (view_w - 150, 15), 8, (0, 0, 230), -1)
        _text(cv2, view, f"REC {state['recording']}", view_w - 135, 21, 0.55, (0, 0, 230), 2)
    height = max(view_h, 520)
    canvas = np.full((height, view_w + panel_w, 3), PANEL_BG, np.uint8)
    canvas[:view_h, :view_w] = view
    lines = panel_lines(state)
    _text(cv2, canvas, lines[0][0], view_w + 16, 34, lines[0][2], lines[0][1], 2)
    dd = state.get("dropdown") or {"open": False, "items": [], "selected": None, "status": ""}
    geo = dropdown_geometry(view_w, panel_w, len(dd["items"]))
    y = DD_TOP + DD_H + 26
    for text, col, scale in lines[1:]:
        if text:
            _text(cv2, canvas, text, view_w + 16, y, scale, col, 1)
        y += int(14 + 22 * scale)
    bx0, by0, bx1, by1 = geo["box"]
    cv2.rectangle(canvas, (bx0, by0), (bx1, by1), (60, 60, 64), -1)
    cv2.rectangle(canvas, (bx0, by0), (bx1, by1), (120, 120, 126), 1)
    current = next((n for i, n in dd["items"] if i == dd["selected"]), None) or state["camera"]["name"]
    _text(cv2, canvas, f"Webcam {dd['selected'] if dd['selected'] is not None else ''}: {current}"[:46], bx0 + 8, by1 - 9, 0.5, TEXT)
    _text(cv2, canvas, "v", bx1 - 18, by1 - 9, 0.5, TEXT)
    if dd.get("status"):
        _text(cv2, canvas, dd["status"], bx0, by1 + 18, 0.45, (80, 160, 255))
    if dd["open"]:
        for (i, name), r in zip(dd["items"], geo["items"]):
            cv2.rectangle(canvas, (r[0], r[1]), (r[2], r[3]), (45, 45, 50) if i != dd["selected"] else (70, 90, 60), -1)
            cv2.rectangle(canvas, (r[0], r[1]), (r[2], r[3]), (120, 120, 126), 1)
            _text(cv2, canvas, f"{i}: {name}"[:50], r[0] + 8, r[3] - 9, 0.5, TEXT)
    _text(cv2, canvas, "H hands  P people  C next webcam  R record  S screenshot  Q quit",
          view_w + 16, height - 14, 0.42, DIM)
    return canvas

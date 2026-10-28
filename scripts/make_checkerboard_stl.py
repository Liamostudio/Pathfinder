"""Write a 3D-printable lens-calibration checkerboard as STL (no dependencies).

    python scripts/make_checkerboard_stl.py                    # FLAT, for a multi-colour printer (AMS)
    python scripts/make_checkerboard_stl.py --raised           # raised squares, single extruder
    python scripts/make_checkerboard_stl.py --square-mm 12

FLAT (default): print FACE DOWN on the bed, like the ArUco panel. Two files, `*_flat_white.stl` and
`*_flat_black.stl`, that fit together exactly with no overlap: the first 0.8 mm (4 layers at 0.2 mm) holds the
pattern, black and white squares side by side, and the remaining 2.2 mm is a white backing. The face is the
side that touched the bed, so it is perfectly flat. In Bambu Studio, select both files in one import and answer
"Yes" to loading them as one object with multiple parts, then assign white to one part and black to the other.
Textured PEI gives a matt face (recommended); smooth PEI gives a glossy one that reflects lights.
The model is mirrored in x, so seen from the face it reads like the PDF.

RAISED (--raised) writes three files to markers/ (millimetres, z up, top-left square at the origin corner):
  *_combined.stl : plate + raised black squares in ONE file. Print in white, and insert a filament change
                   at the layer where the plate ends (the slicer note printed below gives the height), so
                   everything above it, the squares, prints black.
  *_base.stl, *_squares.stl : the same two parts separately, for a multi-material printer or a slicer
                   that lets you assign a filament to each part (load both as one multi-part object).
The squares are 0.8 mm raised, so the pattern is flat and square to the printer; print flat on the bed,
matt filament (gloss reflects room lights), and MEASURE one printed square with calipers or a good
ruler afterwards: calibrate_lens.py needs the real size (--square-mm), not the designed one.
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

PLATE_T = 3.0   # plate thickness, mm
RAISE = 0.8     # black squares stand this far above the plate
OVERLAP = 0.2   # squares sink into the plate so a slicer unions the two shells


def box(x0, y0, z0, x1, y1, z1):
    """12 triangles (outward normals via counter-clockwise winding) for an axis-aligned box."""
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        tris += [(v[a], v[b], v[c]), (v[a], v[c], v[d])]
    return tris


def normal(t):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = t
    ux, uy, uz, vx, vy, vz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
    n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
    length = sum(c * c for c in n) ** 0.5 or 1.0
    return tuple(c / length for c in n)


def write_stl(path: Path, tris, name: str) -> None:
    out = bytearray(name.encode()[:80].ljust(80, b" "))
    out += struct.pack("<I", len(tris))
    for t in tris:
        out += struct.pack("<3f", *normal(t))
        for vertex in t:
            out += struct.pack("<3f", *vertex)
        out += b"\0\0"
    path.write_bytes(bytes(out))


def build(cols: int, rows: int, square_mm: float, border_mm: float):
    """Returns (base triangles, square triangles, (width, height)) for cols x rows INNER corners."""
    nx, ny = cols + 1, rows + 1  # squares across and down
    w, h = nx * square_mm + 2 * border_mm, ny * square_mm + 2 * border_mm
    base = box(0, 0, 0, w, h, PLATE_T)
    squares = []
    for r in range(ny):
        for c in range(nx):
            if (r + c) % 2 == 0:
                x0 = border_mm + c * square_mm
                y0 = h - border_mm - (r + 1) * square_mm  # row 0 at the top when viewed from above
                squares += box(x0, y0, PLATE_T - OVERLAP, x0 + square_mm, y0 + square_mm, PLATE_T + RAISE)
    return base, squares, (w, h)


PATTERN_T = 0.8  # flat version: thickness of the pattern layer (four 0.2 mm layers)


def build_flat(cols: int, rows: int, square_mm: float, border_mm: float):
    """Returns (white triangles, black triangles, (width, height)); the two fill the board exactly, no overlap.

    z = 0 is the face (the side that sits on the print bed). Viewed from the face, row 0 is at the top and
    column 0 at the left, with a black square in the top-left cell, like the PDF.
    """
    nx, ny = cols + 1, rows + 1
    w, h = nx * square_mm + 2 * border_mm, ny * square_mm + 2 * border_mm
    black, white = [], []
    for r in range(ny):
        for c in range(nx):
            x0 = w - border_mm - (c + 1) * square_mm  # mirrored in x: the face is seen from below
            y0 = h - border_mm - (r + 1) * square_mm
            (black if (r + c) % 2 == 0 else white).extend(box(x0, y0, 0, x0 + square_mm, y0 + square_mm, PATTERN_T))
    if border_mm > 0:  # four non-overlapping strips of plain white round the pattern
        for x0, y0, x1, y1 in ((0, 0, w, border_mm), (0, h - border_mm, w, h),
                               (0, border_mm, border_mm, h - border_mm), (w - border_mm, border_mm, w, h - border_mm)):
            white.extend(box(x0, y0, 0, x1, y1, PATTERN_T))
    white.extend(box(0, 0, PATTERN_T, w, h, PLATE_T))  # backing
    return white, black, (w, h)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cols", type=int, default=6, help="inner corners across")
    p.add_argument("--rows", type=int, default=9, help="inner corners down")
    p.add_argument("--square-mm", type=float, default=15.0)
    p.add_argument("--border-mm", type=float, default=5.0, help="plain plate margin round the pattern")
    p.add_argument("--out-dir", type=Path, default=Path("markers"))
    p.add_argument("--raised", action="store_true", help="single-extruder version with raised black squares")
    a = p.parse_args()
    if a.cols < 3 or a.rows < 3 or a.square_mm <= 0 or a.border_mm < 0:
        p.error("need at least 3x3 inner corners and positive sizes")
    a.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"checkerboard_{a.cols}x{a.rows}_{a.square_mm:g}mm"
    if not a.raised:
        white, black, (w, h) = build_flat(a.cols, a.rows, a.square_mm, a.border_mm)
        write_stl(a.out_dir / f"{stem}_flat_white.stl", white, "checkerboard white")
        write_stl(a.out_dir / f"{stem}_flat_black.stl", black, "checkerboard black")
        print(f"Wrote {stem}_flat_white.stl and {stem}_flat_black.stl in {a.out_dir}")
        print(f"Board {w:g} x {h:g} x {PLATE_T:g} mm, {a.cols}x{a.rows} inner corners, {a.square_mm:g} mm squares. "
              f"Print FACE DOWN (z = 0 on the bed). Load both files as one multi-part object, white and black.")
        print("Then measure a printed square and run calibrate_lens.py with that --square-mm "
              f"--cols {a.cols} --rows {a.rows}.")
        return
    base, squares, (w, h) = build(a.cols, a.rows, a.square_mm, a.border_mm)
    write_stl(a.out_dir / f"{stem}_combined.stl", base + squares, "checkerboard combined")
    write_stl(a.out_dir / f"{stem}_base.stl", base, "checkerboard base")
    write_stl(a.out_dir / f"{stem}_squares.stl", squares, "checkerboard squares")
    print(f"Wrote {stem}_combined.stl, _base.stl, _squares.stl in {a.out_dir}")
    print(f"Board {w:g} x {h:g} mm, {a.cols}x{a.rows} inner corners, {a.square_mm:g} mm squares, "
          f"plate {PLATE_T:g} mm + {RAISE:g} mm raised squares (total {PLATE_T + RAISE:g} mm).")
    print(f"Slicer: colour change at Z = {PLATE_T:g} mm (white below, black above). Then measure a square and "
          f"run calibrate_lens.py with that --square-mm and --cols {a.cols} --rows {a.rows}.")


if __name__ == "__main__":
    main()

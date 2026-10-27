"""Write a print-ready ArUco marker (or, with --checkerboard, a lens-calibration board) as an A4 vector PDF.

    python scripts/make_marker.py                 # 12 cm, id 0 -> markers/aruco_id0_120mm.pdf
    python scripts/make_marker.py --size-mm 150

Print at 100% / "actual size" (turn off "fit to page"), on matt paper, then measure the black
square with a ruler and pass that real size to check_calibration.py. Needs the `vision` extra.
"""
from __future__ import annotations

import argparse
from pathlib import Path

A4_MM = (210.0, 297.0)
PT = 72 / 25.4  # points per mm


def marker_cells(marker_id: int, dictionary: str):
    import cv2  # noqa: PLC0415
    d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary))
    n = d.markerSize + 2  # data bits plus the one-cell black border
    return cv2.aruco.generateImageMarker(d, marker_id, n, borderBits=1) < 128, n


def build_pdf(size_mm: float, marker_id: int, dictionary: str) -> bytes:
    cells, n = marker_cells(marker_id, dictionary)
    cell = size_mm / n
    left = (A4_MM[0] - size_mm) / 2
    top = 60.0  # mm from the top edge to the marker's top edge
    ops = ["0 g"]
    for r in range(n):
        for c in range(n):
            if cells[r, c]:
                x = (left + c * cell) * PT
                y = (A4_MM[1] - top - (r + 1) * cell) * PT
                ops.append(f"{x:.3f} {y:.3f} {cell * PT:.3f} {cell * PT:.3f} re f")
    # A 100 mm ruler under the marker: if it doesn't measure 100 mm, the print was scaled.
    ry = (A4_MM[1] - top - size_mm - 25) * PT
    rx = (A4_MM[0] - 100) / 2 * PT
    ops.append(f"{rx:.3f} {ry:.3f} {100 * PT:.3f} {0.6 * PT:.3f} re f")
    for x_mm in (0, 100):
        ops.append(f"{rx + x_mm * PT - 0.3 * PT:.3f} {ry - 2 * PT:.3f} {0.6 * PT:.3f} {4.6 * PT:.3f} re f")
    caption = f"ArUco {dictionary} id {marker_id}  |  black square = {size_mm:g} mm  |  print at 100%"
    ruler = "This line should measure exactly 100 mm. If not, the print was scaled."
    text = [
        f"BT /F1 10 Tf {20 * PT:.2f} {(A4_MM[1] - 25) * PT:.2f} Td ({caption}) Tj ET",
        f"BT /F1 9 Tf {rx:.2f} {ry - 9 * PT:.2f} Td ({ruler}) Tj ET",
    ]
    return assemble_pdf(ops, text)


def assemble_pdf(ops: list[str], text: list[str]) -> bytes:
    stream = "\n".join(ops + ["0 g"] + text).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {A4_MM[0] * PT:.2f} {A4_MM[1] * PT:.2f}] "
        f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def build_checkerboard_pdf(cols: int, rows: int, square_mm: float) -> bytes:
    """portrait A4 checkerboard of `cols` x `rows` inner corners (squares = corners + 1)."""
    w, h = (cols + 1) * square_mm, (rows + 1) * square_mm
    if w > A4_MM[0] - 20 or h > A4_MM[1] - 60:
        raise ValueError("checkerboard does not fit on A4; use a smaller --square-mm")
    left, top = (A4_MM[0] - w) / 2, 40.0
    ops = ["0 g"]
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 0:
                x, y = (left + c * square_mm) * PT, (A4_MM[1] - top - (r + 1) * square_mm) * PT
                ops.append(f"{x:.3f} {y:.3f} {square_mm * PT:.3f} {square_mm * PT:.3f} re f")
    text = [f"BT /F1 10 Tf {20 * PT:.2f} {(A4_MM[1] - 25) * PT:.2f} Td "
            f"(Lens calibration board: {cols}x{rows} inner corners, {square_mm:g} mm squares | print at 100%) Tj ET"]
    return assemble_pdf(ops, text)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--size-mm", type=float, default=120.0, help="side of the black square")
    p.add_argument("--id", type=int, default=0)
    p.add_argument("--dictionary", default="DICT_4X4_50")
    p.add_argument("--out", type=Path)
    p.add_argument("--checkerboard", action="store_true",
                   help="write a lens-calibration checkerboard instead (6x9 inner corners, see --square-mm)")
    p.add_argument("--square-mm", type=float, default=20.0, help="checkerboard square size")
    a = p.parse_args()
    if a.checkerboard:
        out = a.out or Path("markers") / f"checkerboard_6x9_{a.square_mm:g}mm.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(build_checkerboard_pdf(6, 9, a.square_mm))
        print(f"Wrote {out}")
        return
    if not 20 <= a.size_mm <= 180:
        p.error("--size-mm must be between 20 and 180 to fit an A4 page")
    out = a.out or Path("markers") / f"aruco_id{a.id}_{a.size_mm:g}mm.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(build_pdf(a.size_mm, a.id, a.dictionary))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()

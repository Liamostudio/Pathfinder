"""The checkerboard STL is watertight per shell, the right size, and matches the PDF pattern parity."""
import importlib.util
import pytest
import struct
from collections import Counter
from pathlib import Path

spec = importlib.util.spec_from_file_location("mk", Path(__file__).parent.parent / "scripts" / "make_checkerboard_stl.py")
mk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mk)


def test_dimensions_and_counts(tmp_path):
    base, squares, (w, h) = mk.build(6, 9, 15.0, 5.0)
    assert (w, h) == (7 * 15 + 10, 10 * 15 + 10)
    assert len(squares) == 35 * 12 and len(base) == 12  # 70 cells, half are black
    zs = [v[2] for t in squares for v in t]
    assert min(zs) == mk.PLATE_T - mk.OVERLAP and max(zs) == mk.PLATE_T + mk.RAISE


def test_each_box_is_closed():
    edges = Counter()
    for t in mk.box(0, 0, 0, 2, 3, 4):
        for i in range(3):
            edges[frozenset((t[i], t[(i + 1) % 3]))] += 1
    assert set(edges.values()) == {2}  # every edge shared by exactly two triangles


def test_binary_file_round_trip(tmp_path):
    base, squares, _ = mk.build(6, 9, 15.0, 5.0)
    path = tmp_path / "b.stl"
    mk.write_stl(path, base + squares, "t")
    data = path.read_bytes()
    count = struct.unpack("<I", data[80:84])[0]
    assert count == len(base + squares) and len(data) == 84 + 50 * count


def test_top_left_square_is_black_like_the_pdf():
    _, squares, (w, h) = mk.build(6, 9, 15.0, 5.0)
    xs = [v[0] for t in squares for v in t]
    ys = [v[1] for t in squares for v in t]
    assert min(xs) == 5.0 and max(ys) == h - 5.0  # a black square sits in the top-left corner cell


def volume(tris):
    """Signed volume by the divergence theorem (a closed, outward-facing mesh gives its true volume)."""
    total = 0.0
    for a, b, c in tris:
        total += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
    return total


def test_flat_parts_fill_the_board_exactly_with_no_overlap_or_gap():
    white, black, (w, h) = mk.build_flat(6, 9, 15.0, 5.0)
    assert volume(white) + volume(black) == pytest.approx(w * h * mk.PLATE_T)
    assert volume(black) == pytest.approx(35 * 15 * 15 * mk.PATTERN_T)
    assert min(v[2] for t in white + black for v in t) == 0.0  # the face is on the bed


def test_flat_face_reads_as_a_6x9_board_with_15mm_squares_from_below():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    white, black, (w, h) = mk.build_flat(6, 9, 15.0, 5.0)
    S = 8
    img = np.full((int(h * S), int(w * S)), 255, np.uint8)
    for t in black:
        if all(abs(v[2]) < 1e-9 for v in t):  # faces lying on the bed side
            # seen from below, x is mirrored back to the reader's left-to-right
            pts = np.array([[(w - v[0]) * S, (h - v[1]) * S] for v in t], np.int32)
            cv2.fillConvexPoly(img, pts, 0)
    img = cv2.copyMakeBorder(img, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    ok, corners = cv2.findChessboardCornersSB(img, (6, 9))
    assert ok and abs(np.linalg.norm(corners[1][0] - corners[0][0]) / S - 15.0) < 0.1

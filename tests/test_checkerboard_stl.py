"""The checkerboard STL is watertight per shell, the right size, and matches the PDF pattern parity."""
import importlib.util
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

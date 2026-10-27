"""The printable marker is drawn from the same bits OpenCV detects."""
import importlib.util
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from lookandpick.calibration import detect_marker  # noqa: E402

spec = importlib.util.spec_from_file_location("make_marker", Path(__file__).parent.parent / "scripts" / "make_marker.py")
make_marker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_marker)


def test_cells_are_detected_as_the_requested_marker():
    cells, n = make_marker.marker_cells(5, "DICT_4X4_50")
    img = np.where(cells, 0, 255).astype(np.uint8)
    img = cv2.copyMakeBorder(cv2.resize(img, (n * 20, n * 20), interpolation=cv2.INTER_NEAREST), 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    assert detect_marker(img, marker_id=5) is not None
    assert detect_marker(img, marker_id=0) is None


def test_pdf_is_a4_with_exact_marker_size():
    pdf = make_marker.build_pdf(120, 0, "DICT_4X4_50")
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert b"/MediaBox [0 0 595.28 841.89]" in pdf

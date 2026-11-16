"""Plain-assert checks for the generated artwork. Run: python3 tests/test_poster.py"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402
from PIL import Image  # noqa: E402


def check_logo():
    body = app.make_logo()
    assert body.startswith(b"\x89PNG\r\n\x1a\n"), "logo is a PNG"
    image = Image.open(io.BytesIO(body))
    assert image.size == (512, 512), "logo is square"
    assert len(body) > 2000, "logo has real pixels"


def check_poster():
    body = app.make_poster("Aurora Drift", 2025, "demo")
    assert body.startswith(b"\x89PNG\r\n\x1a\n"), "poster is a PNG"
    image = Image.open(io.BytesIO(body))
    assert image.size == (400, 600), "poster has poster proportions"
    assert len(body) > 2000, "poster has real pixels"


def check_long_titles():
    for title in ("A" * 200, "Aurora Drift The Long Subtitle Cut 2025", "x"):
        assert app.make_poster(title, None, title)[:4] == b"\x89PNG", title[:20]


def main():
    check_logo()
    check_poster()
    check_long_titles()
    print("poster rules ok")


if __name__ == "__main__":
    main()

"""Plain-assert checks for Range parsing. Run: python3 tests/test_ranges.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


def main():
    size = 1000
    assert app.parse_range(None, size) == (0, 999, 200)
    assert app.parse_range("bytes=0-99", size) == (0, 99, 206)
    assert app.parse_range("bytes=100-", size) == (100, 999, 206)
    assert app.parse_range("bytes=-100", size) == (900, 999, 206)
    assert app.parse_range("bytes=0-9999", size) == (0, 999, 206)
    assert app.parse_range("bytes=999-999", size) == (999, 999, 206)
    assert app.parse_range("bytes=1000-", size)[2] == 416
    assert app.parse_range("bytes=900-800", size)[2] == 416
    assert app.parse_range("bytes=abc-def", size) == (0, 999, 200)
    assert app.parse_range("bytes=-0", size) == (0, 999, 200)
    assert app.parse_range("bytes=0-1, 5-6", size) == (0, 1, 206)
    print("range rules ok")


if __name__ == "__main__":
    main()

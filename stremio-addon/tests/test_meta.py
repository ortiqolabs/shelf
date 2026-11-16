"""Plain-assert checks for Stremio field types. Run: python3 tests/test_meta.py"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


def main():
    # Stremio's core only accepts epoch milliseconds or RFC3339 here
    assert app.released_ms("2026-11-05") == 1793836800000
    assert app.released_ms("2026-11-05T00:00:00Z") == 1793836800000
    assert app.released_ms(1793836800000) == 1793836800000
    assert app.released_ms("") is None
    assert app.released_ms(None) is None
    assert app.released_ms("not a date") is None
    print("meta rules ok")


if __name__ == "__main__":
    main()

"""Plain-assert checks for poster cache maintenance. Run: python3 tests/test_cache.py"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402

DAY = 24 * 3600


def make_file(directory, name, age_days=0, body=b"\x89PNG fake"):
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(body)
    stamp = time.time() - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


def test_old_removed():
    with tempfile.TemporaryDirectory() as tmp:
        old = make_file(tmp, "old.png", age_days=40)
        assert app.prune_cache(tmp, 30 * DAY, 500) == 1, "one stale poster removed"
        assert not os.path.exists(old)


def test_recent_kept():
    with tempfile.TemporaryDirectory() as tmp:
        fresh = make_file(tmp, "fresh.png", age_days=29)
        assert app.prune_cache(tmp, 30 * DAY, 500) == 0, "recent poster kept"
        assert os.path.exists(fresh)


def test_cap_drops_oldest():
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(6):
            make_file(tmp, f"p{i}.png", age_days=i + 1)
        assert app.prune_cache(tmp, 30 * DAY, 4) == 2, "cap drops two"
        assert sorted(os.listdir(tmp)) == ["p0.png", "p1.png", "p2.png", "p3.png"]


def test_matches_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        match = os.path.join(tmp, "matches.json")
        json.dump({"aurora": 603}, open(match, "w"))
        notes = make_file(tmp, "notes.txt", age_days=90, body=b"x")
        assert app.prune_cache(tmp, 0, 0) == 0, "only PNG posters are candidates"
        assert json.load(open(match)) == {"aurora": 603}
        assert os.path.exists(notes)


def test_missing_dir_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        assert app.prune_cache(os.path.join(tmp, "gone"), 30 * DAY, 500) == 0


def main():
    test_old_removed()
    test_recent_kept()
    test_cap_drops_oldest()
    test_matches_untouched()
    test_missing_dir_is_noop()
    print("cache rules ok")


if __name__ == "__main__":
    main()

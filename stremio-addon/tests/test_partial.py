"""Plain-assert checks for the partial-download rule. Run: python3 tests/test_partial.py"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app


def touch(path, size=16):
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


def test_plain_file_is_complete():
    with tempfile.TemporaryDirectory() as tmp:
        movie = touch(os.path.join(tmp, "Movie.2026.mkv"))
        assert not app.is_partial(movie, set(), True), "plain file is complete"


def test_part_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        movie = touch(os.path.join(tmp, "Movie.2026.mkv"))
        touch(movie + ".part")
        assert app.is_partial(movie, set(), True), ".part means partial"


def test_aria2_sidecar():
    with tempfile.TemporaryDirectory() as tmp:
        movie = touch(os.path.join(tmp, "Movie.2026.mkv"))
        touch(movie + ".aria2")
        assert not app.is_partial(movie, set(), True), "stale aria2 sidecar with live RPC means complete"
        assert app.is_partial(movie, set(), False), "sidecar with dead RPC is treated as partial"
        assert app.is_partial(movie, {os.path.realpath(movie)}, True), "aria2 says it is open"


def test_qbittorrent_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        qb = touch(os.path.join(tmp, "Show.S01E01.mkv.!qB"))
        assert app.is_partial(qb, set(), True), "qBittorrent temp suffix means partial"


def test_subtitles_are_not_video():
    with tempfile.TemporaryDirectory() as tmp:
        movie = touch(os.path.join(tmp, "Movie.2026.mkv"))
        touch(movie + ".srt")
        assert not app.is_partial(movie + ".srt", set(), True), "sidecar subtitles are not video"


def main():
    test_plain_file_is_complete()
    test_part_suffix()
    test_aria2_sidecar()
    test_qbittorrent_suffix()
    test_subtitles_are_not_video()
    print("partial rules ok")


if __name__ == "__main__":
    main()

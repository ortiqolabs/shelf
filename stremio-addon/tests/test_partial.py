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


def main():
    with tempfile.TemporaryDirectory() as tmp:
        movie = touch(os.path.join(tmp, "Movie.2026.mkv"))
        assert not app.is_partial(movie, set(), True), "plain file is complete"

        touch(movie + ".part")
        assert app.is_partial(movie, set(), True), ".part means partial"
        os.unlink(movie + ".part")

        touch(movie + ".aria2")
        assert not app.is_partial(movie, set(), True), "stale aria2 sidecar with live RPC means complete"
        assert app.is_partial(movie, set(), False), "sidecar with dead RPC is treated as partial"
        assert app.is_partial(movie, {os.path.realpath(movie)}, True), "aria2 says it is open"
        os.unlink(movie + ".aria2")

        qb = touch(os.path.join(tmp, "Show.S01E01.mkv.!qB"))
        assert app.is_partial(qb, set(), True), "qBittorrent temp suffix means partial"

        touch(movie + ".srt")
        assert not app.is_partial(movie + ".srt", set(), True), "sidecar subtitles are not video"

    print("partial rules ok")


if __name__ == "__main__":
    main()

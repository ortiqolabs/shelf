import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shelf.aria2 import name_of, shape, summary  # noqa: E402


def test_single_file_uses_the_file_name():
    item = {"gid": "a", "status": "active", "files": [{"path": "/media/Movies_and_Shows/Film.mkv"}]}
    assert name_of(item) == "Film.mkv"


def test_torrent_uses_the_torrent_name():
    item = {"gid": "b", "status": "active", "bittorrent": {"info": {"name": "Show Pack"}}}
    assert name_of(item) == "Show Pack"


def test_multi_file_uses_the_folder():
    item = {
        "gid": "c",
        "status": "active",
        "files": [
            {"path": "/media/Movies_and_Shows/Show Pack/ep1.mkv"},
            {"path": "/media/Movies_and_Shows/Show Pack/ep2.mkv"},
        ],
    }
    assert name_of(item) == "Show Pack"


def test_missing_metadata_falls_back_to_the_link():
    item = {
        "gid": "d",
        "status": "error",
        "files": [{"path": "", "uris": [{"uri": "https://host/file.bin"}]}],
    }
    assert name_of(item) == "file.bin"


def test_shape_maps_status_to_bucket_and_percent():
    item = {
        "gid": "e",
        "status": "complete",
        "totalLength": "200",
        "completedLength": "50",
        "downloadSpeed": "10",
        "files": [{"path": "/media/Movies_and_Shows/x.bin"}],
    }
    shaped = shape(item)
    assert shaped["bucket"] == "done"
    assert shaped["percent"] == 25.0
    assert shaped["down"] == 10


def test_summary_counts_buckets():
    items = [{"bucket": "active"}, {"bucket": "active"}, {"bucket": "done"}, {"bucket": "failed"}]
    assert summary(items) == {"active": 2, "queued": 0, "done": 1, "failed": 1}

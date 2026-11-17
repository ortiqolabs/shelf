import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from shelf import media, web  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point every path media.py writes to at the test's own tmp folder."""
    monkeypatch.setattr(media, "MATCHES_PATH", tmp_path / "matches.json")
    monkeypatch.setattr(media, "TMDB_ENV", tmp_path / "no-tmdb.env")
    monkeypatch.setattr(media, "HLS_ROOT", tmp_path / "hls")
    monkeypatch.setattr(media, "download_dir", lambda: tmp_path / "download")
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    media._probe_cache.clear()
    media._matches = None
    media._offline["until"] = 0.0
    media._sessions.clear()
    yield
    media._sessions.clear()


@pytest.fixture
def client():
    with TestClient(web.app) as test_client:
        yield test_client


def make_file(name, data=b"0123456789" * 10):
    root = media.download_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path, media.item_id(path.relative_to(root).as_posix())


def fake_probe(streams=None, duration="120.500000"):
    async def probe(path, mtime=None):
        return {"streams": streams or [], "format": {"duration": duration}}

    return probe


def write_shim(tmp_path, body):
    path = tmp_path / "fake-ffmpeg"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


PLAYLIST_SHIM = """
last=""
for arg in "$@"; do last="$arg"; done
dir=$(dirname "$last")
printf '#EXTM3U\\n#EXT-X-VERSION:3\\n#EXT-X-TARGETDURATION:4\\n#EXT-X-PLAYLIST-TYPE:EVENT\\n#EXT-X-ENDLIST\\n' > "$last"
printf 'segment-bytes' > "$dir/seg00000.ts"
sleep 5
"""


# ------------------------------------------------------------ file names


def test_parse_filename_drops_junk_and_takes_year():
    assert media.parse_filename("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv") == ("The Matrix", 1999)


def test_parse_filename_keeps_a_number_that_is_the_title():
    assert media.parse_filename("1917.2019.1080p.WEB-DL.mkv") == ("1917", 2019)


def test_parse_filename_without_a_year():
    title, year = media.parse_filename("severance.s01e02.720p.hdtv.mkv")
    assert year is None
    assert title.startswith("Severance")


def test_describe_marks_episode_files_as_series():
    info = media.describe(Path("Severance.S01E02.1080p.WEB-DL.mkv"))
    assert info["kind"] == "series"
    assert info["container"] == "mkv"

    movie = media.describe(Path("Dune.2021.2160p.mkv"))
    assert movie["kind"] == "movie"


def test_item_id_is_stable_and_sixteen_hex():
    first = media.item_id("Movies/Film.mkv")
    assert first == media.item_id("Movies/Film.mkv")
    assert re.fullmatch(r"[0-9a-f]{16}", first)
    assert first != media.item_id("Movies/Other.mkv")


# --------------------------------------------------------------- library


def test_library_lists_videos_newest_first_and_skips_dotfiles(monkeypatch):
    monkeypatch.setattr(media, "ffprobe_bin", lambda: None)
    old, old_id = make_file("Old.mkv")
    new, new_id = make_file("Shows/New.mp4")
    make_file(".hidden.mp4")
    make_file("notes.txt")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))

    client = TestClient(web.app)
    body = client.get("/api/library").json()
    ids = [item["id"] for item in body["items"]]
    assert ids == [new_id, old_id]
    assert body["items"][0]["kind"] == "movie"
    assert body["items"][0]["container"] == "mp4"
    assert body["items"][0]["duration"] is None
    assert body["items"][0]["videoCodec"] == ""
    assert body["items"][0]["poster"] is None
    assert body["items"][0]["playableDirect"] is False


def test_library_reports_probe_details(monkeypatch):
    make_file("Film.mp4")
    monkeypatch.setattr(
        media,
        "probe",
        fake_probe(
            [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac", "channels": 2},
                {"codec_type": "subtitle", "codec_name": "subrip"},
            ]
        ),
    )
    item = TestClient(web.app).get("/api/library").json()["items"][0]
    assert item["duration"] == 120.5
    assert item["videoCodec"] == "h264"
    assert item["audioTracks"] == 1
    assert item["subtitleTracks"] == 1
    assert item["playableDirect"] is True


def test_media_detail_lists_tracks_and_urls(monkeypatch):
    _, file_id = make_file("Film.mkv")
    monkeypatch.setattr(
        media,
        "probe",
        fake_probe(
            [
                {"codec_type": "video", "codec_name": "hevc"},
                {
                    "codec_type": "audio",
                    "codec_name": "eac3",
                    "channels": 6,
                    "tags": {"language": "eng", "title": "Surround"},
                },
                {"codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}},
            ]
        ),
    )
    body = TestClient(web.app).get(f"/api/media/{file_id}").json()
    assert body["item"]["id"] == file_id
    assert body["item"]["playableDirect"] is False
    assert body["tracks"]["audio"] == [
        {"index": 0, "lang": "eng", "title": "Surround", "codec": "eac3", "channels": 6}
    ]
    assert body["tracks"]["subtitles"] == [
        {"index": 0, "lang": "eng", "title": "", "codec": "subrip"}
    ]
    assert body["directUrl"] == f"/api/stream/{file_id}"
    assert body["hlsUrl"] is None


def test_unknown_id_is_404():
    assert TestClient(web.app).get("/api/media/" + "0" * 16).status_code == 404
    assert TestClient(web.app).get("/api/media/../../etc/passwd").status_code == 404


def test_symlink_escaping_the_download_folder_is_hidden(tmp_path):
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"not yours")
    root = media.download_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "Link.mp4").symlink_to(outside)
    file_id = media.item_id("Link.mp4")

    assert media.resolve(file_id) is None
    assert TestClient(web.app).get(f"/api/stream/{file_id}").status_code == 404


# ---------------------------------------------------------------- stream


def test_stream_serves_full_file_and_ranges():
    _, file_id = make_file("Movie.mp4")
    client = TestClient(web.app)

    full = client.get(f"/api/stream/{file_id}")
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-length"] == "100"
    assert full.headers["content-type"] == "video/mp4"
    assert full.content == b"0123456789" * 10

    part = client.get(f"/api/stream/{file_id}", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206
    assert part.headers["content-range"] == "bytes 10-19/100"
    assert part.content == b"0123456789"

    tail = client.get(f"/api/stream/{file_id}", headers={"Range": "bytes=-10"})
    assert tail.status_code == 206
    assert tail.content == b"0123456789"

    beyond = client.get(f"/api/stream/{file_id}", headers={"Range": "bytes=500-"})
    assert beyond.status_code == 416
    assert beyond.headers["content-range"] == "bytes */100"

    head = client.head(f"/api/stream/{file_id}")
    assert head.status_code == 200
    assert head.headers["content-length"] == "100"
    assert head.content == b""


def test_stream_unknown_id_is_404():
    assert TestClient(web.app).get("/api/stream/" + "f" * 16).status_code == 404


# ------------------------------------------------------------- subtitles


def test_subtitles_404_when_ffmpeg_is_missing(monkeypatch):
    _, file_id = make_file("Movie.mkv")
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: None)
    assert TestClient(web.app).get(f"/api/subtitles/{file_id}/0.vtt").status_code == 404


# --------------------------------------------------------------- sessions


def test_player_session_writes_playlist_and_stops(client, tmp_path, monkeypatch):
    _, file_id = make_file("Film.mkv")
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: write_shim(tmp_path, PLAYLIST_SHIM))

    response = client.post("/api/player/session", json={"id": file_id, "audio": 0, "start": 12.5})
    assert response.status_code == 200
    body = response.json()
    session = body["session"]
    assert body["playlist"] == f"/hls/{session}/index.m3u8"

    playlist = wait_for(client, body["playlist"])
    assert playlist.status_code == 200
    assert playlist.headers["cache-control"] == "no-store"
    assert playlist.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert b"#EXTM3U" in playlist.content

    segment = client.get(f"/hls/{session}/seg00000.ts")
    assert segment.status_code == 200
    assert segment.content == b"segment-bytes"
    assert segment.headers["content-type"] == "video/mp2t"

    # a segment ffmpeg has not written yet, while it is still running, is 404
    assert client.get(f"/hls/{session}/seg99999.ts").status_code == 404

    assert client.delete(f"/api/player/session/{session}").json() == {"ok": True}
    assert not (media.HLS_ROOT / session).exists()


def test_player_session_503_without_ffmpeg(client, monkeypatch):
    _, file_id = make_file("Film.mkv")
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: None)
    response = client.post("/api/player/session", json={"id": file_id, "audio": 0, "start": 0})
    assert response.status_code == 503


def test_player_session_caps_at_three(client, tmp_path, monkeypatch):
    _, file_id = make_file("Film.mkv")
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: write_shim(tmp_path, PLAYLIST_SHIM))
    for _ in range(3):
        assert client.post("/api/player/session", json={"id": file_id}).status_code == 200
    assert client.post("/api/player/session", json={"id": file_id}).status_code == 429


def test_idle_session_is_reaped(client, tmp_path, monkeypatch):
    _, file_id = make_file("Film.mkv")
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: write_shim(tmp_path, PLAYLIST_SHIM))
    session = client.post("/api/player/session", json={"id": file_id}).json()["session"]

    media._sessions[session]["last"] -= media.SESSION_TTL + 1
    client.portal.call(media.reap)

    assert session not in media._sessions
    assert not (media.HLS_ROOT / session).exists()
    assert client.get(f"/hls/{session}/index.m3u8").status_code == 404


# -------------------------------------------------------------------- art


def test_generated_art_escapes_the_title():
    response = media.artwork_svg("abc123", 'Tom & Jerry <"x">', wide=False)
    text = response.body.decode()
    assert response.media_type == "image/svg+xml"
    assert "&amp;" in text
    assert "&lt;" in text
    assert "<svg" in text


def test_poster_endpoint_serves_svg():
    _, file_id = make_file("Film.mkv")
    response = TestClient(web.app).get(f"/api/poster/{file_id}.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert b"Film" in response.content

    missing = TestClient(web.app).get("/api/backdrop/" + "0" * 16 + ".svg")
    assert missing.status_code == 404


def wait_for(client, url, timeout=3.0):
    """The player retries a playlist that ffmpeg has not written yet."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(url)
        if response.status_code == 200:
            return response
        time.sleep(0.02)
    return response

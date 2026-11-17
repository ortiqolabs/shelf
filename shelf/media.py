"""Theater: browse and stream the video files already in the download folder.

Everything here copies. Nothing transcodes, because this runs on a Pi.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import time
import xml.sax.saxutils as xml
from pathlib import Path
from typing import Any, Iterator

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from . import settings

VIDEO_EXT = {"mp4", "mkv", "avi", "mov", "m4v", "webm", "ts", "mpg", "mpeg"}
PLAYABLE_CONTAINERS = {"mp4", "m4v", "webm"}
PLAYABLE_CODECS = {"h264", "vp8", "vp9", "av1"}

CONTENT_TYPES = {
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "avi": "video/x-msvideo",
    "mov": "video/quicktime",
    "ts": "video/mp2t",
    "mpg": "video/mpeg",
    "mpeg": "video/mpeg",
}

EPISODE = re.compile(r"[sS]\d{1,2}[ ._-]?[eE]\d{1,3}")
YEAR = re.compile(r"(19|20)\d{2}")
# the same junk the Stremio addon drops; both read the same file names
JUNK = re.compile(
    r"\b(2160p|1080p|720p|480p|4k|uhd|web[- ]?dl|webrip|hdtv|bluray|brrip|dvdrip|hdrip|"
    r"x264|x265|h264|h265|hevc|aac|ddp?5[. ]?1|dts|10bit|hdr|sdr|proper|repack|"
    r"amzn|nf|zee5|hdhub4u|9xmovies|xbet|esub|multi|dual audio|"
    r"esubs?|msub|subbed|dubbed|remux|atmos|truehd|eac3|ac3|opus|flac|xvid|divx|av1|"
    r"extended|uncut|hq|fullhd|full hd|hd|web|dl|5[. ]?1|7[. ]?1|2[. ]?0)\b",
    re.I,
)
SITE = re.compile(
    r"(www\.)?[\w-]+\.(com|net|org|ws|in|mx|to|cc|link|xyz|top|vip|club|live|me|tv)\b",
    re.I,
)

MATCHES_PATH = Path.home() / ".cache" / "shelf-theater" / "matches.json"
TMDB_ENV = Path.home() / ".config" / "shelf" / "tmdb.env"
OMDB_ENV = Path.home() / ".config" / "shelf" / "omdb.env"
TMDB_IMAGE = "https://image.tmdb.org/t/p"
CINEMETA = "https://v3-cinemeta.strem.io/meta"
OMDB_URL = "https://www.omdbapi.com/"
# bumped when the cache gains fields, so old entries are looked up once more
CACHE_SCHEMA = 2

HLS_ROOT = Path(os.environ.get("SHELF_HLS_DIR", "/tmp/shelf-hls"))
SESSION_TTL = 60.0
MAX_SESSIONS = 3

_probe_cache: dict[tuple[str, float], dict[str, Any]] = {}
_matches: dict[str, dict[str, Any]] | None = None
_offline = {"until": 0.0}
_active = {"streams": 0}
_sessions: dict[str, dict[str, Any]] = {}
_reaper: asyncio.Task | None = None


# ---------------------------------------------------------------- library


def download_dir() -> Path:
    """The folder the settings page points at."""
    return Path(os.path.expanduser(settings.current()["downloadDir"]))


def item_id(rel: str) -> str:
    """Stable id for a path relative to the download folder."""
    return hashlib.sha1(rel.encode()).hexdigest()[:16]


def _walk(root: Path) -> list[Path]:
    """Video files under root, dot files and dot folders skipped."""
    found: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                if name.rsplit(".", 1)[-1].lower() not in VIDEO_EXT:
                    continue
                found.append(Path(dirpath) / name)
    except OSError:
        return []
    return found


def parse_filename(name: str) -> tuple[str, int | None]:
    """Title and trailing year from a release-style file name."""
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = SITE.sub(" ", stem)
    stem = re.sub(r"[._]+", " ", stem)
    stem = JUNK.sub(" ", stem)
    stem = re.sub(r"[\[\]{}()]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_")

    year: int | None = None
    # the last year with a title in front of it wins; anything after it is a group
    for match in reversed(list(YEAR.finditer(stem))):
        head = stem[: match.start()].strip(" -_")
        if head:
            stem, year = head, int(match.group(0))
            break

    title = re.sub(r"\s+", " ", stem).strip(" -_").title()
    return title or "Untitled", year


def describe(path: Path) -> dict[str, Any]:
    """Title, year, kind and container for one file."""
    title, year = parse_filename(path.name)
    return {
        "title": title,
        "year": year,
        "kind": "series" if EPISODE.search(path.name) else "movie",
        "container": path.suffix.lower().lstrip("."),
    }


def resolve(file_id: str) -> Path | None:
    """The file behind an id, and only when it stays inside the download folder."""
    if not re.fullmatch(r"[0-9a-f]{16}", file_id):
        return None
    root = download_dir()
    try:
        real_root = root.resolve()
    except OSError:
        return None
    for path in _walk(root):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if item_id(rel) != file_id:
            continue
        try:
            real = path.resolve()
        except OSError:
            return None
        if real.is_relative_to(real_root) and real.is_file():
            return real
    return None


# ------------------------------------------------------------------ probe


def ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


async def probe(path: Path, mtime: float | None = None) -> dict[str, Any]:
    """ffprobe streams and format, cached by path and mtime."""
    try:
        mtime = path.stat().st_mtime if mtime is None else mtime
    except OSError:
        return {}
    key = (str(path), mtime)
    if key in _probe_cache:
        return _probe_cache[key]
    binary = ffprobe_bin()
    if not binary:
        return {}
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(out or b"{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    if len(_probe_cache) > 4096:
        _probe_cache.clear()
    _probe_cache[key] = data
    return data


def _duration(data: dict[str, Any]) -> float | None:
    for source in (data.get("format") or {}, *_video(data)):
        try:
            value = float(source.get("duration"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return round(value, 3)
    return None


def _video(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            yield stream


def _tracks(data: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Streams of one kind; index is the ordinal ffmpeg maps with 0:<kind>:<n>."""
    out: list[dict[str, Any]] = []
    for stream in data.get("streams") or []:
        if stream.get("codec_type") != kind:
            continue
        tags = stream.get("tags") or {}
        track = {
            "index": len(out),
            "lang": tags.get("language") or "",
            "title": tags.get("title") or "",
            "codec": stream.get("codec_name") or "",
        }
        if kind == "audio":
            track["channels"] = int(stream.get("channels") or 0)
        out.append(track)
    return out


# ------------------------------------------------------------- enrichment


def _file_key(path: Path, names: tuple[str, ...]) -> str:
    """First value for one of names in a KEY=value file, empty when absent."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.strip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in names:
            return value.strip().strip("\"'")
    return ""


def tmdb_key() -> str:
    """TMDB key from the environment, else from the config file."""
    key = (os.environ.get("TMDB_API_KEY") or os.environ.get("TMDB_API") or "").strip()
    if key:
        return key
    return _file_key(TMDB_ENV, ("TMDB_API_KEY", "TMDB_API", "TMDB_KEY", "API_KEY"))


def omdb_key() -> str:
    """OMDb key from the environment, else from the config file."""
    key = os.environ.get("SHELF_OMDB_KEY", "").strip()
    if key:
        return key
    return _file_key(OMDB_ENV, ("OMDB_API_KEY",))


def _load_matches() -> dict[str, dict[str, Any]]:
    global _matches
    if _matches is None:
        try:
            data = json.loads(MATCHES_PATH.read_text())
        except (OSError, ValueError):
            data = {}
        _matches = data if isinstance(data, dict) else {}
    return _matches


def _save_matches() -> None:
    try:
        MATCHES_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MATCHES_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_load_matches(), indent=1))
        tmp.replace(MATCHES_PATH)
    except OSError:
        pass


def _empty_metadata() -> dict[str, Any]:
    """Every optional field at its unknown value."""
    return {
        "poster": None,
        "backdrop": None,
        "description": None,
        "tagline": None,
        "genres": [],
        "tmdbRating": None,
        "tmdbVotes": None,
        "imdbId": None,
        "imdbRating": None,
        "rtRating": None,
        "certification": None,
        "director": [],
        "cast": [],
        "trailer": None,
    }


def _cached(entry: dict[str, Any]) -> dict[str, Any]:
    """Cached fields with the unknown values filled in."""
    meta = _empty_metadata()
    for name in meta:
        if name in entry:
            meta[name] = entry[name]
    return meta


def _names(values: Any) -> list[str]:
    return [str(value["name"]) for value in values or [] if value.get("name")]


def _number(value: Any, kind: type[float] | type[int]) -> float | int | None:
    try:
        return kind(value)
    except (TypeError, ValueError):
        return None


def _certification(detail: dict[str, Any], route: str) -> str | None:
    """US certification, else the first one TMDB has."""
    block = "release_dates" if route == "movie" else "content_ratings"
    rows = (detail.get(block) or {}).get("results") or []

    def rating(row: dict[str, Any]) -> str | None:
        if route != "movie":
            return row.get("rating") or None
        for release in row.get("release_dates") or []:
            if release.get("certification"):
                return release["certification"]
        return None

    us = next((row for row in rows if row.get("iso_3166_1") == "US"), None)
    if us and rating(us):
        return rating(us)
    for row in rows:
        value = rating(row)
        if value:
            return value
    return None


def _trailer(detail: dict[str, Any]) -> str | None:
    """YouTube id of an official English trailer, else any trailer."""
    clips = [
        clip
        for clip in (detail.get("videos") or {}).get("results") or []
        if clip.get("site") == "YouTube" and clip.get("type") == "Trailer" and clip.get("key")
    ]
    for clip in clips:
        if clip.get("official") and clip.get("iso_639_1") == "en":
            return clip["key"]
    return clips[0]["key"] if clips else None


def _apply_detail(meta: dict[str, Any], detail: dict[str, Any], route: str) -> None:
    """Copy the fields the detail view shows out of a TMDB detail response."""
    meta["description"] = detail.get("overview") or None
    meta["tagline"] = detail.get("tagline") or None
    meta["genres"] = _names(detail.get("genres"))
    meta["tmdbRating"] = _number(detail.get("vote_average"), float)
    meta["tmdbVotes"] = _number(detail.get("vote_count"), int)
    meta["imdbId"] = (detail.get("external_ids") or {}).get("imdb_id") or None
    meta["certification"] = _certification(detail, route)
    credits = detail.get("credits") or {}
    meta["director"] = [
        person["name"]
        for person in credits.get("crew") or []
        if person.get("job") == "Director" and person.get("name")
    ][:3]
    meta["cast"] = [person["name"] for person in credits.get("cast") or [] if person.get("name")][:6]
    meta["trailer"] = _trailer(detail)


async def _detail(client: httpx.AsyncClient, route: str, tmdb_id: Any, key: str) -> dict[str, Any]:
    """One TMDB detail call with the extra blocks the view needs."""
    extra = "credits,release_dates,videos,external_ids"
    if route != "movie":
        extra = "credits,content_ratings,videos,external_ids"
    response = await client.get(
        f"https://api.themoviedb.org/3/{route}/{tmdb_id}",
        params={"api_key": key, "append_to_response": extra},
    )
    response.raise_for_status()
    return response.json() or {}


async def _imdb_rating(client: httpx.AsyncClient, imdb_id: str, kind: str) -> float | None:
    """IMDb rating from Cinemeta; a failure is not worth a retry."""
    route = "movie" if kind == "movie" else "series"
    try:
        response = await client.get(f"{CINEMETA}/{route}/{imdb_id}.json")
        response.raise_for_status()
        return _number(((response.json() or {}).get("meta") or {}).get("imdbRating"), float)
    except Exception:
        return None


async def _rt_rating(client: httpx.AsyncClient, imdb_id: str) -> str | None:
    """Rotten Tomatoes score from OMDb, only when an OMDb key is configured."""
    key = omdb_key()
    if not key or not imdb_id:
        return None
    try:
        response = await client.get(OMDB_URL, params={"i": imdb_id, "apikey": key})
        response.raise_for_status()
        for rating in (response.json() or {}).get("Ratings") or []:
            if rating.get("Source") == "Rotten Tomatoes":
                return rating.get("Value") or None
    except Exception:
        return None
    return None


async def _lookup(title: str, year: int | None, kind: str) -> dict[str, Any]:
    """Search TMDB once, then enrich from the matched id."""
    key = tmdb_key()
    route = "movie" if kind == "movie" else "tv"
    params = {"api_key": key, "query": title}
    if year:
        params["year" if route == "movie" else "first_air_date_year"] = str(year)
    async with httpx.AsyncClient(timeout=4.0) as client:
        response = await client.get(f"https://api.themoviedb.org/3/search/{route}", params=params)
        response.raise_for_status()
        results = (response.json() or {}).get("results") or []
        meta = _empty_metadata()
        meta["schema"] = CACHE_SCHEMA
        if not results:
            return meta
        first = results[0] or {}
        poster, backdrop = first.get("poster_path"), first.get("backdrop_path")
        meta["poster"] = f"{TMDB_IMAGE}/w342{poster}" if poster else None
        meta["backdrop"] = f"{TMDB_IMAGE}/w780{backdrop}" if backdrop else None
        if not first.get("id"):
            return meta
        try:
            detail = await _detail(client, route, first["id"], key)
        except Exception:  # art alone is better than no item
            return meta
        _apply_detail(meta, detail, route)
        imdb_id = meta["imdbId"]
        if imdb_id:
            meta["imdbRating"], meta["rtRating"] = await asyncio.gather(
                _imdb_rating(client, imdb_id, kind), _rt_rating(client, imdb_id)
            )
    return meta


async def art_for(rel: str, title: str, year: int | None, kind: str) -> dict[str, Any]:
    """Poster, art and detail metadata once per file; a broken network gives nulls."""
    cache = _load_matches()
    known = cache.get(rel)
    if isinstance(known, dict):
        if known.get("schema") == CACHE_SCHEMA:
            return _cached(known)
        if not tmdb_key() or _offline["until"] > time.time():
            return _cached(known)
    if not tmdb_key() or _offline["until"] > time.time():
        return _empty_metadata()
    try:
        found = await _lookup(title, year, kind)
    except Exception:  # the library must render even when TMDB is unreachable
        _offline["until"] = time.time() + 60
        return _empty_metadata()
    cache[rel] = found
    _save_matches()
    return _cached(found)


# ------------------------------------------------------------------ items


async def _library_item(path: Path, root: Path, st: os.stat_result, data: dict[str, Any]) -> dict[str, Any]:
    info = describe(path)
    rel = path.relative_to(root).as_posix()
    video = next(iter(_video(data)), {})
    codec = (video.get("codec_name") or "").lower()
    art = await art_for(rel, info["title"], info["year"], info["kind"])
    return {
        "id": item_id(rel),
        "title": info["title"],
        "year": info["year"],
        "kind": info["kind"],
        "size": int(st.st_size),
        "mtime": float(st.st_mtime),
        "duration": _duration(data),
        "container": info["container"],
        "videoCodec": codec,
        "audioTracks": len(_tracks(data, "audio")),
        "subtitleTracks": len(_tracks(data, "subtitle")),
        "poster": art.get("poster"),
        "backdrop": art.get("backdrop"),
        "description": art.get("description"),
        "tagline": art.get("tagline"),
        "genres": art.get("genres") or [],
        "tmdbRating": art.get("tmdbRating"),
        "tmdbVotes": art.get("tmdbVotes"),
        "imdbId": art.get("imdbId"),
        "imdbRating": art.get("imdbRating"),
        "rtRating": art.get("rtRating"),
        "certification": art.get("certification"),
        "director": art.get("director") or [],
        "cast": art.get("cast") or [],
        "trailer": art.get("trailer"),
        "playableDirect": info["container"] in PLAYABLE_CONTAINERS and codec in PLAYABLE_CODECS,
    }


async def library() -> dict[str, Any]:
    """Every video file, newest first."""
    root = download_dir()
    paths = await asyncio.to_thread(_walk, root)
    stats: list[tuple[Path, os.stat_result]] = []
    for path in paths:
        try:
            stats.append((path, path.stat()))
        except OSError:
            continue

    gate = asyncio.Semaphore(8)

    async def one(path: Path, st: os.stat_result) -> dict[str, Any]:
        async with gate:
            return await _library_item(path, root, st, await probe(path, st.st_mtime))

    items = list(await asyncio.gather(*(one(path, st) for path, st in stats)))
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return {"items": items}


async def detail(file_id: str) -> dict[str, Any]:
    """One item with its audio and subtitle tracks."""
    path = resolve(file_id)
    if path is None:
        raise HTTPException(404, "No such file")
    try:
        st = path.stat()
    except OSError:
        raise HTTPException(404, "No such file") from None
    data = await probe(path, st.st_mtime)
    if not data and ffprobe_bin() is None:
        raise HTTPException(503, "ffprobe is not installed")
    item = await _library_item(path, download_dir(), st, data)
    return {
        "item": item,
        "tracks": {"audio": _tracks(data, "audio"), "subtitles": _tracks(data, "subtitle")},
        "directUrl": f"/api/stream/{file_id}",
        "hlsUrl": None,
    }


# ----------------------------------------------------------------- stream


def parse_range(header: str | None, size: int) -> tuple[int, int, int]:
    """(start, end, status) for one byte range request."""
    if not header or not header.startswith("bytes="):
        return 0, size - 1, 200
    spec = header[6:].split(",")[0].strip()
    first, _, last = spec.partition("-")
    first, last = first.strip(), last.strip()
    try:
        if first:
            start = int(first)
            end = int(last) if last else size - 1
        elif last:
            count = int(last)
            if count <= 0:
                return 0, size - 1, 200
            start, end = max(0, size - count), size - 1
        else:
            return 0, size - 1, 200
    except ValueError:
        return 0, size - 1, 200
    if start < 0 or start >= size:
        return 0, -1, 416
    end = min(end, size - 1)
    if end < start:
        return 0, -1, 416
    return start, end, 206


def _body(path: Path, start: int, length: int) -> Iterator[bytes]:
    _active["streams"] += 1
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            while length > 0:
                chunk = fh.read(min(262144, length))
                if not chunk:
                    return
                length -= len(chunk)
                yield chunk
    finally:
        _active["streams"] -= 1


def active_streams() -> int:
    """Open range responses; the idle watchdog must not sleep through one."""
    return _active["streams"]


async def stream_response(file_id: str, request: Request) -> Response:
    """The file itself, with byte ranges the way a browser player wants them."""
    path = resolve(file_id)
    if path is None:
        raise HTTPException(404, "No such file")
    try:
        st = path.stat()
    except OSError:
        raise HTTPException(404, "No such file") from None
    size = st.st_size
    container = path.suffix.lower().lstrip(".")
    mtype = CONTENT_TYPES.get(container) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    etag = f'"{size:x}-{int(st.st_mtime):x}"'
    start, end, status = parse_range(request.headers.get("range"), size)
    if status == 416:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes", "ETag": etag},
        )

    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": mtype,
        "ETag": etag,
        "Cache-Control": "no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers)
    return StreamingResponse(
        _body(path, start, length), status_code=status, headers=headers, media_type=mtype
    )


# ------------------------------------------------------------- subtitles


async def subtitle_vtt(file_id: str, track: int) -> Response:
    """One subtitle stream as WebVTT."""
    path = resolve(file_id)
    if path is None:
        raise HTTPException(404, "No such file")
    binary = ffmpeg_bin()
    if not binary:
        raise HTTPException(404, "ffmpeg is not installed")
    if track < 0:
        raise HTTPException(404, "No such subtitle track")
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        f"0:s:{track}",
        "-f",
        "webvtt",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0 or not out.strip():
        raise HTTPException(404, "No such subtitle track")
    text = out.decode("utf-8", "replace")
    if not text.lstrip().startswith("WEBVTT"):
        text = "WEBVTT\n\n" + text
    return Response(text, media_type="text/vtt", headers={"Cache-Control": "no-store"})


# -------------------------------------------------------------------- art


def _gradient(file_id: str, wide: bool) -> tuple[int, int]:
    seed = hashlib.sha1(f"{file_id}:{'wide' if wide else 'tall'}".encode()).digest()
    return 20 + seed[0] % 26, 6 + seed[1] % 16


def artwork_svg(file_id: str, title: str, wide: bool) -> Response:
    """A graphite gradient card with the title, used when TMDB has no art."""
    width, height = (1280, 720) if wide else (400, 600)
    top, bottom = _gradient(file_id, wide)
    label = xml.escape((title or "Shelf Theater")[:38])
    size = 34 if wide else 24
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="rgb({top},{top},{top})"/>'
        f'<stop offset="1" stop-color="rgb({bottom},{bottom},{bottom})"/>'
        "</linearGradient></defs>"
        f'<rect width="{width}" height="{height}" fill="url(#g)"/>'
        f'<text x="{width // 2}" y="{height * 3 // 5}" fill="#e8e6e1" '
        f'font-family="ui-sans-serif, system-ui, sans-serif" font-size="{size}" '
        f'text-anchor="middle">{label}</text>'
        "</svg>"
    )
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


async def poster_svg(file_id: str) -> Response:
    path = resolve(file_id)
    if path is None:
        raise HTTPException(404, "No such file")
    return artwork_svg(file_id, describe(path)["title"], wide=False)


async def backdrop_svg(file_id: str) -> Response:
    path = resolve(file_id)
    if path is None:
        raise HTTPException(404, "No such file")
    return artwork_svg(file_id, describe(path)["title"], wide=True)


# --------------------------------------------------------------- sessions


def _kill(entry: dict[str, Any]) -> None:
    proc = entry.get("proc")
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def reap(now: float | None = None) -> None:
    """Stop sessions that have not been touched for the TTL."""
    now = time.time() if now is None else now
    for session, entry in list(_sessions.items()):
        if now - entry["last"] < SESSION_TTL:
            continue
        _sessions.pop(session, None)
        _kill(entry)
        await asyncio.to_thread(shutil.rmtree, entry["dir"], True)


async def _reap_forever() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            await reap()
        except Exception:
            pass


async def start_session(file_id: str, audio: int, start: float) -> dict[str, Any]:
    """Spawn a copy-only ffmpeg HLS session and return its playlist URL."""
    path = resolve(file_id)
    if path is None:
        raise HTTPException(404, "No such file")
    binary = ffmpeg_bin()
    if not binary:
        raise HTTPException(503, "ffmpeg is not installed")
    await reap()
    if len(_sessions) >= MAX_SESSIONS:
        raise HTTPException(429, "Too many player sessions, close one first")

    # the copy-only remux writes a second copy of the file; refuse when it cannot fit
    try:
        need = os.path.getsize(path) + (512 << 20)
        free = shutil.disk_usage(HLS_ROOT).free
    except OSError:
        need, free = 0, 1
    if need and free < need:
        raise HTTPException(507, "Not enough free disk for the player copy")

    session = secrets.token_hex(8)
    directory = HLS_ROOT / session
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(500, f"Cannot create {directory}: {exc}") from exc
    log = directory / "ffmpeg.log"
    args = [
        binary,
        # generate slightly ahead of playback instead of remuxing the whole film
        "-readrate",
        "1.2",
        "-ss",
        f"{max(0.0, float(start)):.3f}",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        f"0:a:{max(0, int(audio))}?",
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        "2",
        "-hls_playlist_type",
        "event",
        "-hls_flags",
        "independent_segments",
        "-hls_segment_filename",
        str(directory / "seg%05d.ts"),
        str(directory / "index.m3u8"),
    ]
    handle = log.open("wb")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=handle,
        )
    except OSError as exc:
        handle.close()
        await asyncio.to_thread(shutil.rmtree, directory, True)
        raise HTTPException(500, f"ffmpeg did not start: {exc}") from exc
    handle.close()

    _sessions[session] = {"pid": proc.pid, "dir": directory, "last": time.time(), "log": log, "proc": proc}

    return {"session": session, "playlist": f"/hls/{session}/index.m3u8"}


async def hls_file(session: str, name: str) -> Response:
    """A playlist or segment for a live session."""
    entry = _sessions.get(session)
    if entry is None:
        raise HTTPException(404, "Unknown session")
    if name != "index.m3u8" and not re.fullmatch(r"seg\d{5}\.ts", name):
        raise HTTPException(404, "No such file")
    entry["last"] = time.time()
    target = (entry["dir"] / name).resolve()
    if not target.is_relative_to(entry["dir"].resolve()):
        raise HTTPException(404, "No such file")
    if not target.is_file():
        # a segment can be requested a beat before ffmpeg writes it
        for _ in range(40):
            if target.is_file():
                break
            if entry["proc"].returncode not in (None, 0):
                raise HTTPException(500, f"ffmpeg failed, see {entry['log']}")
            await asyncio.sleep(0.1)
        if not target.is_file():
            raise HTTPException(404, "Not ready")
    mtype = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
    return FileResponse(target, media_type=mtype, headers={"Cache-Control": "no-store"})


async def stop_session(session: str) -> dict[str, Any]:
    """Kill a session and delete its segments."""
    entry = _sessions.pop(session, None)
    if entry is None:
        raise HTTPException(404, "Unknown session")
    _kill(entry)
    await asyncio.to_thread(shutil.rmtree, entry["dir"], True)
    return {"ok": True}


# --------------------------------------------------------------- lifecycle


def _clear() -> None:
    """Kill every session and empty the HLS root."""
    for entry in list(_sessions.values()):
        _kill(entry)
        shutil.rmtree(entry["dir"], ignore_errors=True)
    _sessions.clear()
    shutil.rmtree(HLS_ROOT, ignore_errors=True)


async def startup() -> None:
    """Drop leftovers from a previous run and start the TTL reaper."""
    global _reaper
    await asyncio.to_thread(_clear)
    if _reaper is not None and not _reaper.done():
        _reaper.cancel()
    _reaper = asyncio.create_task(_reap_forever())


async def shutdown() -> None:
    """Kill the reaper and every ffmpeg still writing."""
    global _reaper
    if _reaper is not None:
        _reaper.cancel()
        _reaper = None
    await asyncio.to_thread(_clear)


atexit.register(_clear)

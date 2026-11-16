"""Shelf for Stremio - exposes a folder of movies and shows as a Stremio addon.

Files are streamed straight off the disk with byte-range support, so seeking
works in every Stremio client. Posters are generated on the fly with Pillow and
cached, because the library has none.
"""

import asyncio
import base64
import hashlib
import html
import io
import json
import mimetypes
import os
import re
import socket
import time
import urllib.parse

import httpx

# The Pi's IPv6 route is flaky; prefer A records so outbound metadata calls do not
# fail with BrokenResourceError on the first IPv6 attempt.
_getaddrinfo = socket.getaddrinfo


def _v4_first(*args, **kwargs):
    return sorted(_getaddrinfo(*args, **kwargs), key=lambda row: row[0] != socket.AF_INET)


socket.getaddrinfo = _v4_first
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.realpath(os.environ.get("SHELF_ROOT", "/media/Movies_and_Shows"))
CACHE = os.path.expanduser("~/.cache/shelf-posters")
MATCH = os.path.join(CACHE, "matches.json")
MISS_TTL = 24 * 3600
CINEMETA = "https://v3-cinemeta.strem.io"
# release titles that do not equal their IMDb name; left side is what the file says
ALIASES = {"satluj": "Punjab '95"}
IDLE_SECONDS = int(os.environ.get("SHELF_IDLE_SECONDS", "900"))
VIDEO = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".mpg", ".mpeg", ".ts"}
SUBS = {".srt", ".vtt", ".ass", ".ssa"}
EP = re.compile(r"[sS](\d{1,2})[ ._-]?[eE](\d{1,3})")
YEAR = re.compile(r"(19|20)\d{2}")
JUNK = re.compile(
    r"\b(2160p|1080p|720p|480p|4k|uhd|web[- ]?dl|webrip|hdtv|bluray|brrip|dvdrip|hdrip|"
    r"x264|x265|h264|h265|hevc|aac|ddp?5[. ]?1|dts|10bit|hdr|sdr|proper|repack|"
    r"amzn|nf|zee5|hdhub4u|9xmovies|xbet|esub|multi|dual audio|"
    r"esubs?|msub|subbed|dubbed|remux|atmos|truehd|eac3|ac3|opus|flac|xvid|divx|av1|"
    r"extended|uncut|hq|fullhd|full hd|hd|web|dl|5[. ]1|7[. ]1|2[. ]0)\b",
    re.I,
)

# upload sites and urls leak into file names; they are never part of a title
SITE = re.compile(
    r"(www\.)?[\w-]+\.(com|net|org|ws|in|mx|to|cc|link|xyz|top|vip|club|live|me|tv)\b",
    re.I,
)
OVERRIDES_PATH = os.path.expanduser("~/.config/shelf/overrides.json")
_overrides = {"at": 0.0, "data": {}}


def overrides() -> dict:
    """Per-file metadata fixes: {"<filename>": {"title","year","imdb","tmdb"}}."""
    try:
        mtime = os.path.getmtime(OVERRIDES_PATH)
    except OSError:
        return {}
    if _overrides["at"] != mtime:
        try:
            data = json.load(open(OVERRIDES_PATH))
            _overrides.update(at=mtime, data=data if isinstance(data, dict) else {})
        except Exception:
            _overrides.update(at=mtime, data={})
    return _overrides["data"]


def override_for(name: str) -> dict:
    data = overrides()
    return data.get(name) or data.get(os.path.basename(name)) or {}

app = FastAPI(title="Shelf for Stremio", docs_url=None, redoc_url=None)
last_seen = time.time()
streams = 0


@app.middleware("http")
async def cors(request: Request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "Content-Range, Accept-Ranges, Content-Length"
    return response


def token(rel: str) -> str:
    raw = base64.urlsafe_b64encode(rel.encode()).decode().rstrip("=")
    return "shelf" + raw


def untoken(tok: str) -> str:
    if not tok.startswith("shelf"):
        raise HTTPException(404, "bad id")
    raw = tok[5:]
    raw += "=" * (-len(raw) % 4)
    try:
        rel = base64.urlsafe_b64decode(raw.encode()).decode()
    except Exception:
        raise HTTPException(404, "bad id")
    path = os.path.realpath(os.path.join(ROOT, rel))
    if not (path == ROOT or path.startswith(ROOT + os.sep)):
        raise HTTPException(403, "outside the share")
    if not os.path.exists(path):
        raise HTTPException(404, "gone")
    return path


def clean_title(name: str):
    forced = override_for(name)
    if forced.get("title"):
        return str(forced["title"]), str(forced.get("year") or "") or None

    stem = SITE.sub(" ", os.path.splitext(name)[0])
    stem = stem.replace(".", " ").replace("_", " ")
    year = None
    m = YEAR.search(stem)
    if m:
        year = m.group(0)
        head = stem[: m.start()]
        head = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", head)
        head = re.sub(r"[\(\[\{]\s*$", "", head)
        head = re.sub(r"\s+", " ", head).strip(" -")
        if head:
            stem = head
        else:
            stem = stem[m.end():]
            stem = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", stem)
    stem = JUNK.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -[](){}")
    stem = re.sub(r"^[-\s]+|[-\s]+$", "", stem)
    title = stem.title() if stem.isupper() or stem.islower() else stem
    return (title or os.path.splitext(name)[0]), year


DEFAULT_NAME = "Shelf"


def encode_cfg(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cfg(tok: str) -> dict:
    try:
        raw = tok[2:] if tok.startswith("c_") else tok
        raw += "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@app.middleware("http")
async def cfg_prefix(request: Request, call_next):
    """`/c/<cfg>/...` carries per-install settings; strip it before routing."""
    path = request.scope.get("path", "")
    match = re.match(r"^/c/([A-Za-z0-9_-]+)(/.*)?$", path)
    if match:
        request.state.cfg_token = match.group(1)
        request.state.cfg = decode_cfg(match.group(1))
        request.scope["path"] = match.group(2) or "/"
    else:
        request.state.cfg_token = None
        request.state.cfg = {}
    response = await call_next(request)
    route = request.scope.get("path", "")
    if route.startswith("/catalog/") or route.startswith("/meta/") or route == "/manifest.json":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def cfg_name(request: Request) -> str:
    cfg = getattr(request.state, "cfg", None) or {}
    name = str(cfg.get("n") or "").strip()
    return name[:40] or DEFAULT_NAME


def base_url(request: Request) -> str:
    cfg = getattr(request.state, "cfg", None) or {}
    override = cfg.get("b") or os.environ.get("SHELF_PUBLIC_URL")
    if override:
        base = str(override).rstrip("/")
    else:
        host = request.headers.get("host") or "127.0.0.1:7020"
        base = f"http://{host}"
    tok = getattr(request.state, "cfg_token", None)
    return f"{base}/c/{tok}" if tok else base


# ------------------------------------------------------------------ real covers

_matches = None
_client = httpx.AsyncClient(timeout=8.0)


def _load_matches():
    global _matches
    if _matches is None:
        try:
            _matches = json.load(open(MATCH))
        except Exception:
            _matches = {}
    return _matches


def _save_matches():
    os.makedirs(CACHE, exist_ok=True)
    tmp = MATCH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(_matches, fh)
    os.replace(tmp, MATCH)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


META_TTL = 6 * 3600


async def get_json(url: str, params: dict | None = None, tries: int = 4) -> dict:
    """Outbound metadata calls on this Pi fail intermittently; retry with backoff."""
    delay = 0.2
    for attempt in range(tries):
        try:
            res = await _client.get(url, params=params)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        if attempt < tries - 1:
            await asyncio.sleep(delay)
            delay *= 2
    return {}


TMDB_ENV = os.path.expanduser("~/.config/shelf/tmdb.env")
TMDB_ENV_LEGACY = "/home/arnav/tmdb-addon/.env"
TMDB_IMG = "https://image.tmdb.org/t/p"


def tmdb_key() -> str:
    key = os.environ.get("TMDB_API", "").strip()
    if key:
        return key
    for path in (TMDB_ENV, TMDB_ENV_LEGACY):
        try:
            with open(path) as fh:
                for line in fh:
                    if line.startswith("TMDB_API="):
                        value = line.split("=", 1)[1].strip()
                        if value:
                            return value
        except OSError:
            continue
    return ""


async def tmdb_search(kind: str, title: str, year: str | None) -> int | None:
    key = tmdb_key()
    if not key:
        return None
    path = "movie" if kind == "movie" else "tv"
    params = {"api_key": key, "query": title, "include_adult": "false"}
    if year:
        params["year" if path == "movie" else "first_air_date_year"] = year
    payload = await get_json(f"https://api.themoviedb.org/3/search/{path}", params=params)
    hits = payload.get("results") or []
    want = norm(title)
    exact = [h for h in hits if norm(h.get("title") or h.get("name") or "") == want]
    pool = exact or hits
    if year:
        same = [h for h in pool if str(h.get("release_date") or h.get("first_air_date") or "").startswith(year)]
        pool = same or pool
    return int(pool[0]["id"]) if pool else None


async def tmdb_details(kind: str, tmdb_id: int) -> dict:
    key = tmdb_key()
    if not key or not tmdb_id:
        return {}
    path = "movie" if kind == "movie" else "tv"
    d = await get_json(
        f"https://api.themoviedb.org/3/{path}/{tmdb_id}",
        params={
            "api_key": key,
            "append_to_response": "credits,images",
            "include_image_language": "en,null",
        },
    )
    if not isinstance(d, dict) or d.get("success") is False:
        return {}
    crew = ((d.get("credits") or {}).get("crew") or [])
    runtime = d.get("runtime") or ((d.get("episode_run_time") or [None])[0])
    released = d.get("release_date") or d.get("first_air_date") or ""
    out = {
        "description": d.get("overview") or "",
        "genres": [g.get("name") for g in d.get("genres") or [] if g.get("name")],
        "cast": [c.get("name") for c in ((d.get("credits") or {}).get("cast") or [])][:8],
        "director": [c.get("name") for c in crew if c.get("job") == "Director"][:3],
        "background": f"{TMDB_IMG}/w1280{d['backdrop_path']}" if d.get("backdrop_path") else "",
        "poster": f"{TMDB_IMG}/w500{d['poster_path']}" if d.get("poster_path") else "",
        "released": released,
        "releaseInfo": released[:4],
        "links": [],
    }
    logos = ((d.get("images") or {}).get("logos") or [])
    if logos:
        best = next((l for l in logos if l.get("iso_639_1") == "en"), logos[0])
        if best.get("file_path"):
            out["logo"] = f"{TMDB_IMG}/w300{best['file_path']}"
    if runtime:
        out["runtime"] = f"{runtime} min"
    vote = d.get("vote_average")
    if vote:
        out["links"].append({
            "name": f"TMDB {round(float(vote), 1)}",
            "category": "tmdb",
            "url": f"https://www.themoviedb.org/{path}/{tmdb_id}",
        })
    return out


async def find_cover(kind: str, title: str, year: str | None, key: str | None = None) -> dict:
    """Look the title up in Cinemeta. Returns {'poster': url, 'id': imdb}."""
    cache = _load_matches()
    key = f"{kind}|{title}|{year or ''}"
    hit = cache.get(key)
    if isinstance(hit, str):
        hit = {"poster": hit, "id": "", "tmdb": 0, "at": 0}
        cache[key] = hit
    forced = override_for(key) if key else {}
    if forced.get("imdb"):
        found = {"poster": f"https://images.metahub.space/poster/medium/{forced['imdb']}/img",
                 "id": str(forced["imdb"]), "tmdb": int(forced.get("tmdb") or 0), "at": time.time()}
        cache[key] = found
        _save_matches()
        return found

    if isinstance(hit, dict):
        empty = not hit.get("poster") and not hit.get("id") and not hit.get("tmdb")
        stale = empty and time.time() - float(hit.get("at") or 0) > MISS_TTL
        if not stale:
            return hit

    url = f"{CINEMETA}/catalog/{kind}/top/search={urllib.parse.quote(title)}.json"
    found = {"poster": "", "id": "", "tmdb": 0}
    try:
        payload = await get_json(url)
        metas = (payload.get("metas") or [])
        want = norm(title)
        exact = [m for m in metas if norm(m.get("name") or "") == want]
        if not exact:
            alias = ALIASES.get(title.strip().lower())
            if alias:
                exact = [m for m in metas if norm(m.get("name") or "") == norm(alias)]
        pool = exact
        if year:
            same = [m for m in pool if (m.get("releaseInfo") or "").startswith(year)]
            pool = same or pool
        if pool:
            best = pool[0]
            poster = best.get("poster") or ""
            if "/poster/small/" in poster:
                poster = poster.replace("/poster/small/", "/poster/medium/")
            found = {"poster": poster, "id": best.get("id") or "", "tmdb": 0}
    except Exception:
        pass

    if not found["id"]:
        tmdb_id = await tmdb_search(kind, title, year)
        if tmdb_id:
            extra = await tmdb_details(kind, tmdb_id)
            found["tmdb"] = tmdb_id
            found["poster"] = extra.get("poster") or found["poster"]

    found["at"] = time.time()
    cache[key] = found
    _save_matches()
    return found


async def cover_url(request: Request, kind: str, title: str, year: str | None, rel: str) -> str:
    hit = await find_cover(kind, title, year, rel)
    return hit.get("poster") or poster_url(request, title, year, rel)


async def details(kind: str, title: str, year: str | None, key: str | None = None) -> dict:
    """Detail-page metadata: Cinemeta first, TMDB fills the gaps."""
    cache = _load_matches()
    mkey = f"meta|{kind}|{title}|{year or ''}|{key or ''}"
    cached = cache.get(mkey)
    if isinstance(cached, dict) and cached.get("at", 0) + META_TTL > time.time():
        return cached.get("data") or {}

    forced = override_for(key) if key else {}
    hit = await find_cover(kind, title, year, key)
    if forced.get("tmdb") and not hit.get("tmdb"):
        hit["tmdb"] = int(forced["tmdb"])
    imdb = hit.get("id")

    cm: dict = {}
    if imdb:
        try:
            payload = await get_json(f"{CINEMETA}/meta/{kind}/{imdb}.json")
            meta = payload.get("meta") or {}
            if meta:
                cm = {
                    "name": meta.get("name") or "",
                    "description": meta.get("description") or "",
                    "runtime": meta.get("runtime") or "",
                    "genres": meta.get("genres") or meta.get("genre") or [],
                    "cast": meta.get("cast") or [],
                    "director": meta.get("director") or [],
                    "writer": meta.get("writer") or [],
                    "country": meta.get("country") or "",
                    "released": meta.get("released") or "",
                    "releaseInfo": meta.get("releaseInfo") or meta.get("year") or "",
                    "imdbRating": meta.get("imdbRating") or "",
                    "background": meta.get("background") or "",
                    "logo": meta.get("logo") or "",
                    "links": meta.get("links") or [],
                    "trailers": meta.get("trailers") or [],
                    "trailerStreams": meta.get("trailerStreams") or [],
                }
        except Exception:
            cm = {}

    tmdb: dict = {}
    needs_tmdb = not cm or not cm.get("background") or not cm.get("cast") or not cm.get("description")
    if needs_tmdb:
        tmdb_id = hit.get("tmdb") or await tmdb_search(kind, title, year)
        if tmdb_id:
            tmdb = await tmdb_details(kind, tmdb_id)
            if not hit.get("tmdb"):
                hit["tmdb"] = tmdb_id
                _save_matches()

    merged: dict = {}
    for key in (
        "name", "description", "runtime", "genres", "cast", "director", "writer",
        "country", "released", "releaseInfo", "imdbRating", "background", "logo",
        "links", "trailers", "trailerStreams",
    ):
        merged[key] = cm.get(key) or tmdb.get(key) or ""
    if not merged["links"] and tmdb.get("links"):
        merged["links"] = tmdb["links"]
    cache[mkey] = {"at": time.time(), "data": merged}
    _save_matches()
    return merged


# ---------------------------------------------------------------- library scan


ARIA2_CONF = os.path.expanduser("~/.config/aria2/aria2.conf")
PARTIAL_TTL = 5.0
_partial = {"at": 0.0, "paths": set(), "rpc": False}


def aria2_secret() -> str:
    try:
        with open(ARIA2_CONF) as fh:
            for line in fh:
                if line.startswith("rpc-secret="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


async def aria2_open_paths() -> tuple[set[str], bool]:
    """Files aria2 is still working on. Cached briefly so one scan is one RPC."""
    now = time.time()
    if now - _partial["at"] < PARTIAL_TTL:
        return _partial["paths"], _partial["rpc"]

    secret = aria2_secret()
    paths: set[str] = set()
    ok = False
    if secret:
        calls = (
            ("aria2.tellActive", [["gid", "status", "files"]]),
            ("aria2.tellWaiting", [0, 200, ["gid", "status", "files"]]),
            ("aria2.tellStopped", [0, 200, ["gid", "status", "files"]]),
        )
        for method, args in calls:
            try:
                res = await _client.post(
                    "http://127.0.0.1:6800/jsonrpc",
                    json={"jsonrpc": "2.0", "id": 1, "method": method,
                          "params": [f"token:{secret}"] + args},
                )
                for item in res.json().get("result") or []:
                    if item.get("status") not in ("active", "waiting", "paused"):
                        continue
                    for part in item.get("files") or []:
                        path = part.get("path") or ""
                        if path:
                            paths.add(os.path.realpath(path))
                ok = True
            except Exception:
                continue

    _partial.update(at=now, paths=paths, rpc=ok)
    return paths, ok


def is_partial(path: str, open_paths: set[str], rpc_ok: bool) -> bool:
    """True while a download is still in flight for this file."""
    real = os.path.realpath(path)
    if real in open_paths:
        return True
    if path.endswith(".!qB") or os.path.exists(path + ".part") or os.path.exists(path + ".!qB"):
        return True
    if os.path.exists(path + ".aria2"):
        # a control file is only proof of work in progress when aria2 still knows it;
        # finished downloads can leave a stale sidecar behind
        return not rpc_ok
    return False


def scan(open_paths: set[str], rpc_ok: bool):
    movies, shows = [], {}

    def usable(path: str) -> bool:
        return not is_partial(path, open_paths, rpc_ok)

    for entry in sorted(os.scandir(ROOT), key=lambda e: e.name.lower()):
        if entry.name.startswith("."):
            continue
        if entry.is_file():
            if os.path.splitext(entry.name)[1].lower() in VIDEO and usable(entry.path):
                movies.append(entry)
            continue
        if not entry.is_dir():
            continue
        episodes = []
        for sub in os.scandir(entry.path):
            if sub.is_file() and os.path.splitext(sub.name)[1].lower() in VIDEO:
                if EP.search(sub.name) and usable(sub.path):
                    episodes.append(sub)
        if episodes:
            shows[entry.path] = episodes
        else:
            for sub in os.scandir(entry.path):
                if sub.is_file() and os.path.splitext(sub.name)[1].lower() in VIDEO:
                    if usable(sub.path):
                        movies.append(sub)

    movies.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    return movies, shows


@app.get("/manifest.json")
async def manifest(request: Request):
    return JSONResponse(
        {
            "id": "org.shelf.media",
            "version": "1.2.0",
            "name": cfg_name(request),
            "description": f"Movies and shows from {cfg_name(request)}.",
            "types": ["movie", "series"],
            "resources": [
                {"name": "catalog", "types": ["movie", "series"], "idPrefixes": ["shelf"]},
                {"name": "meta", "types": ["movie", "series"], "idPrefixes": ["shelf"]},
                {"name": "stream", "types": ["movie", "series"], "idPrefixes": ["shelf"]},
                {"name": "subtitles", "types": ["movie", "series"], "idPrefixes": ["shelf"]},
            ],
            "catalogs": [
                {"type": "movie", "id": "shelf", "name": "Shelf",
                 "extra": [{"name": "search", "isRequired": False}]},
                {"type": "series", "id": "shelf", "name": "Shelf Series",
                 "extra": [{"name": "search", "isRequired": False}]},
            ],
            "idPrefixes": ["shelf"],
            "logo": f"{base_url(request)}/poster/shelf.png",
            "behaviorHints": {"adult": False, "configurable": True, "configurationRequired": False},
        }
    )


@app.get("/catalog/{kind}/{cid}.json")
@app.get("/catalog/{kind}/{cid}/{extra}.json")
async def catalog(kind: str, cid: str, request: Request, extra: str = ""):
    last = time.time()
    m = re.search(r"search=([^&.]+)", extra)
    needle = m.group(1).replace("%20", " ").lower() if m else ""

    open_paths, rpc_ok = await aria2_open_paths()
    movies, shows = await asyncio.to_thread(scan, open_paths, rpc_ok)

    if kind == "movie":
        picked = []
        for entry in movies:
            title, year = clean_title(entry.name)
            if needle and needle not in title.lower():
                continue
            picked.append((entry, title, year, os.path.relpath(entry.path, ROOT), "movie"))
    else:
        picked = []
        for path, _episodes in shows.items():
            title, year = clean_title(os.path.basename(path))
            if needle and needle not in title.lower():
                continue
            picked.append((path, title, year, os.path.relpath(path, ROOT), "series"))

    covers = await asyncio.gather(
        *(cover_url(request, kind_, title, year, rel) for _e, title, year, rel, kind_ in picked),
        return_exceptions=True,
    )
    metas = []
    for (entry, title, year, rel, kind_), poster in zip(picked, covers):
        metas.append({
            "id": token(rel),
            "type": kind_,
            "name": title,
            "poster": poster if isinstance(poster, str) else "",
            "posterShape": "poster",
            "releaseInfo": year or "",
        })
    return JSONResponse({"metas": metas[:300]})


@app.get("/meta/{kind}/{mid}.json")
async def meta(kind: str, mid: str, request: Request):
    path = untoken(mid)
    title, year = clean_title(os.path.basename(path))
    rel = os.path.relpath(path, ROOT)
    out = {
        "id": mid,
        "type": kind,
        "name": title,
        "poster": await cover_url(request, kind, title, year, rel),
        "posterShape": "poster",
        "releaseInfo": year or "",
        "description": os.path.basename(path),
    }
    extra = await details(kind, title, year, rel)
    for key in (
        "description", "runtime", "genres", "cast", "director", "writer",
        "country", "released", "imdbRating", "background", "logo", "links",
        "trailers", "trailerStreams",
    ):
        if extra.get(key):
            out[key] = extra[key]
    if extra.get("releaseInfo") and not out.get("releaseInfo"):
        out["releaseInfo"] = extra["releaseInfo"]
    if kind == "series":
        videos = []
        for ep in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if not ep.is_file() or os.path.splitext(ep.name)[1].lower() not in VIDEO:
                continue
            hit = EP.search(ep.name)
            season, number = (int(hit.group(1)), int(hit.group(2))) if hit else (1, len(videos) + 1)
            label, _ = clean_title(ep.name)
            videos.append({
                "id": f"{mid}:{season}:{number}",
                "title": label,
                "season": season,
                "episode": number,
            })
        out["videos"] = videos
    return JSONResponse({"meta": out})


@app.get("/stream/{kind}/{sid}.json")
async def stream(kind: str, sid: str, request: Request):
    parts = sid.split(":")
    path = untoken(parts[0])
    if kind == "series" and len(parts) == 3:
        season, episode = int(parts[1]), int(parts[2])
        for ep in os.scandir(path):
            hit = EP.search(ep.name)
            if hit and (int(hit.group(1)), int(hit.group(2))) == (season, episode):
                path = ep.path
                break
    rel = os.path.relpath(path, ROOT)
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    url = f"{base_url(request)}/play/{token(rel)}/{urllib.parse.quote(name, safe='')}"
    return JSONResponse({
        "streams": [{
            "name": "Shelf",
            "title": f"{name}\n{os.path.getsize(path) / 2**30:.2f} GB",
            "url": url,
            "behaviorHints": {
                "notWebReady": True,
                "filename": name,
                "videoSize": os.path.getsize(path),
            },
        }]
    })


@app.get("/subtitles/{kind}/{sid}.json")
@app.get("/subtitles/{kind}/{sid}/{extra}.json")
async def subtitles(kind: str, sid: str, request: Request, extra: str = ""):
    parts = sid.split(":")
    path = untoken(parts[0])
    if kind == "series" and len(parts) == 3:
        season, episode = int(parts[1]), int(parts[2])
        for ep in os.scandir(path):
            hit = EP.search(ep.name)
            if hit and (int(hit.group(1)), int(hit.group(2))) == (season, episode):
                path = ep.path
                break
    stem = os.path.splitext(path)[0]
    out = []
    for extra_file in sorted(os.scandir(os.path.dirname(path) or "."), key=lambda e: e.name):
        if not extra_file.is_file():
            continue
        name, ext = os.path.splitext(extra_file.name)
        if ext.lower() not in SUBS or not name.startswith(os.path.basename(stem)):
            continue
        rel = os.path.relpath(extra_file.path, ROOT)
        lang = "eng"
        tag = name[len(os.path.basename(stem)):].strip(" ._-[]")
        if tag:
            lang = tag[:3].lower()
        out.append({
            "id": token(rel),
            "url": f"{base_url(request)}/subs/{token(rel)}/{urllib.parse.quote(extra_file.name, safe='')}",
            "lang": lang,
        })
    return JSONResponse({"subtitles": out})


# ------------------------------------------------------------------- file data


def file_iter(path: str, start: int, length: int, chunk: int = 1 << 20):
    with open(path, "rb") as fh:
        fh.seek(start)
        left = length
        while left > 0:
            data = fh.read(min(chunk, left))
            if not data:
                break
            left -= len(data)
            yield data


@app.api_route("/play/{tok}/{name}", methods=["GET", "HEAD"])
async def play(tok: str, name: str, request: Request):
    global last_seen, streams
    path = untoken(tok)
    size = os.path.getsize(path)
    mtype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if mtype in ("application/x-matroska",):
        mtype = "video/x-matroska"

    rng = request.headers.get("range")
    start, end = 0, size - 1
    status = 200
    if rng and rng.startswith("bytes="):
        spec = rng[6:].split(",")[0].strip()
        a, _, b = spec.partition("-")
        if a.strip():
            start = int(a)
            end = int(b) if b.strip() else size - 1
        elif b.strip():  # suffix range: last N bytes
            start = max(0, size - int(b))
        end = min(end, size - 1)
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206

    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": mtype,
        "Cache-Control": "no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers)

    last_seen = time.time()
    streams += 1
    body = file_iter(path, start, length)

    def guarded():
        global streams
        try:
            yield from body
        finally:
            streams -= 1
            last_seen = time.time()

    return StreamingResponse(guarded(), status_code=status, headers=headers, media_type=mtype)


def _poster_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, width: int, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and words:
        last = lines[-1]
        while draw.textlength(last + "…", font=font) > width and " " in last:
            last = last.rsplit(" ", 1)[0]
        lines[-1] = last + "…"
    return lines


def make_poster(title: str, year, key: str) -> bytes:
    """A typographic poster in the page's palette, generated once and cached."""
    digest = hashlib.sha1(key.encode()).digest()
    wash = 18 + digest[0] % 14
    image = Image.new("RGB", (400, 600), (11, 11, 13))
    draw = ImageDraw.Draw(image)

    for step in range(600):
        ratio = step / 600
        shade = int(wash * (1 - ratio) ** 2)
        draw.line([(0, step), (400, step)], fill=(11 + shade, 11 + shade // 2, 13))

    amber = (240, 168, 60)
    for index in range(3):
        width_line = 34 if index < 2 else 22
        draw.rounded_rectangle(
            [40, 44 + index * 14, 40 + width_line, 50 + index * 14],
            radius=3, fill=amber,
        )

    body = _poster_font(34, bold=True)
    lines = _wrap(draw, title, body, 320, 5)
    top = 300 - len(lines) * 22
    for offset, line in enumerate(lines):
        draw.text((40, top + offset * 44), line, font=body, fill=(240, 240, 243))

    if year:
        small = _poster_font(20)
        draw.text((40, top + len(lines) * 44 + 10), str(year), font=small, fill=(178, 180, 188))

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def make_logo() -> bytes:
    image = Image.new("RGB", (512, 512), (11, 11, 13))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, 511, 511], radius=96, fill=(13, 13, 16))
    amber = (240, 168, 60)
    for index, width_line in enumerate((236, 236, 152)):
        draw.rounded_rectangle(
            [138, 176 + index * 62, 138 + width_line, 196 + index * 62],
            radius=10, fill=amber,
        )
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


@app.get("/poster/{key}.png")
async def poster(key: str):
    if key == "shelf":
        return Response(content=make_logo(), media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})
    cached = os.path.join(CACHE, key + ".png")
    if os.path.exists(cached):
        return Response(content=open(cached, "rb").read(), media_type="image/png")
    for entry in os.scandir(ROOT):
        rel = entry.name
        if hashlib.sha1(rel.encode()).hexdigest()[:16] == key:
            title, year = clean_title(entry.name)
            body = make_poster(title, year, rel)
            try:
                os.makedirs(CACHE, exist_ok=True)
                with open(cached, "wb") as fh:
                    fh.write(body)
            except OSError:
                pass
            return Response(content=body, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=604800"})
    raise HTTPException(404, "no poster")


@app.get("/subs/{tok}/{name}")
async def subs(tok: str, name: str):
    path = untoken(tok)
    mtype = mimetypes.guess_type(name)[0] or "text/plain"
    return Response(content=open(path, "rb").read(), media_type=mtype)


@app.get("/health")
async def health(request: Request):
    """Fast diagnosis: what the addon sees and what it is hiding right now."""
    open_paths, rpc_ok = await aria2_open_paths()
    movies, shows = await asyncio.to_thread(scan, open_paths, rpc_ok)
    stale, hidden = [], []
    for entry in os.scandir(ROOT):
        if not entry.is_file():
            continue
        if os.path.splitext(entry.name)[1].lower() not in VIDEO:
            continue
        real = os.path.realpath(entry.path)
        partial = is_partial(entry.path, open_paths, rpc_ok)
        if partial and real not in open_paths:
            stale.append(entry.name)
        if partial:
            hidden.append(entry.name)
    review = []
    for entry in movies:
        title, year = clean_title(entry.name)
        hit = await find_cover("movie", title, year, os.path.relpath(entry.path, ROOT))
        if not hit.get("id") and not hit.get("tmdb"):
            review.append(entry.name)
    for path in shows:
        title, year = clean_title(os.path.basename(path))
        hit = await find_cover("series", title, year, os.path.relpath(path, ROOT))
        if not hit.get("id") and not hit.get("tmdb"):
            review.append(os.path.basename(path))

    return JSONResponse({
        "root": ROOT,
        "movies": len(movies),
        "series": len(shows),
        "needs_review": review,
        "aria2": {"rpc": rpc_ok, "open": len(open_paths)},
        "hidden": hidden,
        "stale_sidecars": stale,
        "hint": "stale_sidecars are files aria2 no longer tracks; they are hidden only if the RPC is down",
    })


@app.get("/configure")
async def configure(request: Request, server: str = "", name: str = ""):
    host = request.headers.get("host") or "127.0.0.1:7020"
    home = f"http://{host}"
    base = (server or home).strip().rstrip("/")
    if "/c/" in base:
        base = base.split("/c/")[0]
    if not re.match(r"^https?://[^/\s]+$", base):
        base = home
    label = (name or DEFAULT_NAME).strip()[:40] or DEFAULT_NAME
    token = encode_cfg({"b": base, "n": label})
    manifest = f"{base}/c/{token}/manifest.json"
    install = "stremio://" + urllib.parse.quote(manifest, safe="")
    esc = html.escape

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(label)} · configure</title>
<style>
  :root{{--bg:#0c0c0d;--surface:#121214;--line:rgba(255,255,255,.08);--text:#ededef;--muted:#8b8d95;--amber:#e8a33d}}
  *{{box-sizing:border-box}}
  body{{margin:0;min-height:100dvh;background:var(--bg);color:var(--text);
       font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
       display:flex;align-items:center;justify-content:center;padding:32px}}
  main{{width:100%;max-width:560px}}
  h1{{font-size:18px;font-weight:500;margin:0 0 4px}}
  p.lead{{color:var(--muted);margin:0 0 24px;font-size:13px}}
  form{{display:grid;gap:12px;padding:20px;background:var(--surface);
        border:1px solid var(--line);border-radius:14px}}
  label{{display:grid;gap:6px;font-size:12px;color:var(--muted)}}
  input{{height:38px;padding:0 10px;border-radius:8px;border:1px solid var(--line);
         background:#0c0c0d;color:var(--text);font:inherit;font-size:14px}}
  input:focus{{outline:2px solid var(--amber);outline-offset:1px}}
  button{{height:38px;border:0;border-radius:8px;background:var(--amber);color:#16130c;
          font:inherit;font-weight:500;cursor:pointer}}
  .out{{margin-top:20px;display:grid;gap:8px}}
  .row{{display:grid;gap:4px}}
  .row span{{font-size:11px;color:var(--muted)}}
  code{{display:block;padding:10px;border:1px solid var(--line);border-radius:8px;
        background:var(--surface);font:12px/1.4 ui-monospace,monospace;word-break:break-all}}
  a.install{{display:inline-flex;align-items:center;justify-content:center;height:38px;
             margin-top:6px;border-radius:8px;background:#202124;border:1px solid var(--line);
             color:var(--text);text-decoration:none;font-size:13px}}
  footer{{margin-top:18px;font-size:12px;color:var(--muted)}}
</style></head>
<body><main>
  <h1>{esc(label)}</h1>
  <p class="lead">Point this addon at your server, then install it in Stremio.</p>
  <form method="get" action="/configure">
    <label>Server URL
      <input name="server" value="{esc(base)}" spellcheck="false" autocomplete="off">
    </label>
    <label>Library name
      <input name="name" value="{esc(label)}" maxlength="40" autocomplete="off">
    </label>
    <button type="submit">Update links</button>
  </form>
  <div class="out">
    <div class="row"><span>Manifest URL</span><code>{esc(manifest)}</code></div>
    <a class="install" href="{esc(install)}">Install in Stremio</a>
  </div>
  <footer>Anyone on your network can use this URL. The server URL must be reachable from their device.</footer>
</main></body></html>"""
    return Response(content=page, media_type="text/html")


@app.get("/")
async def root(request: Request):
    return JSONResponse({
        "addon": cfg_name(request),
        "install": f"{base_url(request)}/manifest.json",
        "configure": f"{base_url(request)}/configure",
        "library": ROOT,
    })


@app.on_event("startup")
async def watchdog_start():
    async def watchdog():
        tick = max(5, min(60, IDLE_SECONDS // 2))
        while True:
            await asyncio.sleep(tick)
            if streams == 0 and time.time() - last_seen > IDLE_SECONDS:
                print("shelf-stremio: idle, sleeping", flush=True)
                os._exit(0)

    asyncio.create_task(watchdog())

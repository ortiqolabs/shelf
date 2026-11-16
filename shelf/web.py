"""HTTP layer: one page, one snapshot endpoint, one event stream."""

from __future__ import annotations

import asyncio
import json
import math
import re
import secrets
import shutil
import time
from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, settings
from .aria2 import Aria2, summary
from .qbittorrent import QB_PREFIX, QbError, Qbittorrent

IDLE_SECONDS = config.IDLE_SECONDS

app = FastAPI(title="Shelf", docs_url=None, redoc_url=None)
aria2 = Aria2()
qb = Qbittorrent()

state = {"last_seen": time.time(), "streams": 0}


class AddBody(BaseModel):
    url: str
    engine: str = "aria2"


class ActionBody(BaseModel):
    gid: str
    action: str


class SettingsBody(BaseModel):
    downloadDir: str | None = None
    maxConcurrent: int | None = None
    maxDownloadLimit: str | None = None


@app.middleware("http")
async def track_and_cache(request, call_next):
    """Cache the shell, and note activity so the idle watchdog knows."""
    state["last_seen"] = time.time()
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-cache"
    else:
        response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@app.middleware("http")
async def require_token(request: Request, call_next):
    """Gate every request behind SHELF_TOKEN when one is configured."""
    token = config.TOKEN
    if not token:
        return await call_next(request)
    query = request.query_params.get("token")
    supplied = query or request.headers.get("x-shelf-token") or request.cookies.get("shelf_token")
    if supplied and secrets.compare_digest(supplied, token):
        if query:
            response = RedirectResponse(_strip_token(request), status_code=307)
            response.set_cookie(
                "shelf_token",
                token,
                max_age=31536000,
                httponly=True,
                samesite="lax",
                path="/",
            )
            return response
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)
    return PlainTextResponse("Unauthorized", status_code=401)


def _strip_token(request: Request) -> str:
    """The same URL with the token query parameter removed."""
    pairs = [(k, v) for k, v in parse_qsl(request.url.query, keep_blank_values=True) if k != "token"]
    query = urlencode(pairs)
    return request.url.path + (f"?{query}" if query else "")


ADD_LIMIT = 10
ADD_WINDOW = 60.0
_add_hits: dict[str, list[float]] = {}


def _add_wait(ip: str) -> float:
    """Seconds before this client may add again; 0 when allowed."""
    now = time.monotonic()
    hits = [t for t in _add_hits.get(ip, []) if now - t < ADD_WINDOW]
    if hits:
        _add_hits[ip] = hits
    else:
        _add_hits.pop(ip, None)
    if len(hits) < ADD_LIMIT:
        return 0.0
    return ADD_WINDOW - (now - hits[0])


def _record_add(ip: str) -> None:
    _add_hits.setdefault(ip, []).append(time.monotonic())


def disk() -> dict:
    """Free and total space on the configured download volume."""
    path = settings.current()["downloadDir"]
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {"free": 0, "total": 0, "path": path}
    return {"free": usage.free, "total": usage.total, "path": path}


def _torrent_link(link: str) -> bool:
    """True for a magnet or a URL whose path ends in .torrent."""
    return link.lower().startswith("magnet:") or urlparse(link).path.lower().endswith(".torrent")


async def watchdog() -> None:
    """Exit when nobody is looking. systemd wakes us on the next request."""
    while True:
        await asyncio.sleep(30)
        idle = time.time() - state["last_seen"]
        if state["streams"] == 0 and idle > IDLE_SECONDS:
            print("shelf: idle, going to sleep", flush=True)
            raise SystemExit(0)


@app.on_event("startup")
async def start_watchdog() -> None:
    asyncio.create_task(watchdog())


async def snapshot() -> dict:
    """One queue from both daemons; aria2 always renders, even when qB is down."""
    body = await aria2.collect()
    items = list(body.get("items") or [])
    if qb.enabled:
        bridge = await qb.collect()
        items.extend(bridge["items"])
        body["speed"] = int(body.get("speed") or 0) + bridge["speed"]
        body["qbt"] = {"ok": not bridge["error"], "error": bridge["error"]}
    body["items"] = items
    body["summary"] = summary(items)
    body["disk"] = disk()
    return body


@app.get("/api/state")
async def get_state() -> dict:
    return await snapshot()


@app.get("/api/events")
async def events() -> StreamingResponse:
    async def stream():
        state["streams"] += 1
        try:
            while True:
                state["last_seen"] = time.time()
                yield f"data: {json.dumps(await snapshot())}\n\n"
                await asyncio.sleep(1)
        finally:
            state["streams"] -= 1

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/add")
async def add(body: AddBody, request: Request) -> dict:
    raw = body.url.strip()
    if not raw:
        raise HTTPException(400, "Empty link")
    if body.engine not in ("aria2", "qbittorrent"):
        raise HTTPException(400, "Unknown engine")
    ip = request.client.host if request.client else ""
    wait = _add_wait(ip)
    if wait > 0:
        raise HTTPException(
            429,
            "Too many links, try again later",
            headers={"Retry-After": str(max(1, math.ceil(wait)))},
        )
    # a pasted list is fine: every link starts its own transfer
    links = [x for x in re.split(r"\s+", raw) if x]
    if body.engine == "qbittorrent":
        if not qb.enabled:
            raise HTTPException(400, "qBittorrent is not configured")
        if not all(_torrent_link(link) for link in links):
            raise HTTPException(400, "qBittorrent only accepts magnets and torrent files")
        try:
            await qb.add(links)
        except QbError as exc:
            raise HTTPException(400, str(exc)) from exc
        _record_add(ip)
        return {"added": links}

    added: list[str] = []
    for link in links:
        result = await aria2.call("aria2.addUri", [[link], {"pause": "false"}])
        if isinstance(result, str):
            added.append(result)
        else:
            raise HTTPException(400, (result or {}).get("message", "aria2 rejected the link"))
    _record_add(ip)
    return {"added": added}


async def qb_action(torrent_hash: str, action: str) -> dict:
    if not qb.enabled:
        raise HTTPException(400, "qBittorrent Is Not Configured")
    if action == "retry":
        try:
            await qb.recheck(torrent_hash)
            await qb.resume(torrent_hash)
        except QbError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}
    handler = {
        "pause": qb.pause,
        "resume": qb.resume,
        "cancel": qb.delete,  # stop the transfer and remove its files
        "delete": qb.delete,
        "remove": qb.remove,  # clear the entry, keep the files
    }.get(action)
    if not handler:
        raise HTTPException(400, "Unknown action")
    try:
        await handler(torrent_hash)
    except QbError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/action")
async def action(body: ActionBody) -> dict:
    if body.gid.startswith(QB_PREFIX):
        return await qb_action(body.gid[len(QB_PREFIX):], body.action)

    if body.action == "retry":
        status = await aria2.call("aria2.tellStatus", [body.gid, ["files", "status"]])
        uri = ""
        if isinstance(status, dict):
            for item in status.get("files") or []:
                uri = next((u.get("uri", "") for u in item.get("uris") or [] if u.get("uri")), "")
                if uri:
                    break
        if not uri:
            raise HTTPException(400, "No link to retry")
        result = await aria2.call("aria2.addUri", [[uri], {"pause": "false"}])
        if not isinstance(result, str):
            raise HTTPException(400, (result or {}).get("message", "aria2 rejected the link"))
        await aria2.call("aria2.removeDownloadResult", [body.gid])
        return {"ok": True, "gid": result}

    if body.action in ("cancel", "delete"):
        await aria2.purge(body.gid)
        return {"ok": True}

    if body.action == "remove":
        _, current = await aria2.files_for(body.gid)
        if current in ("error", "removed"):
            await aria2.purge(body.gid)
            return {"ok": True}
        result = await aria2.call("aria2.removeDownloadResult", [body.gid])
        if isinstance(result, dict) and result.get("message"):
            raise HTTPException(400, result["message"])
        return {"ok": True}

    method = {"pause": "aria2.pause", "resume": "aria2.unpause"}.get(body.action)
    if not method:
        raise HTTPException(400, "Unknown action")
    result = await aria2.call(method, [body.gid])
    if isinstance(result, dict) and result.get("message"):
        raise HTTPException(400, result["message"])
    return {"ok": True}


@app.get("/api/settings")
async def get_settings() -> dict:
    return settings.current()


@app.put("/api/settings")
async def put_settings(body: SettingsBody):
    payload = body.model_dump(exclude_none=True)
    try:
        return await settings.save(aria2, payload)
    except settings.SettingsError as exc:
        field = next(iter(payload), None)
        return JSONResponse(status_code=400, content={"detail": str(exc), "field": field})


@app.get("/settings", include_in_schema=False)
async def settings_page() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "settings.html")


@app.post("/api/all/{verb}")
async def pause_or_resume_everything(verb: str) -> dict:
    method = {"pause": "aria2.pauseAll", "resume": "aria2.unpauseAll"}.get(verb)
    if not method:
        raise HTTPException(400, "Unknown action")
    await aria2.call(method)
    if not qb.enabled:
        return {"ok": True}
    try:
        await (qb.pause_all() if verb == "pause" else qb.resume_all())
    except QbError as exc:
        return {"ok": True, "qbt": {"ok": False, "error": str(exc)}}
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")

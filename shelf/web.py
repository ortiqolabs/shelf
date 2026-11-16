"""HTTP layer: one page, one snapshot endpoint, one event stream."""

from __future__ import annotations

import asyncio
import json
import re
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .aria2 import Aria2, summary
from .qbittorrent import QB_PREFIX, QbError, Qbittorrent

IDLE_SECONDS = config.IDLE_SECONDS

app = FastAPI(title="Shelf", docs_url=None, redoc_url=None)
aria2 = Aria2()
qb = Qbittorrent()

state = {"last_seen": time.time(), "streams": 0}


class AddBody(BaseModel):
    url: str


class ActionBody(BaseModel):
    gid: str
    action: str


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
async def add(body: AddBody) -> dict:
    raw = body.url.strip()
    if not raw:
        raise HTTPException(400, "Empty link")
    # a pasted list is fine: every link starts its own transfer
    added: list[str] = []
    for link in [x for x in re.split(r"\s+", raw) if x]:
        result = await aria2.call("aria2.addUri", [[link], {"pause": "false"}])
        if isinstance(result, str):
            added.append(result)
        else:
            raise HTTPException(400, (result or {}).get("message", "aria2 rejected the link"))
    return {"added": added}


async def qb_action(torrent_hash: str, action: str) -> dict:
    if not qb.enabled:
        raise HTTPException(400, "qBittorrent Is Not Configured")
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

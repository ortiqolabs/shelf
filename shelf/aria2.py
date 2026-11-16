"""Thin async client for aria2's JSON-RPC, plus the shaping the page consumes."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from . import config

# aria2 status -> the bucket the page groups by
BUCKET = {
    "active": "active",
    "waiting": "queued",
    "paused": "paused",
    "complete": "done",
    "error": "failed",
    "removed": "failed",
}

# only these fields travel from aria2 to the browser
KEEP = (
    "gid status totalLength completedLength downloadSpeed uploadSpeed "
    "connections numSeeders seeder errorMessage files bittorrent dir"
).split()


class Aria2:
    """One instance per process; httpx keeps the connection to the daemon warm."""

    def __init__(self, url: str | None = None, secret: str | None = None) -> None:
        self.url = url or config.ARIA2_RPC
        self.secret = secret if secret is not None else config.RPC_SECRET
        self._client = httpx.AsyncClient(timeout=5.0)
        self._ids = 0

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        self._ids += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._ids,
            "method": method,
            "params": [f"token:{self.secret}"] + (params or []),
        }
        try:
            response = await self._client.post(self.url, json=payload)
            body = response.json()
        except Exception as exc:  # daemon down, restarting, or a timeout
            return {"error": {"message": str(exc)}}
        return body.get("error") or body.get("result")

    async def collect(self) -> dict:
        """One snapshot: every transfer plus the live throughput."""
        active, waiting, stopped, stat = await asyncio.gather(
            self.call("aria2.tellActive", [KEEP]),
            self.call("aria2.tellWaiting", [0, 200, KEEP]),
            self.call("aria2.tellStopped", [0, 200, KEEP]),
            self.call("aria2.getGlobalStat"),
        )
        rows: list[dict] = []
        for chunk in (active, waiting, stopped):
            if isinstance(chunk, list):
                rows.extend(shape(item) for item in chunk)
        speed = int(stat.get("downloadSpeed") or 0) if isinstance(stat, dict) else 0
        return {"items": rows, "speed": speed}

    async def files_for(self, gid: str) -> tuple[list[str], str]:
        status = await self.call("aria2.tellStatus", [gid, ["files", "status"]])
        if not isinstance(status, dict):
            return [], ""
        paths = [f.get("path", "") for f in status.get("files") or []]
        return [p for p in paths if p], status.get("status", "")

    async def purge(self, gid: str) -> None:
        """Stop the transfer, drop it from the list, delete what it wrote."""
        paths, _ = await self.files_for(gid)
        await self.call("aria2.forceRemove", [gid])
        await self.call("aria2.removeDownloadResult", [gid])
        for path in paths:
            safe_unlink(path)


def name_of(item: dict) -> str:
    """A human name for a transfer: torrent name, file name, or the added link."""
    torrent = item.get("bittorrent") or {}
    torrent_name = (torrent.get("info") or {}).get("name")
    if torrent_name:
        return torrent_name

    files = item.get("files") or []
    if len(files) == 1:
        path = files[0].get("path", "")
        if path:
            return os.path.basename(path)
    if files:
        parent = os.path.dirname(files[0].get("path", ""))
        if parent:
            return os.path.basename(parent)
        for uri in files[0].get("uris") or []:
            tail = uri.get("uri", "").rstrip("/").split("/")[-1]
            if tail:
                return tail[:80]
    return item.get("gid", "?")


def shape(item: dict) -> dict:
    """Normalise one aria2 transfer for the page."""
    total = int(item.get("totalLength") or 0)
    done = int(item.get("completedLength") or 0)
    status = item.get("status", "")
    files = item.get("files") or []
    return {
        "gid": item.get("gid"),
        "source": "aria2",
        "name": name_of(item),
        "status": status,
        "bucket": BUCKET.get(status, "queued"),
        "total": total,
        "done": done,
        "percent": round(done * 100 / total, 1) if total else 0.0,
        "down": int(item.get("downloadSpeed") or 0),
        "up": int(item.get("uploadSpeed") or 0),
        "peers": int(item.get("connections") or 0),
        "seeds": int(item.get("numSeeders") or 0),
        "isTorrent": bool(item.get("bittorrent")),
        "dir": item.get("dir", ""),
        "files": len(files),
        "error": item.get("errorMessage") or "",
    }


def summary(items: list[dict]) -> dict:
    counts = {"active": 0, "queued": 0, "done": 0, "failed": 0}
    for item in items:
        counts[item["bucket"]] = counts.get(item["bucket"], 0) + 1
    return counts


def safe_unlink(path: str) -> None:
    """Delete a download, but never outside the configured download folder."""
    root = os.path.realpath(config.DOWNLOAD_DIR)
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep):
        return
    for target in (real, real + ".aria2"):
        try:
            os.unlink(target)
        except OSError:
            pass
    parent = os.path.dirname(real)
    while parent.startswith(root + os.sep):
        try:
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)

"""Thin async client for qBittorrent's WebUI API, shaped like the aria2 client."""

from __future__ import annotations

from typing import Any

import httpx

from . import config

QB_PREFIX = "qb:"

# qBittorrent state -> the bucket the page groups by
BUCKET = {
    "downloading": "active",
    "forcedDL": "active",
    "metaDL": "active",
    "forcedMetaDL": "active",
    "allocating": "active",
    "checkingDL": "active",
    "checkingUP": "active",
    "checkingResumeData": "active",
    "moving": "active",
    "stalledDL": "active",
    "queuedDL": "queued",
    "queuedUP": "queued",
    "pausedDL": "paused",
    "pausedUP": "paused",
    "stoppedDL": "paused",
    "stoppedUP": "paused",
    "uploading": "done",
    "stalledUP": "done",
    "forcedUP": "done",
    "error": "failed",
    "missingFiles": "failed",
}


class QbError(Exception):
    """The daemon answered, but not with what we asked for."""


class Qbittorrent:
    """One instance per process; the SID cookie stays in httpx's jar."""

    def __init__(
        self,
        url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = (config.QB_URL if url is None else url).rstrip("/")
        self.user = config.QB_USER if user is None else user
        self.password = config.QB_PASS if password is None else password
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._auth = False

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.user and self.password)

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        # the WebUI rejects requests without a matching origin
        return {"Referer": self.url, "Origin": self.url}

    async def _login(self) -> None:
        self._auth = False
        try:
            response = await self._client.post(
                f"{self.url}/api/v2/auth/login",
                data={"username": self.user, "password": self.password},
                headers=self._headers(),
            )
        except Exception as exc:  # daemon down, restarting, or a timeout
            raise QbError(f"qBittorrent unreachable: {exc}") from exc
        if response.status_code != 200:
            raise QbError(f"qBittorrent login failed ({response.status_code})")
        if response.text.strip() != "Ok.":
            raise QbError("qBittorrent rejected the credentials")
        self._auth = True

    async def _call(self, method: str, path: str, data: dict | None = None) -> Any:
        """One authenticated request; a stale SID is renewed once."""
        if not self._auth:
            await self._login()
        url = f"{self.url}{path}"
        try:
            response = await self._client.request(method, url, data=data, headers=self._headers())
            if response.status_code == 403:
                await self._login()
                response = await self._client.request(method, url, data=data, headers=self._headers())
        except QbError:
            raise
        except Exception as exc:
            raise QbError(f"qBittorrent unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise QbError(f"qBittorrent answered {response.status_code} for {path}")
        return response

    async def collect(self) -> dict:
        """Every torrent plus its throughput; errors are returned, never raised."""
        try:
            response = await self._call("GET", "/api/v2/torrents/info")
            rows = [shape(item) for item in response.json() if isinstance(item, dict)]
        except Exception as exc:
            return {"items": [], "speed": 0, "error": str(exc)}
        return {"items": rows, "speed": sum(row["down"] for row in rows), "error": ""}

    async def pause(self, torrent_hash: str) -> None:
        await self._call("POST", "/api/v2/torrents/pause", {"hashes": torrent_hash})

    async def resume(self, torrent_hash: str) -> None:
        await self._call("POST", "/api/v2/torrents/resume", {"hashes": torrent_hash})

    async def add(self, urls: str | list[str]) -> None:
        """Queue magnets or torrent URLs by link."""
        if isinstance(urls, list):
            urls = "\n".join(urls)
        await self._call("POST", "/api/v2/torrents/add", {"urls": urls})

    async def recheck(self, torrent_hash: str) -> None:
        await self._call("POST", "/api/v2/torrents/recheck", {"hashes": torrent_hash})

    async def delete(self, torrent_hash: str) -> None:
        await self._call(
            "POST",
            "/api/v2/torrents/delete",
            {"hashes": torrent_hash, "deleteFiles": "true"},
        )

    async def remove(self, torrent_hash: str) -> None:
        await self._call(
            "POST",
            "/api/v2/torrents/delete",
            {"hashes": torrent_hash, "deleteFiles": "false"},
        )

    async def pause_all(self) -> None:
        await self._call("POST", "/api/v2/torrents/pause", {"hashes": "all"})

    async def resume_all(self) -> None:
        await self._call("POST", "/api/v2/torrents/resume", {"hashes": "all"})


def bucket_of(state: str) -> str:
    """Group a qBittorrent state, including future ones with the same prefix."""
    if state in BUCKET:
        return BUCKET[state]
    if state.startswith("queued"):
        return "queued"
    if state.startswith(("paused", "stopped")):
        return "paused"
    if state.startswith(("checking", "forced", "meta")):
        return "active"
    return "queued"


def shape(torrent: dict) -> dict:
    """Normalise one qBittorrent torrent for the page."""
    size = int(torrent.get("size") or 0)
    progress = float(torrent.get("progress") or 0.0)
    state = torrent.get("state", "")
    seeds = torrent.get("num_seeds")
    if seeds is None:
        seeds = torrent.get("num_complete")
    return {
        "gid": f"{QB_PREFIX}{torrent.get('hash', '')}",
        "source": "qbittorrent",
        "name": torrent.get("name") or torrent.get("hash", "?"),
        "status": state,
        "bucket": bucket_of(state),
        "total": size,
        "done": int(size * progress),
        "percent": round(progress * 100, 1),
        "down": int(torrent.get("dlspeed") or 0),
        "up": int(torrent.get("upspeed") or 0),
        "peers": int(torrent.get("num_leechs") or 0),
        "seeds": int(seeds or 0),
        "isTorrent": True,
        "dir": torrent.get("save_path") or "",
        # torrents/info carries no file count; a per-torrent call is not worth it
        "files": 0,
        "error": {"error": "Error", "missingFiles": "Missing Files"}.get(state, ""),
        "eta": int(torrent.get("eta") or 0),
    }

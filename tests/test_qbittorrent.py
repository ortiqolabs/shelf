import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from shelf import web  # noqa: E402
from shelf.qbittorrent import Qbittorrent, bucket_of, shape  # noqa: E402

TORRENT = {
    "hash": "abc123",
    "name": "Show Pack",
    "state": "downloading",
    "size": 2000,
    "progress": 0.25,
    "dlspeed": 300,
    "upspeed": 20,
    "num_seeds": 4,
    "num_complete": 9,
    "eta": 42,
    "save_path": "/media/Movies_and_Shows",
}


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class FakeClient:
    """Stands in for httpx.AsyncClient: records calls, replays responses."""

    def __init__(self, responses=None, login_text="Ok.", login_status=200, raises=None):
        self.requests = []
        self.responses = list(responses or [])
        self.login_text = login_text
        self.login_status = login_status
        self.raises = raises

    async def post(self, url, data=None, headers=None):
        self.requests.append(("POST", url, data))
        if self.raises:
            raise self.raises
        return FakeResponse(self.login_status, self.login_text)

    async def request(self, method, url, data=None, headers=None):
        self.requests.append((method, url, data))
        if self.raises:
            raise self.raises
        if not self.responses:
            return FakeResponse(200, "", [])
        return self.responses.pop(0)

    async def aclose(self):
        pass


def client_with(responses=None, **kwargs):
    fake = FakeClient(responses, **kwargs)
    qbt = Qbittorrent(
        url="http://qb.test:4000",
        user="admin",
        password="secret",
        client=fake,
    )
    return qbt, fake


def actions(fake):
    return [r for r in fake.requests if not r[1].endswith("/auth/login")]


def logins(fake):
    return [r for r in fake.requests if r[1].endswith("/auth/login")]


@pytest.mark.parametrize(
    "state,bucket",
    [
        ("downloading", "active"),
        ("forcedDL", "active"),
        ("metaDL", "active"),
        ("allocating", "active"),
        ("checkingDL", "active"),
        ("checkingUP", "active"),
        ("checkingResumeData", "active"),
        ("queuedDL", "queued"),
        ("queuedUP", "queued"),
        ("pausedDL", "paused"),
        ("pausedUP", "paused"),
        ("stoppedDL", "paused"),
        ("stoppedUP", "paused"),
        ("uploading", "done"),
        ("stalledUP", "done"),
        ("forcedUP", "done"),
        ("error", "failed"),
        ("missingFiles", "failed"),
    ],
)
def test_state_maps_to_bucket(state, bucket):
    assert bucket_of(state) == bucket


@pytest.mark.parametrize(
    "state,bucket",
    [("queuedForChecking", "queued"), ("pausedMetadata", "paused"), ("checkingWhatever", "active")],
)
def test_unlisted_states_fall_back_by_prefix(state, bucket):
    assert bucket_of(state) == bucket


def test_shape_maps_progress_size_speed_seeds_eta():
    item = shape(TORRENT)
    assert item["total"] == 2000
    assert item["done"] == 500
    assert item["percent"] == 25.0
    assert item["down"] == 300
    assert item["up"] == 20
    assert item["seeds"] == 4
    assert item["eta"] == 42
    assert item["peers"] == 0
    assert item["bucket"] == "active"
    assert item["isTorrent"] is True
    assert item["source"] == "qbittorrent"
    assert item["dir"] == "/media/Movies_and_Shows"


def test_shape_falls_back_to_total_seeds():
    item = shape({"hash": "x", "num_complete": 12})
    assert item["seeds"] == 12


def test_ids_are_namespaced():
    assert shape(TORRENT)["gid"] == "qb:abc123"


def test_failed_states_carry_a_reason():
    assert shape({**TORRENT, "state": "error"})["error"] == "Error"
    assert shape({**TORRENT, "state": "missingFiles"})["error"] == "Missing Files"
    assert shape(TORRENT)["error"] == ""


def test_collect_logs_in_once_and_lists_torrents():
    qbt, fake = client_with([FakeResponse(payload=[TORRENT])])
    body = asyncio.run(qbt.collect())
    assert body["error"] == ""
    assert body["speed"] == 300
    assert [i["gid"] for i in body["items"]] == ["qb:abc123"]
    assert len(logins(fake)) == 1
    assert actions(fake)[0][:2] == ("GET", "http://qb.test:4000/api/v2/torrents/info")


def test_session_is_reused_between_calls():
    qbt, fake = client_with([FakeResponse(payload=[TORRENT]), FakeResponse(payload=[])])
    asyncio.run(qbt.collect())
    asyncio.run(qbt.collect())
    assert len(logins(fake)) == 1


def test_403_triggers_one_renewal():
    qbt, fake = client_with(
        [FakeResponse(403, ""), FakeResponse(payload=[TORRENT])],
    )
    body = asyncio.run(qbt.collect())
    assert body["error"] == ""
    assert len(logins(fake)) == 2
    assert len(actions(fake)) == 2


def test_pause_and_resume_send_the_hash():
    qbt, fake = client_with()
    asyncio.run(qbt.pause("abc123"))
    asyncio.run(qbt.resume("abc123"))
    assert actions(fake) == [
        ("POST", "http://qb.test:4000/api/v2/torrents/pause", {"hashes": "abc123"}),
        ("POST", "http://qb.test:4000/api/v2/torrents/resume", {"hashes": "abc123"}),
    ]


def test_delete_removes_files_and_remove_keeps_them():
    qbt, fake = client_with()
    asyncio.run(qbt.delete("abc123"))
    asyncio.run(qbt.remove("abc123"))
    assert actions(fake) == [
        (
            "POST",
            "http://qb.test:4000/api/v2/torrents/delete",
            {"hashes": "abc123", "deleteFiles": "true"},
        ),
        (
            "POST",
            "http://qb.test:4000/api/v2/torrents/delete",
            {"hashes": "abc123", "deleteFiles": "false"},
        ),
    ]


def test_pause_all_sends_all():
    qbt, fake = client_with()
    asyncio.run(qbt.pause_all())
    assert actions(fake) == [("POST", "http://qb.test:4000/api/v2/torrents/pause", {"hashes": "all"})]


def test_rejected_login_is_reported_not_raised():
    qbt, _ = client_with(login_text="Fails.")
    body = asyncio.run(qbt.collect())
    assert body["items"] == []
    assert "credentials" in body["error"]
    assert body["speed"] == 0


def test_unreachable_daemon_is_reported_not_raised():
    qbt, _ = client_with(raises=httpx.ConnectError("refused"))
    body = asyncio.run(qbt.collect())
    assert body["items"] == []
    assert "unreachable" in body["error"]


def test_bridge_is_disabled_without_the_three_variables():
    assert Qbittorrent(url="", user="", password="").enabled is False
    assert Qbittorrent(url="http://qb.test:4000", user="admin", password="secret").enabled is True


class FakeAria2:
    async def collect(self):
        return {
            "items": [{"gid": "abc", "source": "aria2", "name": "Film.mkv", "bucket": "active"}],
            "speed": 2048,
        }

    async def call(self, method, params=None):
        return "ok"

    async def files_for(self, gid):
        return [], "complete"

    async def purge(self, gid):
        pass


class FakeQb:
    def __init__(self, error=""):
        self.enabled = True
        self.error = error
        self.calls = []

    async def collect(self):
        items = [] if self.error else [{"gid": "qb:h1", "source": "qbittorrent", "name": "Show", "bucket": "active"}]
        return {"items": items, "speed": 0 if self.error else 100, "error": self.error}

    async def pause(self, torrent_hash):
        self.calls.append(("pause", torrent_hash))

    async def resume(self, torrent_hash):
        self.calls.append(("resume", torrent_hash))

    async def delete(self, torrent_hash):
        self.calls.append(("delete", torrent_hash))

    async def remove(self, torrent_hash):
        self.calls.append(("remove", torrent_hash))

    async def pause_all(self):
        self.calls.append(("pause_all", None))

    async def resume_all(self):
        self.calls.append(("resume_all", None))


def page_for(monkeypatch, qbt):
    monkeypatch.setattr(web, "aria2", FakeAria2())
    monkeypatch.setattr(web, "qb", qbt)
    return TestClient(web.app)


def test_state_merges_both_sources(monkeypatch):
    body = page_for(monkeypatch, FakeQb()).get("/api/state").json()
    assert [item["source"] for item in body["items"]] == ["aria2", "qbittorrent"]
    assert body["summary"]["active"] == 2
    assert body["speed"] == 2148
    assert body["qbt"] == {"ok": True, "error": ""}


def test_state_keeps_aria2_when_qb_login_fails(monkeypatch):
    body = page_for(monkeypatch, FakeQb(error="qBittorrent rejected the credentials")).get("/api/state").json()
    assert [item["gid"] for item in body["items"]] == ["abc"]
    assert body["qbt"]["ok"] is False
    assert "credentials" in body["qbt"]["error"]


def test_state_omits_qbt_when_bridge_disabled(monkeypatch):
    body = page_for(monkeypatch, Qbittorrent(url="", user="", password="")).get("/api/state").json()
    assert "qbt" not in body


@pytest.mark.parametrize(
    "action,expected",
    [("pause", "pause"), ("resume", "resume"), ("cancel", "delete"), ("delete", "delete"), ("remove", "remove")],
)
def test_actions_dispatch_on_the_prefix(monkeypatch, action, expected):
    qbt = FakeQb()
    response = page_for(monkeypatch, qbt).post("/api/action", json={"gid": "qb:h1", "action": action})
    assert response.status_code == 200
    assert qbt.calls == [(expected, "h1")]


def test_qb_action_is_rejected_when_disabled(monkeypatch):
    response = page_for(monkeypatch, Qbittorrent(url="", user="", password="")).post(
        "/api/action", json={"gid": "qb:h1", "action": "pause"}
    )
    assert response.status_code == 400


def test_unknown_qb_action_is_rejected(monkeypatch):
    response = page_for(monkeypatch, FakeQb()).post(
        "/api/action", json={"gid": "qb:h1", "action": "explode"}
    )
    assert response.status_code == 400


def test_pause_all_covers_both_sources(monkeypatch):
    qbt = FakeQb()
    response = page_for(monkeypatch, qbt).post("/api/all/pause")
    assert response.status_code == 200
    assert qbt.calls == [("pause_all", None)]

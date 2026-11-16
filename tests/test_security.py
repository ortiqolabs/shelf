import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from shelf import web  # noqa: E402


class FakeAria2:
    async def collect(self):
        return {"items": [], "speed": 0}

    async def call(self, method, params=None):
        return "gid-new"

    async def files_for(self, gid):
        return [], ""

    async def purge(self, gid):
        pass


@pytest.fixture(autouse=True)
def reset_add_limit():
    web._add_hits.clear()
    yield
    web._add_hits.clear()


def make_client(monkeypatch, token="", follow_redirects=True):
    monkeypatch.setattr(web, "aria2", FakeAria2())
    monkeypatch.setattr(web.config, "TOKEN", token)
    return TestClient(web.app, follow_redirects=follow_redirects)


def test_token_is_disabled_by_default(monkeypatch):
    client = make_client(monkeypatch, token="")
    assert client.get("/api/state").status_code == 200


def test_missing_token_is_rejected(monkeypatch):
    client = make_client(monkeypatch, token="s3cret")
    api = client.get("/api/state")
    assert api.status_code == 401
    assert api.json()["detail"]
    assert client.get("/api/events").status_code == 401
    page = client.get("/")
    assert page.status_code == 401
    assert page.headers["content-type"].startswith("text/plain")
    assert "Unauthorized" in page.text


def test_wrong_token_is_rejected(monkeypatch):
    client = make_client(monkeypatch, token="s3cret")
    assert client.get("/api/state", headers={"X-Shelf-Token": "nope"}).status_code == 401


def test_header_token_is_accepted(monkeypatch):
    client = make_client(monkeypatch, token="s3cret")
    response = client.get("/api/state", headers={"X-Shelf-Token": "s3cret"})
    assert response.status_code == 200


def test_cookie_token_is_accepted(monkeypatch):
    client = make_client(monkeypatch, token="s3cret")
    client.cookies.set("shelf_token", "s3cret")
    assert client.get("/api/state").status_code == 200


def test_query_token_sets_a_cookie_and_redirects(monkeypatch):
    client = make_client(monkeypatch, token="s3cret", follow_redirects=False)
    response = client.get("/api/state?token=s3cret")
    assert response.status_code == 307
    assert response.headers["location"] == "/api/state"
    cookie = response.headers["set-cookie"].lower()
    assert "shelf_token=s3cret" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=31536000" in cookie
    # the cookie now travels on its own
    assert client.get("/api/state").status_code == 200


def test_query_token_keeps_other_parameters(monkeypatch):
    client = make_client(monkeypatch, token="s3cret", follow_redirects=False)
    response = client.get("/api/state?token=s3cret&x=1")
    assert response.headers["location"] == "/api/state?x=1"


def test_add_allows_ten_requests_per_minute(monkeypatch):
    client = make_client(monkeypatch)
    for _ in range(10):
        response = client.post("/api/add", json={"url": "https://host/file.bin"})
        assert response.status_code == 200


def test_eleventh_add_is_rate_limited(monkeypatch):
    client = make_client(monkeypatch)
    for _ in range(10):
        assert client.post("/api/add", json={"url": "https://host/file.bin"}).status_code == 200
    response = client.post("/api/add", json={"url": "https://host/file.bin"})
    assert response.status_code == 429
    assert "try again" in response.json()["detail"]
    assert int(response.headers["Retry-After"]) >= 1


def test_state_reports_disk_space(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setattr(web.settings, "current", lambda: {"downloadDir": "/media/Movies_and_Shows"})
    monkeypatch.setattr(web.shutil, "disk_usage", lambda path: SimpleNamespace(free=123, total=456))
    body = client.get("/api/state").json()
    assert body["disk"] == {"free": 123, "total": 456, "path": "/media/Movies_and_Shows"}


def test_events_payload_carries_disk_space(monkeypatch):
    monkeypatch.setattr(web, "aria2", FakeAria2())
    monkeypatch.setattr(web.config, "TOKEN", "")
    monkeypatch.setattr(web.settings, "current", lambda: {"downloadDir": "/media/TV"})
    monkeypatch.setattr(web.shutil, "disk_usage", lambda path: SimpleNamespace(free=7, total=9))
    body = asyncio.run(web.snapshot())
    assert body["disk"] == {"free": 7, "total": 9, "path": "/media/TV"}

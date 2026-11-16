import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from shelf import web  # noqa: E402


class FakeAria2:
    def __init__(self):
        self.calls = []
        self.transfers = [
            {
                "gid": "abc",
                "name": "Film.mkv",
                "status": "active",
                "bucket": "active",
                "total": 100,
                "done": 40,
                "percent": 40.0,
                "down": 2048,
                "up": 0,
                "peers": 2,
                "seeds": 1,
                "isTorrent": False,
                "dir": "/media/Movies_and_Shows",
                "files": 1,
                "error": "",
            }
        ]

    async def collect(self):
        return {"items": list(self.transfers), "speed": 2048}

    async def call(self, method, params=None):
        self.calls.append((method, params))
        return "gid-new"

    async def files_for(self, gid):
        return ["/media/Movies_and_Shows/Film.mkv"], "complete"

    async def purge(self, gid):
        self.calls.append(("purge", gid))


def client_with(fake):
    web.aria2 = fake
    return TestClient(web.app)


def test_state_returns_summary():
    fake = FakeAria2()
    response = client_with(fake).get("/api/state")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["active"] == 1
    assert body["items"][0]["name"] == "Film.mkv"


def test_add_splits_a_pasted_list():
    fake = FakeAria2()
    response = client_with(fake).post("/api/add", json={"url": "https://a/1 https://a/2"})
    assert response.status_code == 200
    assert len(response.json()["added"]) == 2
    assert [c[0] for c in fake.calls] == ["aria2.addUri", "aria2.addUri"]


def test_add_rejects_empty_input():
    assert client_with(FakeAria2()).post("/api/add", json={"url": "  "}).status_code == 400


def test_pause_calls_aria2():
    fake = FakeAria2()
    assert client_with(fake).post("/api/action", json={"gid": "abc", "action": "pause"}).status_code == 200
    assert fake.calls[-1][0] == "aria2.pause"


def test_delete_purges_files():
    fake = FakeAria2()
    assert client_with(fake).post("/api/action", json={"gid": "abc", "action": "delete"}).status_code == 200
    assert fake.calls[-1] == ("purge", "abc")


def test_unknown_action_is_rejected():
    response = client_with(FakeAria2()).post("/api/action", json={"gid": "abc", "action": "explode"})
    assert response.status_code == 400

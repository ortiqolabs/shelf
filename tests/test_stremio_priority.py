"""Checks for the addon-order tool, against a local fake datastore."""

import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "stremio_priority", os.path.join(ROOT, "tools", "stremio-priority.py")
)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)

SHELF = {"manifest": {"id": "org.shelf.media", "name": "Shelf"}, "transportUrl": "http://pi:7020/manifest.json"}
CINEMETA = {"manifest": {"id": "com.linvo.cinemeta", "name": "Cinemeta"}, "transportUrl": "https://v3-cinemeta.strem.io/manifest.json"}
TRAKT = {"manifest": {"id": "community.trakt", "name": "Trakt"}, "transportUrl": "https://trakt/manifest.json"}

state = {"addons": [CINEMETA, SHELF, TRAKT]}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        path = self.path.rsplit("/", 1)[-1]
        if path == "addonCollectionGet":
            body = {"result": {"addons": state["addons"]}}
        elif path == "addonCollectionSet":
            state["addons"] = payload["addons"]
            body = {"result": {"success": True}}
        else:
            body = {"error": {"message": "unknown"}}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def test_prioritize_puts_shelf_first_and_keeps_the_rest():
    moved = tool.prioritize([CINEMETA, SHELF, TRAKT], "org.shelf.media")
    assert [a["manifest"]["id"] for a in moved] == [
        "org.shelf.media", "com.linvo.cinemeta", "community.trakt",
    ]


def test_prioritize_is_idempotent():
    once = tool.prioritize([CINEMETA, SHELF, TRAKT], "org.shelf.media")
    twice = tool.prioritize(once, "org.shelf.media")
    assert once == twice


def test_prioritize_rejects_a_missing_addon():
    try:
        tool.prioritize([CINEMETA], "org.shelf.media")
    except SystemExit:
        return
    raise AssertionError("missing addon should abort")


def test_round_trip_against_a_fake_api():
    state["addons"] = [CINEMETA, SHELF, TRAKT]
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    api = f"http://127.0.0.1:{server.server_port}/api"
    try:
        addons = tool.fetch(api, "key")
        tool.post(api, "addonCollectionSet", {
            "type": "AddonCollectionSet", "authKey": "key",
            "addons": tool.prioritize(addons, "org.shelf.media"),
        })
        assert tool.fetch(api, "key")[0]["manifest"]["id"] == "org.shelf.media"
    finally:
        server.shutdown()

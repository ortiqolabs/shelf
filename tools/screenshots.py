"""Capture the README screenshots against invented demo data.

The real page shows the owner's library, so every request to /api/* is answered
with a fixture here. Nothing from the actual queue is ever rendered.

    python tools/screenshots.py [--url http://192.168.1.11:5000]

Writes docs/screenshots/desktop.png and docs/screenshots/phone.png.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"

ITEMS = [
    {
        "gid": "demo1", "name": "Aurora Drift (2025) 1080p WEB-DL.mkv",
        "status": "active", "bucket": "active",
        "total": 9_020_000_000, "done": 5_310_000_000, "percent": 58.9,
        "down": 6_942_000, "up": 0, "peers": 24, "seeds": 11,
        "isTorrent": True, "dir": "/media/library", "files": 1, "error": "",
        "source": "aria2",
    },
    {
        "gid": "qb:demo5", "name": "Harbour Lights S01E07 1080p.mkv",
        "status": "downloading", "bucket": "active",
        "total": 2_760_000_000, "done": 640_000_000, "percent": 23.2,
        "down": 1_310_000, "up": 0, "peers": 6, "seeds": 3,
        "isTorrent": True, "dir": "/media/library", "files": 1, "error": "",
        "source": "qbittorrent",
    },
    {
        "gid": "demo3", "name": "Monsoon Tapes 2024 FLAC.zip",
        "status": "paused", "bucket": "paused",
        "total": 1_340_000_000, "done": 402_000_000, "percent": 30.0,
        "down": 0, "up": 0, "peers": 0, "seeds": 0,
        "isTorrent": False, "dir": "/media/library", "files": 1, "error": "",
        "source": "aria2",
    },
    {
        "gid": "demo4", "name": "Lantern Bay (2024) 2160p HDR.mkv",
        "status": "complete", "bucket": "done",
        "total": 14_260_000_000, "done": 14_260_000_000, "percent": 100.0,
        "down": 0, "up": 0, "peers": 0, "seeds": 0,
        "isTorrent": True, "dir": "/media/library", "files": 1, "error": "",
        "source": "aria2",
    },
]

STATE = {
    "items": ITEMS,
    "speed": 9_226_000,
    "summary": {"active": 2, "queued": 0, "done": 1, "failed": 0},
    "qbt": {"ok": True, "error": ""},
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://192.168.1.11:5000/")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    body = json.dumps(STATE)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, width, height, mobile in (
            ("desktop", 1440, 1040, False),
            ("phone", 390, 844, True),
        ):
            context = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=2,
                is_mobile=mobile,
                has_touch=mobile,
            )
            page = context.new_page()
            page.route("**/api/state", lambda route: route.fulfill(
                status=200, content_type="application/json", body=body))
            page.route("**/api/events", lambda route: route.fulfill(
                status=200,
                headers={"content-type": "text/event-stream", "cache-control": "no-cache"},
                body=f"data: {body}\n\n",
            ))
            page.goto(args.url, wait_until="networkidle")
            page.wait_for_timeout(1800)
            # the fixture closes the stream after one frame, which trips the
            # reconnect notice; restore the normal hint before capturing
            page.evaluate("""() => {
              const hint = document.getElementById('hint');
              if (hint) {
                hint.innerHTML = 'Magnets, Torrents and Direct Links Supported';
                hint.dataset.tone = '';
              }
            }""")
            page.screenshot(path=str(OUT / f"{name}.png"))
            print(f"{name}: {width}x{height}")
            context.close()
        browser.close()


if __name__ == "__main__":
    main()

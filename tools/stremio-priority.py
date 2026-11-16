#!/usr/bin/env python3
"""Move the Shelf addon to the top of a Stremio account's addon list.

Stremio builds the board from the addon array in your account, in order, and
installs append to the end. This script rewrites that array with Shelf first.

    STREMIO_AUTH_KEY=... python3 tools/stremio-priority.py
    python3 tools/stremio-priority.py --dry-run --key ...

The key is your Stremio account key, not a password. It is sent only to
api.strem.io, the same service the app uses.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

API = "https://api.strem.io/api"
ADDON_ID = "org.shelf.media"


def post(api: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{api}/{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read())
    if "error" in body:
        raise SystemExit(f"stremio: {body['error'].get('message', body['error'])}")
    return body.get("result", body)


def fetch(api: str, key: str) -> list[dict]:
    result = post(api, "addonCollectionGet", {"type": "AddonCollectionGet", "authKey": key, "update": True})
    addons = result.get("addons")
    if not isinstance(addons, list):
        raise SystemExit("stremio: unexpected collection response")
    return addons


def prioritize(addons: list[dict], addon_id: str) -> list[dict]:
    target = [a for a in addons if (a.get("manifest") or {}).get("id") == addon_id]
    if not target:
        raise SystemExit(f"stremio: {addon_id} is not installed in this account")
    return target + [a for a in addons if a is not target[0]]


def names(addons: list[dict]) -> list[str]:
    return [(a.get("manifest") or {}).get("name") or "?" for a in addons]


def main() -> None:
    parser = argparse.ArgumentParser(description="Put Shelf first in Stremio's addon list")
    parser.add_argument("--key", default=os.environ.get("STREMIO_AUTH_KEY"), help="Stremio account key")
    parser.add_argument("--api", default=API, help="datastore API base")
    parser.add_argument("--id", default=ADDON_ID, help="manifest id to move to the front")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.key:
        raise SystemExit("missing --key or STREMIO_AUTH_KEY")

    addons = fetch(args.api, args.key)
    print("before:", " -> ".join(names(addons)))
    if (addons[0].get("manifest") or {}).get("id") == args.id:
        print("shelf is already first")
        return
    reordered = prioritize(addons, args.id)
    print("after: ", " -> ".join(names(reordered)))
    if args.dry_run:
        return
    post(args.api, "addonCollectionSet",
         {"type": "AddonCollectionSet", "authKey": args.key, "addons": reordered})
    verified = fetch(args.api, args.key)
    if (verified[0].get("manifest") or {}).get("id") != args.id:
        raise SystemExit("stremio: reorder did not stick")
    print("verified: shelf is first")


if __name__ == "__main__":
    main()

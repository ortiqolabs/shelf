# Shelf

A one page download station for a Raspberry Pi. Paste a magnet, a torrent URL or
a direct HTTP link; aria2 downloads it, the page shows every transfer live.

The UI is asleep until someone opens it. systemd holds the port, the first request
wakes the process, and 15 idle minutes send it back to sleep.

## Layout

```
shelf/
  config.py      paths, aria2 secret, qBittorrent credentials, idle timer
  aria2.py       JSON-RPC client, transfer shaping, safe deletes
  qbittorrent.py WebUI client, torrent shaping, actions
  web.py         FastAPI routes, merged queue, SSE stream, idle watchdog
static/          the page: html, css, js, fonts, background
deploy/          systemd units, aria2 configuration, install script
tests/           shaping, bridge and API tests
```

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m shelf          # http://127.0.0.1:5000
.venv/bin/pytest
```

## Deploy

```bash
./deploy/deploy.sh                 # HOST=raspi DEST=/home/arnav/shelf
```

Idempotent. Copies the repo, rebuilds the venv, writes `aria2.conf` using the
secret in `$DEST/.aria2-secret`, installs `aria2.service`, `shelf.service` and
`shelf.socket`, then smoke-tests the page.

`shelf.service` reads `$DEST/.env` when it exists. `deploy.sh` creates it with
mode 600 on the first run only, so a later deploy never overwrites it. The
password has no default in the script:

```bash
SHELF_QB_PASS=... ./deploy/deploy.sh    # first run only, fills the new .env
```

Without that variable the file gets an empty password and the bridge stays off.
Edit `$DEST/.env` to change it later, then `sudo systemctl restart shelf.service`.

## qBittorrent bridge

Three environment variables switch it on; unset or empty disables it and the
page behaves exactly as an aria2-only install:

- `SHELF_QB_URL` — `http://127.0.0.1:4000`
- `SHELF_QB_USER` — WebUI user
- `SHELF_QB_PASS` — WebUI password

On the Pi they live in `/home/arnav/shelf/.env` (never committed). Torrents from
both daemons share one queue. aria2 keeps its raw gid; qBittorrent items are
`qb:<hash>`. Every item carries `"source": "aria2" | "qbittorrent"`. When the
bridge is on, `/api/state` and `/api/events` add
`"qbt": {"ok": true|false, "error": "..."}`; a qBittorrent failure leaves the
aria2 rows intact. `cancel` and `delete` remove a torrent's files, `remove`
clears only the entry.

## API

- `GET /api/state` snapshot with a summary
- `GET /api/events` server-sent events, one update per second
- `POST /api/add` `{"url": "..."}`, spaces split into several links
- `POST /api/action` `{"gid": "...", "action": "pause|resume|cancel|delete|remove"}`
  - accepts an aria2 gid or a `qb:<hash>` id
  - `cancel` and `delete` stop the transfer and remove its files
  - `remove` clears a finished entry, cleaning leftovers only when it failed
- `POST /api/all/pause` and `/api/all/resume`

## Behaviour worth knowing

- The aria2 RPC binds to loopback only; the page is LAN-only with no login.
- qBittorrent is reached over loopback with its own login; the SID cookie is
  reused and renewed once on a 403.
- Files land in `dir=` from `aria2.conf`, currently the shared media folder.
- `SHELF_IDLE_SECONDS` changes the sleep timer.
- Session state survives restarts through `aria2.session`.
- Status is shown by icon, never by a word: arrow, pause ring, clock, check, alert.

## Design

`DESIGN.md` holds the visual direction: control-room graphite, amber signal,
frosted glass over the owner's starfield photograph, IBM-free Google Sans type.

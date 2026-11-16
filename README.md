# Shelf

A one page download station for a Raspberry Pi. Paste a magnet, a torrent URL or
a direct HTTP link; aria2 downloads it, the page shows every transfer live.

The UI is asleep until someone opens it. systemd holds the port, the first request
wakes the process, and 15 idle minutes send it back to sleep.

## Layout

```
shelf/
  config.py      paths, aria2 secret, idle timer
  aria2.py       JSON-RPC client, transfer shaping, safe deletes
  web.py         FastAPI routes, SSE stream, idle watchdog
static/          the page: html, css, js, fonts, background
deploy/          systemd units, aria2 configuration, install script
tests/           shaping and API tests
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

## API

- `GET /api/state` snapshot with a summary
- `GET /api/events` server-sent events, one update per second
- `POST /api/add` `{"url": "..."}`, spaces split into several links
- `POST /api/action` `{"gid": "...", "action": "pause|resume|cancel|delete|remove"}`
  - `cancel` and `delete` stop the transfer and remove its files
  - `remove` clears a finished entry, cleaning leftovers only when it failed
- `POST /api/all/pause` and `/api/all/resume`

## Behaviour worth knowing

- The aria2 RPC binds to loopback only; the page is LAN-only with no login.
- Files land in `dir=` from `aria2.conf`, currently the shared media folder.
- `SHELF_IDLE_SECONDS` changes the sleep timer.
- Session state survives restarts through `aria2.session`.
- Status is shown by icon, never by a word: arrow, pause ring, clock, check, alert.

## Design

`DESIGN.md` holds the visual direction: control-room graphite, amber signal,
frosted glass over the owner's starfield photograph, IBM-free Google Sans type.

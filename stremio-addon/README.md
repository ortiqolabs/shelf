# Shelf for Stremio

A Stremio addon that serves `/media/Movies_and_Shows` (the Samba share) as a
catalog. Pick a film or episode in Stremio and it plays straight off the Pi.

No transcoding, no library database, no background scan: the folder is read when
Stremio asks, and the file is streamed with byte ranges so seeking works.

## Configure

Open `/configure` on the host. Set the server URL that clients should reach and a
library name, and it prints the manifest URL plus a `stremio://` install link.

```
http://192.168.1.11:7020/configure
```

The settings live in the URL itself (`/c/<token>/...`), so each install can point
at a different host or carry a different library name without touching the server.
Legacy `/manifest.json` URLs keep working.

## Install

In Stremio: **Addons → Install from URL**

```
http://192.168.1.11:7020/manifest.json
```

Works in the desktop, Android and TV apps. `web.stremio.com` refuses to install
a plain `http://` addon because the page itself is https, so use one of the apps
on the LAN.

## What it exposes

| Route | Purpose |
|---|---|
| `/manifest.json` | addon manifest |
| `/catalog/movie/shelf.json` | movies found in the share |
| `/catalog/series/shelf.json` | folders containing `S08E03` style episodes |
| `/catalog/.../search=...json` | catalog search |
| `/meta/{type}/{id}.json` | title, year, poster, episode list |
| `/stream/{type}/{id}.json` | one stream, direct URL with `notWebReady` for mkv |
| `/subtitles/{type}/{id}.json` | sidecar `.srt`, `.vtt`, `.ass` next to the video |
| `/poster/{key}.png` | generated poster, cached in `~/.cache/shelf-posters` |
| `/play/{token}/{name}` | the bytes, `Range` and `HEAD` supported |

Files still downloading are skipped: a `.aria2`, `.part` or `.!qB` sibling keeps
them out of the catalog until the download finishes.

## Files

- `app.py` single file, FastAPI + Pillow
- `deploy/stremio-shelf.socket` port 7020, wakes the service on first hit
- `deploy/stremio-shelf.service` uvicorn on the inherited socket, sleeps after
  30 idle minutes
- `deploy.sh` copies, builds the venv, installs and restarts

## Playback quality

Streams are served as raw bytes with range support and are marked
`notWebReady`, so Stremio's native player plays the file directly. The addon
never transcodes and never downscales: what you see is the file's own bitrate.
A 544p HDRip at 650 kbps looks like a 544p HDRip at 650 kbps.

## Notes

- Titles come from filenames: the year is kept, release tags are dropped.
- Posters are generated at 400x600 with Pillow, one shared look, amber rule.
- No auth. Anything on the LAN that reaches `:7020` can read the share.
- `SHELF_ROOT` and `SHELF_IDLE_SECONDS` override the defaults.

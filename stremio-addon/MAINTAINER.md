# Maintainer note

Read this before changing the scanner or the routing. It records the traps that
already caused real bugs here.

## The incident this file exists for

A finished download stayed invisible for hours. aria2 had completed the file but
left a stale `<name>.aria2` control file behind. The scanner treated any
`.aria2` sibling as "still downloading" and hid the movie. The user saw a library
missing a title that was sitting complete on disk.

Rule now: a control file is only proof of work in progress when aria2's RPC says
so. `aria2_open_paths()` asks `tellActive`, `tellWaiting` and `tellStopped`
(status `paused` included), caches the path set for `PARTIAL_TTL`, and the scan
uses that. If the RPC is unreachable, the conservative fallback returns "partial"
for any control file, so a genuine download is never exposed half-written.

Do not "simplify" this back to `os.path.exists(path + ".aria2")`.
`tests/test_partial.py` pins the rule; run it on the Pi with the service venv:

```bash
cd /home/arnav/stremio-shelf && .venv/bin/python tests/test_partial.py
```

## Other invariants

- Catalog is sorted by file mtime descending, newest first. A new download must
  appear at the front without touching config.
- Only files matching the video extension set and not partial are listed. Series
  folders need `SxxEyy` in the filename to be treated as a show.
- Stream URLs are absolute and carry the config prefix when present. They are
  built through `base_url(request)`; never hardcode host, port or `/c/`.
- Every stream sets `notWebReady: true`. The addon never transcodes; Stremio's
  native player plays raw bytes with range support.
- `/manifest.json`, `/catalog/*` and `/meta/*` send `Cache-Control: no-store`.
  Stremio caches the manifest per install, so a flag change (e.g.
  `configurable`) needs a remove/re-add on the client, but catalogs re-read the
  directory every board refresh.
- Config rides in the URL: `/c/<base64url of {"b": base, "n": name}>/...`. The
  middleware strips the prefix before routing, and `base_url` re-adds it to every
  generated URL. Legacy root routes must keep working.
- `SHELF_IDLE_SECONDS` sends the service to sleep; the systemd socket wakes it.
  Never assume the process is resident.

## Diagnose in one call

```bash
curl -s http://127.0.0.1:7020/health | python3 -m json.tool
```

`hidden` lists files currently withheld, `stale_sidecars` lists files aria2 no
longer tracks (hidden only if the RPC is down), `aria2.open` is the live count.
A healthy library has empty `hidden` and empty `stale_sidecars`.

## Before you ship

```bash
./deploy.sh                      # copy, venv, units, restart
ssh raspi 'sudo -n systemctl restart stremio-shelf'
curl -s http://192.168.1.11:7020/catalog/movie/shelf.json | python3 -m json.tool | head
```

Check the rendered board in Stremio after any change to the manifest or the
poster paths. Catalogs are fetched by the client, not by curl, and the two can
disagree when a header or URL prefix is wrong.

## Known limits

- Partial detection depends on aria2. qBittorrent downloads are recognised only
  by `.part` / `.!qB` suffixes.
- Posters come from Cinemeta with an exact title+year match; `ALIASES` exists for
  releases whose file name differs from their IMDb name (`satluj` -> `Punjab '95`).
  Never fuzzy-match, that is how a wrong cover shipped once.
- Stremio's own transcode settings live outside this repo. `transcodeMaxWidth` is
  set to 3840 so a 4K file can never be downscaled if a transcode ever occurs.

#!/usr/bin/env bash
# Deploy the Stremio addon to the Pi.
set -euo pipefail

HOST=${HOST:-raspi}
DEST=${DEST:-/home/arnav/stremio-shelf}

echo "== copy"
ssh "$HOST" "mkdir -p $DEST/deploy"
rsync -az --delete --exclude .venv --exclude __pycache__ ./ "$HOST:$DEST/"

echo "== venv"
ssh "$HOST" "test -d $DEST/.venv || python3 -m venv $DEST/.venv"
ssh "$HOST" "$DEST/.venv/bin/pip -q install -r $DEST/requirements.txt"

echo "== units"
ssh "$HOST" "sudo -n cp $DEST/deploy/stremio-shelf.service $DEST/deploy/stremio-shelf.socket /etc/systemd/system/
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now stremio-shelf.socket
sudo -n systemctl restart stremio-shelf.service"

IP=$(ssh "$HOST" "hostname -I" | awk '{print $1}')
echo "== smoke"
ssh "$HOST" "curl -s -o /dev/null -w 'manifest %{http_code}\n' http://127.0.0.1:7020/manifest.json"
echo "install url: http://$IP:7020/manifest.json"

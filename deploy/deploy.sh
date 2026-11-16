#!/usr/bin/env bash
# Install or upgrade Shelf on the Pi. Run from the repository root.
set -euo pipefail

HOST=${HOST:-raspi}
DEST=${DEST:-/home/arnav/shelf}
SECRET_FILE=$DEST/.aria2-secret
ENV_FILE=$DEST/.env

# the qBittorrent WebUI it bridges to; the password has no default on purpose
QB_URL=${SHELF_QB_URL:-http://127.0.0.1:4000}
QB_USER=${SHELF_QB_USER:-admin}
QB_PASS=${SHELF_QB_PASS:-}

say() { printf '\n== %s\n' "$*"; }

say "stop the previous install, if any"
ssh "$HOST" '
  for unit in dl-panel.socket dl-panel.service; do
    sudo -n systemctl disable --now "$unit" 2>/dev/null || true
    sudo -n rm -f "/etc/systemd/system/$unit"
  done
  sudo -n systemctl daemon-reload
  rm -rf /home/arnav/dl-panel
'

say "copy the project to $HOST:$DEST"
ssh "$HOST" "mkdir -p $DEST /home/arnav/.config/aria2"
rsync -az --delete \
  --exclude .venv --exclude __pycache__ --exclude .git --exclude '*.egg-info' \
  --exclude .env --exclude .aria2-secret \
  ./ "$HOST:$DEST/"

say "python environment"
ssh "$HOST" "test -d $DEST/.venv || python3 -m venv $DEST/.venv"
ssh "$HOST" "$DEST/.venv/bin/pip -q install --upgrade pip"
ssh "$HOST" "$DEST/.venv/bin/pip -q install -e $DEST"

say "aria2 configuration"
ssh "$HOST" "if [ ! -f $SECRET_FILE ]; then openssl rand -hex 32 > $SECRET_FILE; chmod 600 $SECRET_FILE; fi"
ssh "$HOST" "SECRET=\$(cat $SECRET_FILE); sed \"s|__SECRET__|\$SECRET|\" $DEST/deploy/aria2.conf > /home/arnav/.config/aria2/aria2.conf; chmod 600 /home/arnav/.config/aria2/aria2.conf; touch /home/arnav/.config/aria2/aria2.session"

say "qBittorrent bridge configuration"
ssh "$HOST" "if [ ! -f $ENV_FILE ]; then
  printf 'SHELF_QB_URL=%s\nSHELF_QB_USER=%s\nSHELF_QB_PASS=%s\n' '$QB_URL' '$QB_USER' '$QB_PASS' > $ENV_FILE
  chmod 600 $ENV_FILE
fi
grep -q '^SHELF_QB_PASS=.\+' $ENV_FILE || echo 'qBittorrent bridge stays off: set SHELF_QB_PASS in $ENV_FILE'"

say "systemd units"
ssh "$HOST" "sudo -n cp $DEST/deploy/aria2.service $DEST/deploy/shelf.service $DEST/deploy/shelf.socket /etc/systemd/system/
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now aria2.service
sudo -n systemctl enable --now shelf.socket
sudo -n systemctl restart aria2.service
sudo -n systemctl restart shelf.service"

say "wait for the aria2 RPC"
ssh "$HOST" 'for _ in $(seq 1 20); do
  curl -sf -o /dev/null -H "content-type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"aria2.getVersion\"}" \
    http://127.0.0.1:6800/jsonrpc && { echo "rpc up"; break; }
  sleep 1
done'

say "smoke test through the socket"
ssh "$HOST" 'curl -s -o /dev/null -w "index %{http_code}\n" http://127.0.0.1:5000/'
ssh "$HOST" 'curl -s http://127.0.0.1:5000/api/state | head -c 160; echo'

printf '\nShelf is at http://%s:5000\n' "$(ssh "$HOST" hostname -I | awk '{print $1}')"

#!/usr/bin/env bash
#
# shelf.sh - install and run Shelf on this machine.
#
#   ./shelf.sh                     set everything up (default)
#   ./shelf.sh --setup             same, explicit
#   ./shelf.sh --start|--stop|--restart|--status|--logs|--uninstall
#
# Setup installs aria2 and the Python environment, writes the aria2 config,
# installs the systemd units, creates a Samba share for the download folder and
# opens the firewall when one is active. Running it twice is safe.
#
set -Eeuo pipefail

APP="shelf"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
VERSION="1.1.0"
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
PORT="5000"
IDLE_SECONDS="900"
DOWNLOAD_DIR=""
SHARE_PATH=""
SHARE_NAME=""
SHARE_WRITE="no"
DO_SAMBA="yes"
DO_FIREWALL="yes"
WITH_STREMIO="no"
ADDON_PORT="7020"
TMDB_KEY=""
STREMIO_KEY="${STREMIO_AUTH_KEY:-}"
STREMIO_ID="org.shelf.media"
YES="no"
CONFIGURED="no"
DRY_RUN="no"
ACTION="setup"

# ---------------------------------------------------------------- output

if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; AMBER=$'\033[38;5;214m'; RED=$'\033[31m'; OFF=$'\033[0m'
else
  BOLD=""; DIM=""; AMBER=""; RED=""; OFF=""
fi

log()  { printf '%s==>%s %s\n' "$AMBER" "$OFF" "$*"; }
note() { printf '    %s%s%s\n' "$DIM" "$*" "$OFF"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$RED" "$OFF" "$*" >&2; exit 1; }

# run a command, or print it during --dry-run
run() {
  if [ "$DRY_RUN" = "yes" ]; then
    printf '    would run: %s\n' "$*"
  else
    "$@"
  fi
}

# write stdin to a file (mode is the second argument)
write_file() {
  local path="$1" mode="${2:-644}"
  if [ "$DRY_RUN" = "yes" ]; then
    printf '    would write: %s\n' "$path"
    cat >/dev/null
  else
    mkdir -p "$(dirname "$path")"
    cat >"$path"
    chmod "$mode" "$path"
  fi
}

usage() {
  cat <<EOF
${BOLD}Shelf ${VERSION}${OFF} - download page for aria2

Usage: ./shelf.sh [action] [options]

Actions
  (none), --setup     install or refresh everything, then start it
  --start             start aria2 and the page
  --stop              stop both
  --restart           restart both
  --status            show what is running and where to reach it
  --logs              last 60 log lines (add --follow to stream)
  --stremio-first     move Shelf to the top of the Stremio addon list
  --uninstall         remove units and the Samba share, keep files and data
  --help, -h          this text
  --version, -v       print the version

Options for --setup
  --dir PATH          download folder (default: /media/Movies_and_Shows if it
                      exists, otherwise ~/Downloads)
  --share PATH        folder to share over Samba (default: the download folder)
  --name NAME         Samba share name (default: the folder name)
  --write             make the share writable (default: read-only guests)
  --with-stremio      also install the Stremio addon from ./stremio-addon
  --no-stremio        skip the addon (default)
  --addon-port PORT   addon port (default: 7020)
  --tmdb-key KEY      store a TMDB key for posters, optional
  --stremio-key KEY   Stremio account key, for --stremio-first
  --stremio-id ID     manifest id to move first (default org.shelf.media)
  --no-samba          skip Samba setup
  --no-firewall       skip firewall rules
  --port PORT         page port (default: 5000)
  --idle SECONDS      sleep after this much idle time (default: 900)
  --user USER         system user that runs the services (default: sudo caller)
  --dry-run           print what would happen, change nothing
  -y, --yes           take every default, never ask a question
  --follow            with --logs, stream instead of printing once

With no options and a terminal attached, --setup asks four short questions with
safe defaults. Any explicit option skips the questions, for scripted installs.

Examples
  sudo ./shelf.sh                       guided setup
  sudo ./shelf.sh --yes                 defaults, no questions
  sudo ./shelf.sh --with-stremio --dir /srv/media --name Media --write
  ./shelf.sh --stremio-first --stremio-key \$STREMIO_AUTH_KEY
  ./shelf.sh --status
EOF
}

# ---------------------------------------------------------------- arguments

while [ $# -gt 0 ]; do
  case "$1" in
    --setup)        ACTION="setup" ;;
    --start)        ACTION="start" ;;
    --stop)         ACTION="stop" ;;
    --restart)      ACTION="restart" ;;
    --status)       ACTION="status" ;;
    --logs)         ACTION="logs" ;;
    --stremio-first) ACTION="stremio-first" ;;
    --stremio-key)  STREMIO_KEY="${2:?--stremio-key needs a value}"; shift ;;
    --stremio-id)   STREMIO_ID="${2:?--stremio-id needs a value}"; shift ;;
    --uninstall)    ACTION="uninstall" ;;
    -h|--help)      usage; exit 0 ;;
    -v|--version)   printf '%s %s\n' "$APP" "$VERSION"; exit 0 ;;
    --dir)          DOWNLOAD_DIR="${2:?--dir needs a path}"; shift; CONFIGURED="yes" ;;
    --share)        SHARE_PATH="${2:?--share needs a path}"; shift; CONFIGURED="yes" ;;
    --name)         SHARE_NAME="${2:?--name needs a value}"; shift; CONFIGURED="yes" ;;
    --port)         PORT="${2:?--port needs a number}"; shift; CONFIGURED="yes" ;;
    --idle)         IDLE_SECONDS="${2:?--idle needs seconds}"; shift; CONFIGURED="yes" ;;
    --user)         RUN_USER="${2:?--user needs a name}"; shift; CONFIGURED="yes" ;;
    --write)        SHARE_WRITE="yes"; CONFIGURED="yes" ;;
    --with-stremio) WITH_STREMIO="yes"; CONFIGURED="yes" ;;
    --no-stremio)   WITH_STREMIO="no"; CONFIGURED="yes" ;;
    --addon-port)   ADDON_PORT="${2:?--addon-port needs a number}"; shift; CONFIGURED="yes" ;;
    --tmdb-key)     TMDB_KEY="${2:?--tmdb-key needs a value}"; shift; CONFIGURED="yes" ;;
    --no-samba)     DO_SAMBA="no"; CONFIGURED="yes" ;;
    --no-firewall)  DO_FIREWALL="no"; CONFIGURED="yes" ;;
    --dry-run)      DRY_RUN="yes" ;;
    -y|--yes|--non-interactive) YES="yes" ;;
    --follow)       FOLLOW="yes" ;;
    *)              die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

FOLLOW="${FOLLOW:-no}"

[ -n "$RUN_HOME" ] || die "user not found: $RUN_USER"

need_root() {
  [ "$(id -u)" -eq 0 ] && return 0
  [ "$DRY_RUN" = "yes" ] && return 0
  command -v sudo >/dev/null 2>&1 || die "run this as root (sudo missing)"
  log "elevating with sudo"
  exec sudo -E bash "${BASH_SOURCE[0]}" "$@"
}

case "$ACTION" in
  setup|start|stop|restart|logs|uninstall) need_root "$@" ;;
esac

# ---------------------------------------------------------------- guided setup

ask() {
  local prompt="$1" default="$2" answer=""
  printf '  %s %s[%s]%s: ' "$prompt" "$DIM" "$default" "$OFF" >&2
  read -r answer || true
  printf '%s' "${answer:-$default}"
}

guided_setup() {
  [ "$ACTION" = "setup" ] || return 0
  [ "$YES" = "yes" ] && return 0
  [ "$CONFIGURED" = "yes" ] && return 0
  [ -t 0 ] || return 0

  printf '\n%sShelf setup%s  press Enter to accept the default in brackets\n\n' "$BOLD" "$OFF"
  DOWNLOAD_DIR="$(ask "Download folder" "$DOWNLOAD_DIR")"
  PORT="$(ask "Page port" "$PORT")"

  local share addon firewall
  share="$(ask "Share the download folder over Samba (y/n)" "y")"
  case "$share" in n|N|no|NO) DO_SAMBA="no" ;; esac

  addon="$(ask "Install the Stremio addon (y/n)" "n")"
  case "$addon" in
    y|Y|yes|YES)
      WITH_STREMIO="yes"
      ADDON_PORT="$(ask "Addon port" "$ADDON_PORT")"
      ;;
  esac

  firewall="$(ask "Open firewall ports (y/n)" "y")"
  case "$firewall" in n|N|no|NO) DO_FIREWALL="no" ;; esac

  printf '\n  folder %s\n  page   http://<host>:%s\n  samba  %s\n  addon  %s\n  firewall %s\n\n' \
    "$DOWNLOAD_DIR" "$PORT" "$DO_SAMBA" "$WITH_STREMIO" "$DO_FIREWALL"
  case "$(ask "Looks right (y/n)" "y")" in
    n|N|no|NO) die "stopped; nothing changed" ;;
  esac
}

# ---------------------------------------------------------------- platform

PKG=""
detect_package_manager() {
  for candidate in apt-get dnf pacman zypper; do
    if command -v "$candidate" >/dev/null 2>&1; then PKG="$candidate"; return 0; fi
  done
  PKG=""
}

pkg_install() {
  local packages=("$@")
  [ "${#packages[@]}" -eq 0 ] && return 0
  case "$PKG" in
    apt-get) run apt-get update -qq; run apt-get install -y "${packages[@]}" ;;
    dnf)     run dnf install -y "${packages[@]}" ;;
    pacman)  run pacman -S --noconfirm --needed "${packages[@]}" ;;
    zypper)  run zypper --non-interactive install "${packages[@]}" ;;
    *)       warn "no supported package manager; install manually: ${packages[*]}" ;;
  esac
}

package_for() {
  case "$1" in
    aria2c)      printf 'aria2' ;;
    python3)     printf 'python3' ;;
    smbd)        printf 'samba' ;;
    *)           printf '%s' "$1" ;;
  esac
}

require_commands() {
  detect_package_manager
  local missing=() packages=()
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
  done
  [ "${#missing[@]}" -eq 0 ] && return 0
  note "missing: ${missing[*]}"
  for cmd in "${missing[@]}"; do
    packages+=("$(package_for "$cmd")")
  done
  pkg_install "${packages[@]}"
}

# ---------------------------------------------------------------- aria2

ARIA2_CONF="$RUN_HOME/.config/aria2/aria2.conf"
ARIA2_SECRET_FILE="$REPO_DIR/.aria2-secret"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-}"
if [ -z "$DOWNLOAD_DIR" ]; then
  if [ -d /media/Movies_and_Shows ]; then DOWNLOAD_DIR="/media/Movies_and_Shows"; else DOWNLOAD_DIR="$RUN_HOME/Downloads"; fi
fi

install_aria2() {
  require_commands aria2c
  log "aria2 configuration"
  local secret=""
  if [ -f "$ARIA2_SECRET_FILE" ]; then
    secret="$(cat "$ARIA2_SECRET_FILE")"
  else
    secret="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    printf '%s' "$secret" | write_file "$ARIA2_SECRET_FILE" 600
  fi
  [ "$DRY_RUN" = "yes" ] || chown "$RUN_USER" "$ARIA2_SECRET_FILE" 2>/dev/null || true

  run install -d -m 755 -o "$RUN_USER" -g "$RUN_USER" "$DOWNLOAD_DIR" 2>/dev/null \
    || run mkdir -p "$DOWNLOAD_DIR"

  write_file "$ARIA2_CONF" 600 <<CONF
## aria2, configured by shelf.sh
enable-rpc=true
rpc-listen-all=false
rpc-listen-port=6800
rpc-secret=$secret
rpc-allow-origin-all=false

dir=$DOWNLOAD_DIR
continue=true
file-allocation=none
disk-cache=16M
max-concurrent-downloads=4
max-connection-per-server=8
split=8
min-split-size=4M
max-overall-download-limit=0
max-overall-upload-limit=1M

bt-enable-lpd=true
bt-max-peers=60
bt-request-peer-speed-limit=200K
enable-dht=true
enable-peer-exchange=true
follow-torrent=true
seed-time=0
seed-ratio=0

save-session=$RUN_HOME/.config/aria2/aria2.session
input-file=$RUN_HOME/.config/aria2/aria2.session
save-session-interval=30
force-save=true

summary-interval=0
console-log-level=warn
log=$RUN_HOME/.config/aria2/aria2.log
log-level=warn
CONF
  if [ "$DRY_RUN" = "no" ]; then
    chown -R "$RUN_USER" "$RUN_HOME/.config/aria2"
    [ -f "$RUN_HOME/.config/aria2/aria2.session" ] || : >"$RUN_HOME/.config/aria2/aria2.session"
    chown "$RUN_USER" "$RUN_HOME/.config/aria2/aria2.session"
  fi
}

# ---------------------------------------------------------------- app

fetch_fonts() {
  local font_dir="$REPO_DIR/static/fonts"
  [ -f "$font_dir/google-sans-text-400.woff2" ] && return 0
  log "fetching web fonts"
  mkdir -p "$font_dir"
  run python3 - "$font_dir" <<'FONTS_PY'
import os, re, sys, urllib.request

dest = sys.argv[1]
url = ("https://fonts.googleapis.com/css2?family=Google+Sans:wght@500;700"
       "&family=Google+Sans+Text:wght@400;500;700&display=swap")
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"})
css = urllib.request.urlopen(req, timeout=20).read().decode()

blocks = re.findall(r"(?:/\*\s*(\S+)\s*\*/\s*)?@font-face\s*\{([^}]+)\}", css)
for subset, body in blocks:
    if subset not in ("latin", None, ""):
        continue
    family = re.search(r"font-family:\s*'([^']+)'", body).group(1)
    weight = re.search(r"font-weight:\s*(\d+)", body).group(1)
    match = re.search(r"src:\s*url\((https://[^)]+)\)", body)
    if not match:
        continue
    slug = "google-sans" if family == "Google Sans" else "google-sans-text"
    path = os.path.join(dest, f"{slug}-{weight}.woff2")
    urllib.request.urlretrieve(match.group(1), path)
    print("  ", os.path.basename(path))
FONTS_PY
  [ "$DRY_RUN" = "yes" ] || chown -R "$RUN_USER" "$font_dir"
}

install_app() {
  require_commands python3 curl systemctl
  fetch_fonts
  log "python environment in $REPO_DIR/.venv"
  if [ ! -x "$REPO_DIR/.venv/bin/python" ]; then
    run python3 -m venv "$REPO_DIR/.venv"
  fi
  run "$REPO_DIR/.venv/bin/pip" -q install --upgrade pip
  run "$REPO_DIR/.venv/bin/pip" -q install "$REPO_DIR"
  [ "$DRY_RUN" = "yes" ] || chown -R "$RUN_USER" "$REPO_DIR/.venv"
}

install_units() {
  log "systemd units"
  write_file /etc/systemd/system/aria2.service 644 <<UNIT
[Unit]
Description=aria2 download engine (Shelf)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
ExecStart=/usr/bin/aria2c --conf-path=$ARIA2_CONF
Restart=on-failure
RestartSec=5
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
UNIT

  write_file /etc/systemd/system/$APP.socket 644 <<UNIT
[Unit]
Description=Shelf socket (wakes the page on demand)

[Socket]
ListenStream=0.0.0.0:$PORT
Accept=no
NoDelay=true

[Install]
WantedBy=sockets.target
UNIT

  write_file /etc/systemd/system/$APP.service 644 <<UNIT
[Unit]
Description=Shelf download page
After=network-online.target aria2.service
Wants=aria2.service
Requires=$APP.socket
After=$APP.socket

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$REPO_DIR
EnvironmentFile=-$REPO_DIR/.env
Environment=SHELF_IDLE_SECONDS=$IDLE_SECONDS
Environment=PYTHONUNBUFFERED=1
ExecStart=$REPO_DIR/.venv/bin/uvicorn shelf.web:app --fd 3 --proxy-headers
StandardInput=null
Restart=on-failure
RestartSec=2
TimeoutStopSec=5

[Install]
Also=$APP.socket
WantedBy=multi-user.target
UNIT

  run systemctl daemon-reload
}

ADDON_DIR="$REPO_DIR/stremio-addon"

install_addon() {
  [ -d "$ADDON_DIR" ] || die "stremio-addon is missing from $REPO_DIR"
  log "stremio addon"
  require_commands python3
  if [ ! -x "$ADDON_DIR/.venv/bin/python" ]; then
    run python3 -m venv "$ADDON_DIR/.venv"
  fi
  run "$ADDON_DIR/.venv/bin/pip" -q install --upgrade pip
  run "$ADDON_DIR/.venv/bin/pip" -q install -r "$ADDON_DIR/requirements.txt"
  if [ "$DRY_RUN" = "no" ]; then
    chown -R "$RUN_USER" "$ADDON_DIR/.venv"
  fi

  local tmdb_env="$RUN_HOME/.config/shelf/tmdb.env"
  if [ -n "$TMDB_KEY" ]; then
    printf 'TMDB_API_KEY=%s\n' "$TMDB_KEY" | write_file "$tmdb_env" 600
    [ "$DRY_RUN" = "yes" ] || chown "$RUN_USER" "$tmdb_env"
  elif [ -f "$tmdb_env" ]; then
    note "keeping the existing TMDB key in $tmdb_env"
  fi

  write_file /etc/systemd/system/stremio-shelf.socket 644 <<UNIT
[Unit]
Description=Shelf addon for Stremio (socket, wakes on demand)

[Socket]
ListenStream=0.0.0.0:$ADDON_PORT
Accept=no
NoDelay=true

[Install]
WantedBy=sockets.target
UNIT

  write_file /etc/systemd/system/stremio-shelf.service 644 <<UNIT
[Unit]
Description=Shelf addon for Stremio
After=network-online.target
Requires=stremio-shelf.socket
After=stremio-shelf.socket

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$ADDON_DIR
EnvironmentFile=-$RUN_HOME/.config/shelf/tmdb.env
Environment=SHELF_ROOT=$DOWNLOAD_DIR
Environment=SHELF_IDLE_SECONDS=1800
Environment=PYTHONUNBUFFERED=1
ExecStart=$ADDON_DIR/.venv/bin/uvicorn app:app --fd 3 --proxy-headers
StandardInput=null
Restart=on-failure
RestartSec=2
TimeoutStopSec=5

[Install]
Also=stremio-shelf.socket
WantedBy=multi-user.target
UNIT

  run systemctl daemon-reload
  run systemctl enable --now stremio-shelf.socket
  run systemctl restart stremio-shelf.service
}

start_services() {
  log "starting aria2 and the page"
  run systemctl enable --now aria2.service
  run systemctl enable --now "$APP.socket"
  run systemctl restart aria2.service
}

stop_services() {
  log "stopping"
  run systemctl stop "$APP.service" "$APP.socket" aria2.service 2>/dev/null || true
}

# ---------------------------------------------------------------- samba

sanitize_name() {
  local raw="$1"
  printf '%s' "$raw" | sed -E 's/[^A-Za-z0-9._-]+/_/g; s/^_+//; s/_+$//' | cut -c1-32
}

configure_samba() {
  local path="$1" name="$2"
  log "samba share for $path"
  require_commands smbd

  run mkdir -p /etc/samba/shares.d

  # an earlier hand-written block with the same name would collide with the include
  if grep -qsE "^\[$name\]$" /etc/samba/smb.conf; then
    note "removing the old [$name] block from smb.conf"
    if [ "$DRY_RUN" = "no" ]; then
      cp -a /etc/samba/smb.conf "/etc/samba/smb.conf.bak.$(date +%F-%H%M%S)"
      awk -v target="[$name]" '
        $0 == target { skip = 1; next }
        skip && /^\[/ { skip = 0 }
        !skip { print }
      ' /etc/samba/smb.conf >/etc/samba/smb.conf.tmp
      mv /etc/samba/smb.conf.tmp /etc/samba/smb.conf
    fi
  fi

  local include="include = /etc/samba/shares.d/$name.conf"
  local wildcard="include = /etc/samba/shares.d/*.conf"
  if [ "$DRY_RUN" = "no" ]; then
    cp -a /etc/samba/smb.conf "/etc/samba/smb.conf.bak.$(date +%F-%H%M%S)"
    # older versions of this script used a wildcard include, which some Samba
    # builds do not expand; drop it and include the share file explicitly
    if grep -qsF "$wildcard" /etc/samba/smb.conf; then
      grep -vF "$wildcard" /etc/samba/smb.conf >/etc/samba/smb.conf.tmp
      mv /etc/samba/smb.conf.tmp /etc/samba/smb.conf
    fi
    if ! grep -qsF "$include" /etc/samba/smb.conf; then
      if grep -qE '^\[global\]' /etc/samba/smb.conf; then
        awk -v line="$include" '{ print } /^\[global\]/ && !done { print line; done = 1 }' \
          /etc/samba/smb.conf >/etc/samba/smb.conf.tmp
        mv /etc/samba/smb.conf.tmp /etc/samba/smb.conf
      else
        { printf '[global]\n%s\n\n' "$include"; cat /etc/samba/smb.conf; } >/etc/samba/smb.conf.tmp
        mv /etc/samba/smb.conf.tmp /etc/samba/smb.conf
      fi
    fi
  else
    printf '    would add to smb.conf: %s\n' "$include"
  fi

  local read_only="yes"
  [ "$SHARE_WRITE" = "yes" ] && read_only="no"

  write_file "/etc/samba/shares.d/$name.conf" 644 <<CONF
[$name]
   comment = $name
   path = $path
   browseable = yes
   guest ok = yes
   read only = $read_only
   create mask = 0644
   directory mask = 0755
   force user = $RUN_USER
CONF

  if [ "$DRY_RUN" = "no" ]; then
    if [ "$(stat -c %U "$path")" != "$RUN_USER" ]; then
      note "taking ownership of $path"
      chown -R "$RUN_USER:$RUN_USER" "$path"
    fi
    chmod 755 "$path" 2>/dev/null || true
    if ! testparm -s >/dev/null 2>&1; then
      die "testparm rejected the configuration; existing smb.conf is untouched"
    fi
  fi

  local smbd="smbd" nmbd="nmbd"
  if ! systemctl list-unit-files 2>/dev/null | grep -qE '^[[:space:]]*smbd\.service'; then
    smbd="smb"; nmbd="nmb"
  fi
  run systemctl enable --now "$smbd" "$nmbd" 2>/dev/null || run systemctl enable --now "$smbd"
}

# ---------------------------------------------------------------- firewall

configure_firewall() {
  log "firewall"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    note "ufw is active"
    run ufw allow "$PORT"/tcp
    [ "$WITH_STREMIO" = "yes" ] && run ufw allow "$ADDON_PORT"/tcp
    if ufw app list 2>/dev/null | grep -q '^  Samba'; then
      run ufw allow Samba
    else
      run ufw allow 137,138/udp
      run ufw allow 139,445/tcp
    fi
    return 0
  fi

  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    note "firewalld is active"
    run firewall-cmd --permanent --add-port="$PORT"/tcp
    [ "$WITH_STREMIO" = "yes" ] && run firewall-cmd --permanent --add-port="$ADDON_PORT"/tcp
    run firewall-cmd --permanent --add-service=samba
    run firewall-cmd --reload
    return 0
  fi

  if command -v iptables >/dev/null 2>&1 && iptables -L INPUT -n >/dev/null 2>&1 \
     && [ "$(iptables -S INPUT | wc -l)" -gt 1 ]; then
    note "iptables rules present"
    local rules=("-p tcp --dport $PORT" "-p tcp --dport 139" "-p tcp --dport 445" "-p udp --dport 137" "-p udp --dport 138")
    [ "$WITH_STREMIO" = "yes" ] && rules+=("-p tcp --dport $ADDON_PORT")
    for rule in "${rules[@]}"; do
      # shellcheck disable=SC2086
      if ! iptables -C INPUT $rule -j ACCEPT 2>/dev/null; then
        # shellcheck disable=SC2086
        run iptables -I INPUT $rule -j ACCEPT
      fi
    done
    if command -v netfilter-persistent >/dev/null 2>&1; then
      run netfilter-persistent save
    else
      note "rules are live but not persisted; save them with your distro's tool"
    fi
    return 0
  fi

  if command -v nft >/dev/null 2>&1 && nft list ruleset 2>/dev/null | grep -q 'table'; then
    warn "nftables is in use; open 137-139/udp and 139,445/tcp yourself"
    return 0
  fi

  note "no active firewall found, nothing to open"
}

# ---------------------------------------------------------------- actions

do_setup() {
  guided_setup
  log "setting up $APP in $REPO_DIR for user $RUN_USER"
  install_aria2
  install_app
  install_units

  if [ "$DO_SAMBA" = "yes" ]; then
    SHARE_PATH="${SHARE_PATH:-$DOWNLOAD_DIR}"
    [ -d "$SHARE_PATH" ] || run mkdir -p "$SHARE_PATH"
    configure_samba "$SHARE_PATH" "${SHARE_NAME:-$(sanitize_name "$(basename "$SHARE_PATH")")}"
  else
    note "samba skipped"
  fi

  if [ "$WITH_STREMIO" = "yes" ]; then
    install_addon
    [ -n "$STREMIO_KEY" ] && do_stremio_first
  fi

  [ "$DO_FIREWALL" = "yes" ] && configure_firewall || note "firewall skipped"

  start_services
  do_status
}

do_status() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  printf '\n%s%-12s%s %s\n' "$BOLD" "page" "$OFF" "http://${ip:-127.0.0.1}:$PORT"
  printf '%s%-12s%s %s\n' "$BOLD" "downloads" "$OFF" "$DOWNLOAD_DIR"
  printf '%s%-12s%s %s\n' "$BOLD" "aria2 rpc" "$OFF" "http://127.0.0.1:6800/jsonrpc"
  local share
  for share_file in /etc/samba/shares.d/*.conf; do
    [ -f "$share_file" ] || continue
    share="$(basename "$share_file" .conf)"
    printf '%s%-12s%s %s\n' "$BOLD" "samba" "$OFF" "smb://${ip:-localhost}/$share"
  done
  if [ -f /etc/systemd/system/stremio-shelf.socket ]; then
    printf '%s%-12s%s %s\n' "$BOLD" "addon" "$OFF" "http://${ip:-127.0.0.1}:$(grep -oE '[0-9]+$' /etc/systemd/system/stremio-shelf.socket | head -1)/manifest.json"
  fi
  printf '%s%-12s%s' "$BOLD" "services" "$OFF"
  for unit in aria2.service "$APP.socket" "$APP.service"; do
    printf ' %s=%s' "$unit" "$(systemctl is-active "$unit" 2>/dev/null || echo unknown)"
  done
  printf '\n\n'
}

do_uninstall() {
  log "removing units"
  run systemctl disable --now "$APP.socket" "$APP.service" aria2.service 2>/dev/null || true
  run systemctl disable --now stremio-shelf.socket stremio-shelf.service 2>/dev/null || true
  run rm -f "/etc/systemd/system/$APP.socket" "/etc/systemd/system/$APP.service" \
    /etc/systemd/system/stremio-shelf.socket /etc/systemd/system/stremio-shelf.service
  run systemctl daemon-reload
  if [ -n "$SHARE_NAME" ]; then
    run rm -f "/etc/samba/shares.d/$SHARE_NAME.conf"
    if [ "$DRY_RUN" = "no" ] && [ -f /etc/samba/smb.conf ]; then
      grep -vF "include = /etc/samba/shares.d/$SHARE_NAME.conf" /etc/samba/smb.conf >/etc/samba/smb.conf.tmp
      mv /etc/samba/smb.conf.tmp /etc/samba/smb.conf
    fi
    run systemctl restart smbd 2>/dev/null || true
  fi
  note "kept: $REPO_DIR, $DOWNLOAD_DIR and the aria2 configuration"
}

do_stremio_first() {
  [ -n "$STREMIO_KEY" ] || die "pass --stremio-key or set STREMIO_AUTH_KEY"
  [ -f "$REPO_DIR/tools/stremio-priority.py" ] || die "tools/stremio-priority.py is missing"
  log "moving $STREMIO_ID to the top of the Stremio addon list"
  export STREMIO_AUTH_KEY="$STREMIO_KEY"
  if [ "$DRY_RUN" = "yes" ]; then
    run python3 "$REPO_DIR/tools/stremio-priority.py" --id "$STREMIO_ID" --dry-run
  else
    run python3 "$REPO_DIR/tools/stremio-priority.py" --id "$STREMIO_ID"
  fi
}

case "$ACTION" in
  setup)     do_setup ;;
  start)     start_services; do_status ;;
  stop)      stop_services ;;
  restart)   stop_services; start_services; do_status ;;
  status)    do_status ;;
  logs)      if [ "$FOLLOW" = "yes" ]; then journalctl -u "$APP" -f; else journalctl -u "$APP" -n 60 --no-pager; fi ;;
  stremio-first) do_stremio_first ;;
  uninstall) do_uninstall ;;
esac

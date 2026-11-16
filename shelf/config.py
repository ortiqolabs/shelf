"""Everything that comes from the environment: paths, the aria2 secret, timers."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"

ARIA2_CONF = Path(
    os.environ.get("SHELF_ARIA2_CONF", Path.home() / ".config" / "aria2" / "aria2.conf")
)
ARIA2_RPC = os.environ.get("SHELF_ARIA2_RPC", "http://127.0.0.1:6800/jsonrpc")

# the service exits after this long without a request or an open event stream
IDLE_SECONDS = int(os.environ.get("SHELF_IDLE_SECONDS", "900"))


def conf_value(key: str, default: str = "") -> str:
    """Read a single key from aria2.conf, tolerating a missing file."""
    try:
        with ARIA2_CONF.open() as fh:
            for line in fh:
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


RPC_SECRET = conf_value("rpc-secret")
DOWNLOAD_DIR = conf_value("dir", str(Path.home()))

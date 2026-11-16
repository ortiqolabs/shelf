"""Read and write the aria2 settings the page is allowed to change."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import config

# key in aria2.conf -> the JSON field the page uses
FIELDS = {
    "dir": "downloadDir",
    "max-concurrent-downloads": "maxConcurrent",
    "max-overall-download-limit": "maxDownloadLimit",
}

DEFAULT_MAX_CONCURRENT = 4


class SettingsError(ValueError):
    """A value that cannot be applied, with a message safe to show a person."""


def _read_lines() -> list[str]:
    try:
        return config.ARIA2_CONF.read_text().splitlines()
    except OSError:
        return []


def current() -> dict[str, Any]:
    values = {key: config.conf_value(key) for key in FIELDS}
    return {
        "downloadDir": values["dir"] or config.DOWNLOAD_DIR,
        "maxConcurrent": int(values["max-concurrent-downloads"] or DEFAULT_MAX_CONCURRENT),
        "maxDownloadLimit": values["max-overall-download-limit"] or "0",
        "configPath": str(config.ARIA2_CONF),
    }


def parse_limit(raw: str | int) -> str:
    """Normalise a speed limit to what aria2 accepts: 0, 500K, 5M, 1G."""
    if isinstance(raw, int):
        raw = str(raw)
    text = str(raw).strip().upper()
    if text in ("", "0", "UNLIMITED"):
        return "0"
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMG]?)", text)
    if not match:
        raise SettingsError("Speed limit should look like 0, 500K or 5M")
    number, unit = match.groups()
    if float(number) <= 0:
        return "0"
    if float(number) > 1024:
        raise SettingsError("Speed limit is too large")
    return f"{number}{unit}"


def validate_dir(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise SettingsError("Download folder cannot be empty")
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        raise SettingsError("Use an absolute path, for example /media/Movies_and_Shows")
    if ".." in path.parts:
        raise SettingsError("The path cannot contain ..")
    if path.exists() and not path.is_dir():
        raise SettingsError("That path exists and is not a folder")
    if path.exists() and not os.access(path, os.W_OK):
        raise SettingsError("That folder is not writable by the service user")
    return str(path)


def validate(payload: dict[str, Any]) -> dict[str, str]:
    """Return the aria2.conf updates, or raise SettingsError."""
    updates: dict[str, str] = {}

    if "downloadDir" in payload:
        updates["dir"] = validate_dir(payload["downloadDir"])

    if "maxConcurrent" in payload:
        try:
            value = int(payload["maxConcurrent"])
        except (TypeError, ValueError):
            raise SettingsError("Concurrent downloads must be a number") from None
        if not 1 <= value <= 16:
            raise SettingsError("Concurrent downloads must be between 1 and 16")
        updates["max-concurrent-downloads"] = str(value)

    if "maxDownloadLimit" in payload:
        updates["max-overall-download-limit"] = parse_limit(payload["maxDownloadLimit"])

    if not updates:
        raise SettingsError("Nothing to change")
    return updates


def write(updates: dict[str, str]) -> None:
    """Rewrite only the changed keys, keeping every comment and unknown line."""
    lines = _read_lines()
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in updates and not line.startswith("#"):
            result.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            result.append(line)
    for key, value in updates.items():
        if key not in seen:
            result.append(f"{key}={value}")

    config.ARIA2_CONF.parent.mkdir(parents=True, exist_ok=True)
    config.ARIA2_CONF.write_text("\n".join(result).rstrip("\n") + "\n")


async def apply(aria2, updates: dict[str, str]) -> None:
    """Make the change live without restarting the daemon."""
    payload = {key: value for key, value in updates.items()}
    if "dir" in payload:
        Path(payload["dir"]).mkdir(parents=True, exist_ok=True)
    result = await aria2.call("aria2.changeGlobalOption", [payload])
    if isinstance(result, dict) and result.get("message"):
        raise SettingsError(result["message"])


async def save(aria2, payload: dict[str, Any]) -> dict[str, Any]:
    updates = validate(payload)
    await apply(aria2, updates)
    write(updates)
    return current()

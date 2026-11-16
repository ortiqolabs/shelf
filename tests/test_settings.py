import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path  # noqa: E402

from shelf import config, settings  # noqa: E402


def with_conf(tmpdir: str, text: str):
    path = Path(tmpdir) / "aria2.conf"
    path.write_text(text)
    config.ARIA2_CONF = path
    return path


def test_parse_limit_normalises_values():
    assert settings.parse_limit("0") == "0"
    assert settings.parse_limit("") == "0"
    assert settings.parse_limit(500) == "500"
    assert settings.parse_limit("500k") == "500K"
    assert settings.parse_limit("5M") == "5M"


def test_parse_limit_rejects_nonsense():
    for bad in ("fast", "5T", "1000000"):
        try:
            settings.parse_limit(bad)
        except settings.SettingsError:
            continue
        raise AssertionError(f"{bad} should be rejected")


def test_validate_dir_requires_absolute_path(tmpdir="/tmp"):
    try:
        settings.validate_dir("media/downloads")
    except settings.SettingsError as exc:
        assert "absolute" in str(exc)
    else:
        raise AssertionError("relative path should be rejected")


def test_validate_rejects_too_many_connections():
    try:
        settings.validate({"maxConcurrent": 99})
    except settings.SettingsError as exc:
        assert "between 1 and 16" in str(exc)
    else:
        raise AssertionError("99 should be rejected")


def test_write_updates_only_named_keys_and_keeps_comments():
    with tempfile.TemporaryDirectory() as tmp:
        path = with_conf(tmp, "# header\ndir=/old\nmax-concurrent-downloads=2\nrpc-secret=abc\n")
        settings.write({"dir": "/new", "max-concurrent-downloads": "6"})
        text = path.read_text()
        assert "# header" in text
        assert "dir=/new" in text
        assert "max-concurrent-downloads=6" in text
        assert "rpc-secret=abc" in text


def test_write_appends_missing_keys():
    with tempfile.TemporaryDirectory() as tmp:
        path = with_conf(tmp, "dir=/old\n")
        settings.write({"max-overall-download-limit": "5M"})
        assert "max-overall-download-limit=5M" in path.read_text()


def test_current_reads_the_file():
    with tempfile.TemporaryDirectory() as tmp:
        with_conf(tmp, "dir=/media/Shows\nmax-concurrent-downloads=3\n")
        snapshot = settings.current()
        assert snapshot["downloadDir"] == "/media/Shows"
        assert snapshot["maxConcurrent"] == 3
        assert snapshot["maxDownloadLimit"] == "0"


def test_validate_empty_payload_is_rejected():
    try:
        settings.validate({})
    except settings.SettingsError:
        return
    raise AssertionError("empty payload should be rejected")

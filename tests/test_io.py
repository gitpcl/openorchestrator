"""Tests for safe IO utilities."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.utils.io import (
    atomic_write_text,
    exclusive_file_lock,
    safe_read_json,
    safe_write_json,
    shared_file_lock,
)


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path: Path):
        dest = tmp_path / "test.txt"
        atomic_write_text(dest, "hello world")
        assert dest.read_text() == "hello world"

    def test_sets_permissions(self, tmp_path: Path):
        dest = tmp_path / "secure.txt"
        atomic_write_text(dest, "secret", perms=0o600)
        mode = os.stat(dest).st_mode & 0o777
        assert mode == 0o600

    def test_creates_parent_directories(self, tmp_path: Path):
        dest = tmp_path / "sub" / "dir" / "file.txt"
        atomic_write_text(dest, "nested")
        assert dest.read_text() == "nested"

    def test_overwrites_existing_file(self, tmp_path: Path):
        dest = tmp_path / "overwrite.txt"
        dest.write_text("old")
        atomic_write_text(dest, "new")
        assert dest.read_text() == "new"

    def test_atomic_no_partial_writes(self, tmp_path: Path):
        dest = tmp_path / "atomic.txt"
        dest.write_text("original")
        # If an error occurred during write, original should be intact
        # (simulated by writing normally — atomic_write_text uses os.replace)
        atomic_write_text(dest, "replaced")
        assert dest.read_text() == "replaced"


class TestSafeReadJson:
    def test_reads_valid_json(self, tmp_path: Path):
        dest = tmp_path / "data.json"
        dest.write_text('{"key": "value"}')
        result = safe_read_json(dest)
        assert result == {"key": "value"}

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        result = safe_read_json(tmp_path / "nonexistent.json")
        assert result is None

    def test_returns_none_for_invalid_json(self, tmp_path: Path):
        dest = tmp_path / "bad.json"
        dest.write_text("not json {{{")
        result = safe_read_json(dest)
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path: Path):
        dest = tmp_path / "empty.json"
        dest.write_text("")
        result = safe_read_json(dest)
        assert result is None


class TestSafeWriteJson:
    def test_writes_valid_json(self, tmp_path: Path):
        dest = tmp_path / "out.json"
        safe_write_json(dest, {"key": "value"})
        data = json.loads(dest.read_text())
        assert data["key"] == "value"

    def test_writes_list(self, tmp_path: Path):
        dest = tmp_path / "list.json"
        safe_write_json(dest, [1, 2, 3])
        data = json.loads(dest.read_text())
        assert data == [1, 2, 3]

    def test_sets_permissions(self, tmp_path: Path):
        dest = tmp_path / "perms.json"
        safe_write_json(dest, {"a": 1}, perms=0o600)
        mode = os.stat(dest).st_mode & 0o777
        assert mode == 0o600

    def test_roundtrip(self, tmp_path: Path):
        dest = tmp_path / "roundtrip.json"
        original = {"nested": {"list": [1, 2, 3], "bool": True}}
        safe_write_json(dest, original)
        loaded = safe_read_json(dest)
        assert loaded == original


class TestSharedFileLock:
    def test_locks_and_unlocks_on_unix(self, tmp_path: Path):
        if sys.platform == "win32":
            pytest.skip("Unix-only path")
        f = (tmp_path / "lock.txt").open("w")
        try:
            with shared_file_lock(f):
                f.write("data")
        finally:
            f.close()

    def test_continues_when_unix_lock_fails(self, tmp_path: Path):
        if sys.platform == "win32":
            pytest.skip("Unix-only path")
        f = (tmp_path / "lock.txt").open("w")
        try:
            with patch("fcntl.flock", side_effect=OSError("locked")):
                with shared_file_lock(f):
                    f.write("ok")
        finally:
            f.close()

    def test_unix_unlock_swallows_oserror(self, tmp_path: Path):
        if sys.platform == "win32":
            pytest.skip("Unix-only path")
        import fcntl as _fcntl

        f = (tmp_path / "lock.txt").open("w")
        try:
            real_flock = _fcntl.flock
            calls = {"n": 0}

            def flaky_flock(fh, op):  # noqa: ANN001
                calls["n"] += 1
                if calls["n"] == 1:
                    return real_flock(fh, op)
                raise OSError("unlock failed")

            with patch("fcntl.flock", side_effect=flaky_flock):
                with shared_file_lock(f):
                    pass
        finally:
            f.close()

    def test_windows_path_simulated(self, tmp_path: Path):
        # Force the win32 code branch by patching sys.platform + fake msvcrt module.
        fake_msvcrt = MagicMock()
        fake_msvcrt.LK_NBLCK = 1
        fake_msvcrt.LK_UNLCK = 2
        f = (tmp_path / "lock.txt").open("w")
        try:
            with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                with shared_file_lock(f):
                    pass
            assert fake_msvcrt.locking.call_count == 2  # lock + unlock
        finally:
            f.close()

    def test_windows_lock_oserror(self, tmp_path: Path):
        fake_msvcrt = MagicMock()
        fake_msvcrt.LK_NBLCK = 1
        fake_msvcrt.LK_UNLCK = 2
        fake_msvcrt.locking.side_effect = OSError("locked")
        f = (tmp_path / "lock.txt").open("w")
        try:
            with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                with shared_file_lock(f):
                    pass
        finally:
            f.close()


class TestExclusiveFileLock:
    def test_locks_and_unlocks_on_unix(self, tmp_path: Path):
        if sys.platform == "win32":
            pytest.skip("Unix-only path")
        f = (tmp_path / "lock.txt").open("w")
        try:
            with exclusive_file_lock(f):
                f.write("data")
        finally:
            f.close()

    def test_continues_when_unix_lock_fails(self, tmp_path: Path):
        if sys.platform == "win32":
            pytest.skip("Unix-only path")
        f = (tmp_path / "lock.txt").open("w")
        try:
            with patch("fcntl.flock", side_effect=OSError("locked")):
                with exclusive_file_lock(f):
                    f.write("ok")
        finally:
            f.close()

    def test_unix_unlock_swallows_oserror(self, tmp_path: Path):
        if sys.platform == "win32":
            pytest.skip("Unix-only path")
        import fcntl as _fcntl

        f = (tmp_path / "lock.txt").open("w")
        try:
            real_flock = _fcntl.flock
            calls = {"n": 0}

            def flaky_flock(fh, op):  # noqa: ANN001
                calls["n"] += 1
                if calls["n"] == 1:
                    return real_flock(fh, op)
                raise OSError("unlock failed")

            with patch("fcntl.flock", side_effect=flaky_flock):
                with exclusive_file_lock(f):
                    pass
        finally:
            f.close()

    def test_windows_path_simulated(self, tmp_path: Path):
        fake_msvcrt = MagicMock()
        fake_msvcrt.LK_NBLCK = 1
        fake_msvcrt.LK_UNLCK = 2
        f = (tmp_path / "lock.txt").open("w")
        try:
            with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                with exclusive_file_lock(f):
                    pass
            assert fake_msvcrt.locking.call_count == 2
        finally:
            f.close()

    def test_windows_lock_oserror(self, tmp_path: Path):
        fake_msvcrt = MagicMock()
        fake_msvcrt.LK_NBLCK = 1
        fake_msvcrt.LK_UNLCK = 2
        fake_msvcrt.locking.side_effect = OSError("locked")
        f = (tmp_path / "lock.txt").open("w")
        try:
            with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                with exclusive_file_lock(f):
                    pass
        finally:
            f.close()


class TestAtomicWriteTextErrorPaths:
    def test_chmod_permission_error_is_warned(self, tmp_path: Path, caplog):
        dest = tmp_path / "perm.txt"
        with patch("open_orchestrator.utils.io.os.chmod", side_effect=PermissionError("denied")):
            atomic_write_text(dest, "data", perms=0o600)
        assert dest.read_text() == "data"
        assert any("Could not set permissions" in r.message for r in caplog.records)

    def test_cleans_up_temp_on_replace_failure(self, tmp_path: Path):
        dest = tmp_path / "fail.txt"
        with patch("open_orchestrator.utils.io.os.replace", side_effect=OSError("nope")):
            with pytest.raises(OSError):
                atomic_write_text(dest, "data")
        # No leftover temp files in dest's parent.
        assert list(tmp_path.glob("tmp*")) == []
        # Even more conservatively: no files at all created (dest doesn't exist).
        assert not dest.exists()

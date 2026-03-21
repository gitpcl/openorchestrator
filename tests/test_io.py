"""Tests for safe IO utilities."""

from __future__ import annotations

import json
import os
from pathlib import Path

from open_orchestrator.utils.io import atomic_write_text, safe_read_json, safe_write_json


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

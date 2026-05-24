"""Permission tests for the shared SQLite ``open_db`` helper.

Every long-lived SQLite database in ``open_orchestrator`` (status,
memory_store, denial_tracker) flows through :func:`open_db`. These
databases sit under ``~/.open-orchestrator/`` on shared developer
machines; a default ``umask`` of ``0o022`` would leave them
world-readable, leaking transcripts, MEMORY index, and denial counts.

This module pins the ``0o600`` (owner read/write only) contract at the
chokepoint plus the WAL/SHM siblings spawned by WAL mode.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from open_orchestrator.core._db import open_db, secure_db_perms

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits are not enforced on Windows",
)

_OWNER_RW = 0o600
_PERM_MASK = 0o777


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode) & _PERM_MASK


def test_open_db_sets_owner_only_perms_on_new_file(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"

    conn = open_db(db_path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    assert db_path.exists()
    assert _mode(db_path) == _OWNER_RW


def test_open_db_secures_wal_and_shm_siblings(tmp_path: Path) -> None:
    db_path = tmp_path / "with-wal.db"

    # Keep the connection open across the perms re-apply: SQLite checkpoints
    # and may unlink ``-wal`` / ``-shm`` on close, so the assertions need to
    # run while the WAL files are still on disk.
    conn = open_db(db_path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('x')")
        conn.commit()

        wal = db_path.with_name(db_path.name + "-wal")
        shm = db_path.with_name(db_path.name + "-shm")

        # WAL mode produces both siblings after a committed write.
        assert wal.exists(), "WAL sibling missing — WAL mode may not be active"
        assert shm.exists(), "SHM sibling missing — WAL mode may not be active"

        # Simulate a stale loose-perms sibling, then re-secure via the helper.
        os.chmod(wal, 0o644)
        os.chmod(shm, 0o644)
        secure_db_perms(db_path)

        assert _mode(wal) == _OWNER_RW
        assert _mode(shm) == _OWNER_RW
    finally:
        conn.close()


def test_open_db_rechmods_existing_loose_file(tmp_path: Path) -> None:
    db_path = tmp_path / "loose.db"
    db_path.touch()
    os.chmod(db_path, 0o644)
    assert _mode(db_path) == 0o644

    conn = open_db(db_path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    assert _mode(db_path) == _OWNER_RW


def test_secure_db_perms_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idem.db"
    db_path.write_bytes(b"")
    os.chmod(db_path, _OWNER_RW)

    # Re-applying the same mode should not raise nor change the mode.
    secure_db_perms(db_path)
    secure_db_perms(db_path)
    secure_db_perms(db_path)

    assert _mode(db_path) == _OWNER_RW


def test_secure_db_perms_skips_missing_siblings(tmp_path: Path) -> None:
    db_path = tmp_path / "no-wal.db"
    db_path.write_bytes(b"")
    os.chmod(db_path, 0o644)

    # Must not raise even though -wal / -shm do not exist.
    secure_db_perms(db_path)

    assert _mode(db_path) == _OWNER_RW
    assert not db_path.with_name(db_path.name + "-wal").exists()
    assert not db_path.with_name(db_path.name + "-shm").exists()


def test_secure_db_perms_handles_missing_main_file(tmp_path: Path) -> None:
    db_path = tmp_path / "ghost.db"
    # Nothing on disk at all — must be a silent no-op, not an OSError.
    secure_db_perms(db_path)
    assert not db_path.exists()

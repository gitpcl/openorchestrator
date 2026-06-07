"""Unit tests for :mod:`open_orchestrator.core.status_schema`.

Covers the schema/migration carve-out (Sprint 027 Phase 8): pure module
behavior (apply schema, idempotent re-apply, column migrations, row
serialization, legacy JSON migration) is exercised here without spinning
up a full :class:`StatusTracker`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from open_orchestrator.core import status_schema
from open_orchestrator.core._db import open_db
from open_orchestrator.core.status_schema import (
    SCHEMA_VERSION,
    apply_schema,
    dt_to_str,
    ensure_schema,
    migrate_columns,
    migrate_legacy_json,
    record_usage,
    row_to_status,
    str_to_dt,
    upsert_status_row,
    usage_counts,
)
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """Open a fresh DB through the project's ``open_db`` helper."""
    db_path = tmp_path / "test.db"
    return open_db(db_path)


def _sample_status(name: str = "wt") -> WorktreeAIStatus:
    now = datetime.now()
    return WorktreeAIStatus(
        worktree_name=name,
        worktree_path=f"/tmp/{name}",
        branch=f"feature/{name}",
        tmux_session=f"owt-{name}",
        ai_tool="claude",
        activity_status=AIActivityStatus.WORKING,
        current_task="t",
        last_task_update=now,
        notes=None,
        modified_files=["a.py", "b.py"],
        backend_kind="tmux",
        backend_session_id=f"owt-{name}",
        backend_meta={"k": "v"},
        session_type="worktree",
        created_at=now,
        updated_at=now,
    )


class TestApplySchema:
    """``apply_schema`` provisions the expected tables on an empty DB."""

    def test_creates_expected_tables(self, conn: sqlite3.Connection) -> None:
        apply_schema(conn)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"worktree_status", "shared_notes", "metadata", "peer_messages"}.issubset(tables)

    def test_apply_schema_is_idempotent(self, conn: sqlite3.Connection) -> None:
        """Re-applying schema on an initialized DB must be a no-op."""
        apply_schema(conn)
        upsert_status_row(conn, _sample_status())
        apply_schema(conn)  # second call must not throw or wipe data
        rows = conn.execute("SELECT worktree_name FROM worktree_status").fetchall()
        assert [r[0] for r in rows] == ["wt"]


class TestEnsureSchema:
    """``ensure_schema`` writes the version stamp + commits."""

    def test_stamps_schema_version(self, conn: sqlite3.Connection) -> None:
        ensure_schema(conn)
        row = conn.execute("SELECT value FROM metadata WHERE key='version'").fetchone()
        assert row[0] == SCHEMA_VERSION

    def test_returns_added_columns_on_first_run(self, conn: sqlite3.Connection) -> None:
        """First-run schema apply: no migrations needed (columns already present)."""
        added = ensure_schema(conn)
        assert added == []


class TestMigrateColumns:
    """Pre-Sprint-025 DBs are upgraded in place by ``migrate_columns``."""

    def test_adds_backend_and_session_type_columns_to_legacy_table(self, conn: sqlite3.Connection) -> None:
        """A DB with the old minimal schema gets all post-025 columns added."""
        # Build the legacy worktree_status shape (no backend_*, no session_type).
        conn.executescript(
            """
            CREATE TABLE worktree_status (
                worktree_name TEXT PRIMARY KEY,
                worktree_path TEXT NOT NULL,
                branch TEXT NOT NULL,
                tmux_session TEXT,
                ai_tool TEXT DEFAULT 'claude',
                activity_status TEXT DEFAULT 'idle',
                current_task TEXT,
                last_task_update TEXT,
                notes TEXT,
                modified_files TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # Seed a legacy row to confirm migration preserves data.
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO worktree_status (worktree_name, worktree_path, branch, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("legacy", "/tmp/legacy", "feature/legacy", now, now),
        )

        added = migrate_columns(conn)
        conn.commit()

        assert set(added) == {"backend_kind", "backend_session_id", "backend_meta", "session_type"}
        cols = {row[1] for row in conn.execute("PRAGMA table_info(worktree_status)").fetchall()}
        assert {"backend_kind", "backend_session_id", "backend_meta", "session_type"}.issubset(cols)

        # Pre-existing row survives and gets defaults from new columns.
        row = conn.execute("SELECT worktree_name, backend_kind, session_type FROM worktree_status").fetchone()
        assert row["worktree_name"] == "legacy"
        assert row["backend_kind"] == "tmux"
        assert row["session_type"] == "worktree"


class TestRowSerialization:
    """``upsert_status_row``/``row_to_status`` are inverse operations."""

    def test_roundtrip_preserves_fields(self, conn: sqlite3.Connection) -> None:
        ensure_schema(conn)
        original = _sample_status("roundtrip")
        upsert_status_row(conn, original)
        row = conn.execute("SELECT * FROM worktree_status WHERE worktree_name=?", ("roundtrip",)).fetchone()
        recovered = row_to_status(row)
        assert recovered.worktree_name == original.worktree_name
        assert recovered.modified_files == original.modified_files
        assert recovered.backend_meta == original.backend_meta
        assert recovered.activity_status == AIActivityStatus.WORKING


class TestDateTimeHelpers:
    """``dt_to_str``/``str_to_dt`` round-trip via ISO-8601."""

    def test_none_passes_through(self) -> None:
        assert dt_to_str(None) is None
        assert str_to_dt(None) is None

    def test_roundtrip(self) -> None:
        dt = datetime(2026, 5, 24, 9, 30, 0)
        assert str_to_dt(dt_to_str(dt)) == dt


class TestMigrateLegacyJson:
    """Legacy ``ai_status.json`` import path."""

    def test_no_legacy_file_returns_false(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        ensure_schema(conn)
        result = migrate_legacy_json(conn, tmp_path / "status.db")
        assert result is False

    def test_imports_legacy_rows_and_renames_file(self, tmp_path: Path) -> None:
        storage = tmp_path / "status.db"
        legacy = tmp_path / "ai_status.json"
        now = datetime.now().isoformat()
        legacy.write_text(
            json.dumps(
                {
                    "statuses": {
                        "old-wt": {
                            "worktree_name": "old-wt",
                            "worktree_path": "/tmp/old-wt",
                            "branch": "feature/old",
                            "ai_tool": "claude",
                            "activity_status": "idle",
                            "modified_files": [],
                            "created_at": now,
                            "updated_at": now,
                        }
                    },
                    "shared_notes": ["hello"],
                }
            )
        )

        conn = open_db(storage)
        ensure_schema(conn)
        assert migrate_legacy_json(conn, storage) is True

        rows = conn.execute("SELECT worktree_name FROM worktree_status").fetchall()
        assert [r[0] for r in rows] == ["old-wt"]
        notes = conn.execute("SELECT note FROM shared_notes").fetchall()
        assert [n[0] for n in notes] == ["hello"]
        # Backup file replaces the original.
        assert not legacy.exists()
        assert (tmp_path / "ai_status.json.bak").exists()


class TestUsageEvents:
    """Local usage signal (the project's kill-switch gauge)."""

    def test_record_and_count(self, conn: sqlite3.Connection) -> None:
        ensure_schema(conn)
        record_usage(conn, "control_plane")
        record_usage(conn, "new")
        record_usage(conn, "new")
        counts = usage_counts(conn, days=30)
        assert counts == {"control_plane": 1, "new": 2}

    def test_empty_when_no_events(self, conn: sqlite3.Connection) -> None:
        ensure_schema(conn)
        assert usage_counts(conn, days=30) == {}

    def test_window_excludes_old_events(self, conn: sqlite3.Connection) -> None:
        ensure_schema(conn)
        old = (datetime.now() - timedelta(days=40)).isoformat()
        conn.execute("INSERT INTO usage_events (event, created_at) VALUES (?, ?)", ("new", old))
        conn.commit()
        record_usage(conn, "new")  # within window
        assert usage_counts(conn, days=30) == {"new": 1}


class TestPublicSurface:
    """Schema module is importable through the canonical path."""

    def test_module_exports(self) -> None:
        for name in ("SCHEMA_VERSION", "SCHEMA_SQL", "PEER_MESSAGES_SCHEMA", "ensure_schema"):
            assert hasattr(status_schema, name)

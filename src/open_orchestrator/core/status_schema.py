"""SQLite schema definitions and migrations for the status DB.

This module is the single source of truth for the on-disk shape of the
status database used by :mod:`open_orchestrator.core.status`. Extracted from
``core/status.py`` during Sprint 027 Phase 8 so the tracker module stays
focused on runtime behavior.

The schema covers four tables:

* ``worktree_status`` — one row per tracked worktree / branch session.
* ``shared_notes`` — operator-broadcast notes.
* ``metadata`` — key/value bag (schema version, etc).
* ``peer_messages`` — MCP peer messaging inbox.

Migrations are kept as small ``ALTER TABLE`` steps in :func:`migrate_columns`
because the columns have been added incrementally across sprints. New
columns must be appended there (not edited into ``_SCHEMA_SQL`` blindly) so
pre-existing DBs upgrade in place.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from open_orchestrator.core._db import open_db

if TYPE_CHECKING:
    from open_orchestrator.models.status import WorktreeAIStatus

logger = logging.getLogger(__name__)

DEFAULT_STATUS_FILENAME = "status.db"
STATUS_DB_ENV_VAR = "OWT_DB_PATH"

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_SQL",
    "PEER_MESSAGES_SCHEMA",
    "DEFAULT_STATUS_FILENAME",
    "STATUS_DB_ENV_VAR",
    "StatusConfig",
    "SQLiteStatusRepository",
    "apply_schema",
    "migrate_columns",
    "ensure_schema",
    "record_schema_version",
    "dt_to_str",
    "str_to_dt",
    "row_to_status",
    "upsert_status_row",
    "insert_shared_note",
    "record_usage",
    "usage_counts",
    "load_legacy_json",
    "backup_legacy_json",
    "migrate_legacy_json",
    "default_status_path",
    "runtime_status_config",
]

SCHEMA_VERSION = "3.3"

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS worktree_status (
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
    backend_kind TEXT DEFAULT 'tmux',
    backend_session_id TEXT,
    backend_meta TEXT DEFAULT '{}',
    session_type TEXT DEFAULT 'worktree',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS peer_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_peer TEXT NOT NULL,
    to_peer TEXT NOT NULL,
    message TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_peer_messages_to_peer_read
    ON peer_messages(to_peer, read);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_events_created_at
    ON usage_events(created_at);
"""

# Exported for ``mcp_peer.py`` to avoid duplicate schema definitions when a
# peer process opens the DB before the StatusTracker has run its migrations.
PEER_MESSAGES_SCHEMA = """\
CREATE TABLE IF NOT EXISTS peer_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_peer TEXT NOT NULL,
    to_peer TEXT NOT NULL,
    message TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_peer_messages_to_peer_read
    ON peer_messages(to_peer, read);
"""

# Columns introduced after the initial schema cut. Each entry is the column
# name plus the ``ALTER TABLE`` clause used to add it to an existing DB.
# Order matters only for readability — each column is independently checked
# against ``PRAGMA table_info`` before being added.
_WORKTREE_STATUS_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("backend_kind", "ALTER TABLE worktree_status ADD COLUMN backend_kind TEXT DEFAULT 'tmux'"),
    ("backend_session_id", "ALTER TABLE worktree_status ADD COLUMN backend_session_id TEXT"),
    ("backend_meta", "ALTER TABLE worktree_status ADD COLUMN backend_meta TEXT DEFAULT '{}'"),
    ("session_type", "ALTER TABLE worktree_status ADD COLUMN session_type TEXT DEFAULT 'worktree'"),
)


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create base tables/indices if they don't exist.

    Idempotent — every ``CREATE`` uses ``IF NOT EXISTS`` so calling this
    against an already-initialized DB is a no-op.
    """
    conn.executescript(SCHEMA_SQL)


def migrate_columns(conn: sqlite3.Connection) -> list[str]:
    """Add columns introduced in later sprints to pre-existing DBs.

    Sprint 025 P7 added the ``backend_*`` columns; Sprint 026 P1 added
    ``session_type`` so doctor can tell worktree-mode and branch-mode rows
    apart without crossing the git layer. Returns the list of column names
    that were added during this call (empty when nothing changed) so callers
    can log meaningful migration activity.
    """
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(worktree_status)").fetchall()}
    added: list[str] = []
    for column, ddl in _WORKTREE_STATUS_MIGRATIONS:
        if column not in existing_cols:
            conn.execute(ddl)
            added.append(column)
    return added


def record_schema_version(conn: sqlite3.Connection, version: str = SCHEMA_VERSION) -> None:
    """Persist the current schema version into the ``metadata`` table."""
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('version', ?)",
        (version,),
    )


def dt_to_str(dt: datetime | None) -> str | None:
    """Serialize a :class:`datetime` for SQLite storage."""
    if dt is None:
        return None
    return dt.isoformat()


def str_to_dt(s: str | None) -> datetime | None:
    """Inverse of :func:`dt_to_str`."""
    if s is None:
        return None
    return datetime.fromisoformat(s)


def row_to_status(row: sqlite3.Row) -> WorktreeAIStatus:
    """Map a ``worktree_status`` :class:`sqlite3.Row` to a domain dataclass.

    ``sqlite3.Row.keys()`` is the safe portable way to introspect columns;
    older DBs created before Sprint 025 P7 may not have ``backend_*`` columns.
    Keeping this mapping next to the schema means a new column added in
    :data:`SCHEMA_SQL` is paired with its read path in one file.
    """
    from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

    columns = set(row.keys())
    return WorktreeAIStatus(
        worktree_name=row["worktree_name"],
        worktree_path=row["worktree_path"],
        branch=row["branch"],
        tmux_session=row["tmux_session"],
        ai_tool=row["ai_tool"] or "claude",
        activity_status=AIActivityStatus(row["activity_status"] or "unknown"),
        current_task=row["current_task"],
        last_task_update=str_to_dt(row["last_task_update"]),
        notes=row["notes"],
        modified_files=json.loads(row["modified_files"] or "[]"),
        backend_kind=(row["backend_kind"] if "backend_kind" in columns else None) or "tmux",
        backend_session_id=row["backend_session_id"] if "backend_session_id" in columns else None,
        backend_meta=json.loads((row["backend_meta"] if "backend_meta" in columns else None) or "{}"),
        session_type=(row["session_type"] if "session_type" in columns else None) or "worktree",
        created_at=str_to_dt(row["created_at"]) or datetime.now(),
        updated_at=str_to_dt(row["updated_at"]) or datetime.now(),
    )


def upsert_status_row(conn: sqlite3.Connection, s: WorktreeAIStatus) -> None:
    """Persist a :class:`WorktreeAIStatus` to the ``worktree_status`` table.

    Co-located with :data:`SCHEMA_SQL` so column order changes only need to
    touch one file. Commits on success.
    """
    conn.execute(
        """INSERT OR REPLACE INTO worktree_status
           (worktree_name, worktree_path, branch, tmux_session, ai_tool,
            activity_status, current_task, last_task_update, notes,
            modified_files, backend_kind, backend_session_id, backend_meta,
            session_type, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            s.worktree_name,
            s.worktree_path,
            s.branch,
            s.tmux_session,
            s.ai_tool,
            s.activity_status.value,
            s.current_task,
            dt_to_str(s.last_task_update),
            s.notes,
            json.dumps(s.modified_files),
            s.backend_kind,
            s.backend_session_id,
            json.dumps(s.backend_meta),
            s.session_type,
            dt_to_str(s.created_at),
            dt_to_str(s.updated_at),
        ),
    )
    conn.commit()


def insert_shared_note(conn: sqlite3.Connection, note: str) -> None:
    """Append a shared operator note. Commits on success."""
    conn.execute(
        "INSERT INTO shared_notes (note, created_at) VALUES (?, ?)",
        (note, datetime.now().isoformat()),
    )
    conn.commit()


def record_usage(conn: sqlite3.Connection, event: str) -> None:
    """Append a local usage event (e.g. ``control_plane`` / ``new``).

    Local-only — never leaves the machine. Used as a low-effort signal of
    whether the cockpit is actually being used (the project's kill-switch
    criterion). Commits on success.
    """
    conn.execute(
        "INSERT INTO usage_events (event, created_at) VALUES (?, ?)",
        (event, datetime.now().isoformat()),
    )
    conn.commit()


def usage_counts(conn: sqlite3.Connection, *, days: int = 30) -> dict[str, int]:
    """Return per-event counts over the last ``days`` days (newest window)."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT event, COUNT(*) FROM usage_events WHERE created_at >= ? GROUP BY event",
        (since,),
    ).fetchall()
    return {str(event): int(count) for event, count in rows}


def load_legacy_json(storage_path: Path) -> tuple[Path | None, dict[str, object] | None]:
    """Load legacy ``ai_status.json`` next to the SQLite DB, if any."""
    from open_orchestrator.utils.io import safe_read_json

    json_path = storage_path.parent / "ai_status.json"
    if not json_path.exists():
        return None, None
    try:
        data = safe_read_json(json_path)
        return json_path, data
    except (OSError, ValueError) as e:
        logger.warning("Failed to migrate %s: %s", json_path, e)
        return None, None


def backup_legacy_json(json_path: Path) -> None:
    """Rename a migrated legacy JSON status file to a ``.bak`` sibling."""
    bak_path = json_path.with_suffix(".json.bak")
    json_path.rename(bak_path)
    logger.info("Migrated %s -> SQLite, backup at %s", json_path, bak_path)


def migrate_legacy_json(conn: sqlite3.Connection, storage_path: Path) -> bool:
    """Import data from a legacy ``ai_status.json`` next to ``storage_path``.

    Returns ``True`` when a legacy file was found and migration completed,
    ``False`` when there was nothing to migrate. Errors during migration
    are logged at WARNING level (not raised) so the tracker can keep
    booting even if the legacy file is corrupt.
    """
    from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

    json_path, data = load_legacy_json(storage_path)
    if not json_path or data is None:
        return False
    try:
        for name, s in data.get("statuses", {}).items():  # type: ignore[attr-defined]
            status = WorktreeAIStatus(
                worktree_name=s.get("worktree_name", name),
                worktree_path=s.get("worktree_path", ""),
                branch=s.get("branch", ""),
                tmux_session=s.get("tmux_session"),
                ai_tool=s.get("ai_tool", "claude"),
                activity_status=AIActivityStatus(s.get("activity_status", "unknown")),
                current_task=s.get("current_task"),
                last_task_update=str_to_dt(s.get("last_task_update")),
                notes=s.get("notes"),
                modified_files=s.get("modified_files", []),
                created_at=str_to_dt(s.get("created_at")) or datetime.now(),
                updated_at=str_to_dt(s.get("updated_at")) or datetime.now(),
            )
            upsert_status_row(conn, status)
        for note in data.get("shared_notes", []):  # type: ignore[attr-defined]
            insert_shared_note(conn, note)
        backup_legacy_json(json_path)
        return True
    except (OSError, ValueError) as e:
        logger.warning("Failed to migrate %s: %s", json_path, e)
        return False


def ensure_schema(conn: sqlite3.Connection, *, version: str = SCHEMA_VERSION) -> list[str]:
    """One-shot helper: apply schema, run migrations, stamp version, commit.

    Returns the list of columns added by :func:`migrate_columns` so callers
    can surface meaningful upgrade telemetry. Commits on success.
    """
    apply_schema(conn)
    added = migrate_columns(conn)
    record_schema_version(conn, version)
    conn.commit()
    return added


# ---------------------------------------------------------------------------
# Storage path resolution
#
# Path resolution is co-located with schema because both answer "how do we
# bring a status DB online?". The StatusTracker (runtime concern) layers on
# top of these primitives.
# ---------------------------------------------------------------------------


@dataclass
class StatusConfig:
    """Configuration for status tracking."""

    storage_path: Path | None = None


def default_status_path() -> Path:
    """Resolve the default status DB path.

    ``OWT_DB_PATH`` takes precedence so hook-driven subprocesses, MCP peers,
    and in-process callers can be pointed at the same DB explicitly.
    """
    env_path = os.environ.get(STATUS_DB_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".open-orchestrator" / DEFAULT_STATUS_FILENAME


def _is_writable_sqlite_target(path: Path) -> bool:
    """Check whether a SQLite file target appears writable."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    if path.exists():
        return os.access(path, os.W_OK)
    return os.access(path.parent, os.W_OK)


def _temp_status_path(repo_name: str | None = None) -> Path:
    """Create a user-scoped temp location for fallback status storage."""
    user_tmp = Path(tempfile.gettempdir()) / f"owt-{os.getuid()}"
    user_tmp.mkdir(mode=0o700, exist_ok=True)
    if repo_name:
        return user_tmp / repo_name / DEFAULT_STATUS_FILENAME
    return user_tmp / DEFAULT_STATUS_FILENAME


def _resolve_repo_root(repo_path: str | Path | None = None) -> Path | None:
    """Resolve the common git root for a repo or worktree path."""
    from open_orchestrator.core.worktree import WorktreeManager

    candidate = Path(repo_path) if repo_path is not None else Path.cwd()
    try:
        return WorktreeManager(candidate).git_root
    except Exception:
        logger.debug("Could not resolve repo root from %s", candidate, exc_info=True)
        return None


def runtime_status_config(repo_path: str | Path | None = None) -> StatusConfig:
    """Build a status config suitable for orchestrator/batch runtime use.

    Production flows keep using the shared default DB path so hooks, MCP,
    and other CLI surfaces stay in sync. When the shared home-directory DB
    is unavailable, repo-bound commands fall back to a repo-local DB rooted
    at the common git dir so worktrees, hooks, switchboard, and orchestration
    keep reading the same state.
    """
    shared_path = default_status_path()
    if _is_writable_sqlite_target(shared_path):
        return StatusConfig(storage_path=shared_path)

    repo_root = _resolve_repo_root(repo_path)
    if repo_root is not None:
        repo_local = repo_root / ".open-orchestrator" / DEFAULT_STATUS_FILENAME
        if _is_writable_sqlite_target(repo_local):
            return StatusConfig(storage_path=repo_local)
        return StatusConfig(storage_path=_temp_status_path(repo_root.name))

    if repo_path is None:
        return StatusConfig(storage_path=_temp_status_path())

    repo = Path(repo_path)
    safe_name = repo.name or "repo"
    return StatusConfig(storage_path=_temp_status_path(safe_name))


# ---------------------------------------------------------------------------
# Repository (owns the connection + schema bootstrap)
# ---------------------------------------------------------------------------


class SQLiteStatusRepository:
    """SQLite-backed persistence for status tracking.

    Owns the long-lived :class:`sqlite3.Connection`, applies the schema +
    migrations, and exposes legacy-JSON migration entry points. Tracker
    behavior (queries, upserts, summaries) lives in
    :class:`open_orchestrator.core.status.StatusTracker`.
    """

    def __init__(self, config: StatusConfig | None = None):
        self.config = config or StatusConfig()
        self.storage_path = self.config.storage_path or default_status_path()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = open_db(self.storage_path)
        self._ensure_schema()
        with contextlib.suppress(PermissionError, OSError):
            os.chmod(self.storage_path, 0o600)

    def _ensure_schema(self) -> None:
        """Create tables if they don't exist and migrate columns added later."""
        ensure_schema(self.conn)

    def _migrate_columns(self) -> list[str]:
        """Run incremental column migrations against the underlying DB."""
        added = migrate_columns(self.conn)
        self.conn.commit()
        return added

    def load_legacy_json(self) -> tuple[Path | None, dict[str, object] | None]:
        """Load legacy ``ai_status.json`` if present."""
        return load_legacy_json(self.storage_path)

    def backup_legacy_json(self, json_path: Path) -> None:
        """Rename a migrated legacy JSON status file to a backup."""
        backup_legacy_json(json_path)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.conn.close()

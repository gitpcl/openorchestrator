"""
Status tracking service for worktree AI tool sessions.

SQLite backend — replaces the previous JSON + file-locking approach.
WAL mode allows concurrent reads/writes from the switchboard and hooks.
"""

import json
import logging
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from open_orchestrator.config import AITool
from open_orchestrator.models.status import (
    AIActivityStatus,
    StatusSummary,
    WorktreeAIStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_STATUS_FILENAME = "status.db"
STATUS_DB_ENV_VAR = "OWT_DB_PATH"

_SCHEMA_SQL = """\
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
"""


def _dt_to_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _str_to_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _row_to_status(row: sqlite3.Row) -> WorktreeAIStatus:
    return WorktreeAIStatus(
        worktree_name=row["worktree_name"],
        worktree_path=row["worktree_path"],
        branch=row["branch"],
        tmux_session=row["tmux_session"],
        ai_tool=row["ai_tool"] or "claude",
        activity_status=AIActivityStatus(row["activity_status"] or "unknown"),
        current_task=row["current_task"],
        last_task_update=_str_to_dt(row["last_task_update"]),
        notes=row["notes"],
        modified_files=json.loads(row["modified_files"] or "[]"),
        created_at=_str_to_dt(row["created_at"]) or datetime.now(),
        updated_at=_str_to_dt(row["updated_at"]) or datetime.now(),
    )


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


def runtime_status_config(repo_path: str | Path | None = None) -> StatusConfig:
    """Build a status config suitable for orchestrator/batch runtime use.

    Production flows keep using the shared default DB path so hooks, MCP,
    and other CLI surfaces stay in sync. Test-only or synthetic repo paths
    that do not exist get a temp-backed DB instead of failing on a global
    home-directory write.
    """
    shared_path = default_status_path()
    if _is_writable_sqlite_target(shared_path):
        return StatusConfig(storage_path=shared_path)

    if repo_path is None:
        return StatusConfig(storage_path=_temp_status_path())

    repo = Path(repo_path)
    if repo.exists():
        repo_local = repo / ".open-orchestrator" / DEFAULT_STATUS_FILENAME
        if _is_writable_sqlite_target(repo_local):
            return StatusConfig(storage_path=repo_local)
        return StatusConfig(storage_path=_temp_status_path(repo.name))

    safe_name = repo.name or "repo"
    return StatusConfig(storage_path=_temp_status_path(safe_name))


class SQLiteStatusRepository:
    """SQLite-backed persistence for status tracking."""

    def __init__(self, config: StatusConfig | None = None):
        self.config = config or StatusConfig()
        self.storage_path = self.config.storage_path or default_status_path()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(self.storage_path), isolation_level="DEFERRED"
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()
        try:
            os.chmod(self.storage_path, 0o600)
        except (PermissionError, OSError):
            pass

    def _ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR IGNORE INTO metadata (key, value) VALUES ('version', '3.0')"
        )
        self.conn.commit()

    def load_legacy_json(self) -> tuple[Path | None, dict[str, object] | None]:
        """Load legacy ai_status.json if present."""
        from open_orchestrator.utils.io import safe_read_json

        json_path = self.storage_path.parent / "ai_status.json"
        if not json_path.exists():
            return None, None
        try:
            data = safe_read_json(json_path)
            return json_path, data
        except (OSError, ValueError) as e:
            logger.warning("Failed to migrate %s: %s", json_path, e)
            return None, None

    def backup_legacy_json(self, json_path: Path) -> None:
        """Rename a migrated legacy JSON status file to a backup."""
        bak_path = json_path.with_suffix(".json.bak")
        json_path.rename(bak_path)
        logger.info("Migrated %s → SQLite, backup at %s", json_path, bak_path)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.conn.close()


class StatusTracker:
    """
    Tracks and persists AI tool activity status for worktrees.

    Uses SQLite with WAL mode for concurrent access from the switchboard
    UI and hook-driven writes from multiple agents.
    """

    DEFAULT_STATUS_FILENAME = DEFAULT_STATUS_FILENAME

    def __init__(
        self,
        config: StatusConfig | None = None,
        repository: SQLiteStatusRepository | None = None,
    ):
        self.config = config or runtime_status_config()
        self._repository = repository or SQLiteStatusRepository(self.config)
        self._storage_path = self._repository.storage_path
        self._conn = self._repository.conn
        self._migrate_json()

    def _get_default_path(self) -> Path:
        """Get default path for status storage in user's home directory."""
        return default_status_path()

    def _ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        self._repository._ensure_schema()

    def _migrate_json(self) -> None:
        """Import data from legacy ai_status.json if it exists."""
        json_path, data = self._repository.load_legacy_json()
        if not json_path or data is None:
            return
        try:
            for name, s in data.get("statuses", {}).items():
                status = WorktreeAIStatus(
                    worktree_name=s.get("worktree_name", name),
                    worktree_path=s.get("worktree_path", ""),
                    branch=s.get("branch", ""),
                    tmux_session=s.get("tmux_session"),
                    ai_tool=s.get("ai_tool", "claude"),
                    activity_status=AIActivityStatus(s.get("activity_status", "unknown")),
                    current_task=s.get("current_task"),
                    last_task_update=_str_to_dt(s.get("last_task_update")),
                    notes=s.get("notes"),
                    modified_files=s.get("modified_files", []),
                    created_at=_str_to_dt(s.get("created_at")) or datetime.now(),
                    updated_at=_str_to_dt(s.get("updated_at")) or datetime.now(),
                )
                self._upsert_status(status)
            for note in data.get("shared_notes", []):
                self.add_shared_note(note)
            self._repository.backup_legacy_json(json_path)
        except (OSError, ValueError) as e:
            logger.warning("Failed to migrate %s: %s", json_path, e)

    def close(self) -> None:
        """Close the database connection."""
        self._repository.close()

    @property
    def storage_path(self) -> Path:
        """Return the underlying SQLite storage path."""
        return self._storage_path

    def reload(self) -> None:
        """No-op: SQLite reads are always fresh."""

    def get_status(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Get status for a specific worktree."""
        row = self._conn.execute(
            "SELECT * FROM worktree_status WHERE worktree_name = ?",
            (worktree_name,),
        ).fetchone()
        return _row_to_status(row) if row else None

    def get_all_statuses(self) -> list[WorktreeAIStatus]:
        """Get statuses for all tracked worktrees."""
        rows = self._conn.execute("SELECT * FROM worktree_status").fetchall()
        return [_row_to_status(r) for r in rows]

    def _upsert_status(self, s: WorktreeAIStatus) -> None:
        """Insert or replace a status row."""
        self._conn.execute(
            """INSERT OR REPLACE INTO worktree_status
               (worktree_name, worktree_path, branch, tmux_session, ai_tool,
                activity_status, current_task, last_task_update, notes,
                modified_files, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                s.worktree_name,
                s.worktree_path,
                s.branch,
                s.tmux_session,
                s.ai_tool,
                s.activity_status.value,
                s.current_task,
                _dt_to_str(s.last_task_update),
                s.notes,
                json.dumps(s.modified_files),
                _dt_to_str(s.created_at),
                _dt_to_str(s.updated_at),
            ),
        )
        self._conn.commit()

    def set_status(self, status: WorktreeAIStatus) -> None:
        """Public API to persist a WorktreeAIStatus update."""
        self._upsert_status(status)

    def initialize_status(
        self,
        worktree_name: str,
        worktree_path: str,
        branch: str,
        tmux_session: str | None = None,
        ai_tool: AITool | str = AITool.CLAUDE,
    ) -> WorktreeAIStatus:
        """Initialize status tracking for a new worktree."""
        ai_tool_str = ai_tool.value if isinstance(ai_tool, AITool) else ai_tool

        status = WorktreeAIStatus(
            worktree_name=worktree_name,
            worktree_path=worktree_path,
            branch=branch,
            tmux_session=tmux_session,
            ai_tool=ai_tool_str,
            activity_status=AIActivityStatus.IDLE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self._upsert_status(status)
        return status

    def update_task(
        self, worktree_name: str, task: str, status: AIActivityStatus = AIActivityStatus.WORKING
    ) -> WorktreeAIStatus | None:
        """Update the current task for a worktree."""
        wt_status = self.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.update_task(task, status)
        self._upsert_status(wt_status)
        return wt_status

    def record_command(
        self, target_worktree: str, command: str, source_worktree: str | None = None, pane_index: int = 0, window_index: int = 0
    ) -> WorktreeAIStatus | None:
        """Record a command sent to a worktree and mark it as working."""
        wt_status = self.get_status(target_worktree)
        if not wt_status:
            return None

        if wt_status.activity_status in (
            AIActivityStatus.IDLE,
            AIActivityStatus.WAITING,
            AIActivityStatus.BLOCKED,
        ):
            wt_status.activity_status = AIActivityStatus.WORKING

        wt_status.updated_at = datetime.now()
        self._upsert_status(wt_status)
        return wt_status

    def mark_completed(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Mark a worktree's current task as completed."""
        wt_status = self.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.mark_completed()
        self._upsert_status(wt_status)
        return wt_status

    def mark_idle(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Mark a worktree as idle."""
        wt_status = self.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.mark_idle()
        self._upsert_status(wt_status)
        return wt_status

    def set_notes(self, worktree_name: str, notes: str) -> WorktreeAIStatus | None:
        """Set notes for a worktree."""
        wt_status = self.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.notes = notes
        wt_status.updated_at = datetime.now()
        self._upsert_status(wt_status)
        return wt_status

    def remove_status(self, worktree_name: str) -> bool:
        """Remove status tracking for a worktree."""
        cursor = self._conn.execute(
            "DELETE FROM worktree_status WHERE worktree_name = ?",
            (worktree_name,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_shared_notes(self) -> list[str]:
        """Get all shared notes."""
        rows = self._conn.execute(
            "SELECT note FROM shared_notes ORDER BY id"
        ).fetchall()
        return [r["note"] for r in rows]

    def add_shared_note(self, note: str) -> None:
        """Add a shared note."""
        self._conn.execute(
            "INSERT INTO shared_notes (note, created_at) VALUES (?, ?)",
            (note, datetime.now().isoformat()),
        )
        self._conn.commit()

    def clear_shared_notes(self) -> None:
        """Clear all shared notes."""
        self._conn.execute("DELETE FROM shared_notes")
        self._conn.commit()

    def get_summary(self, worktree_names: list[str] | None = None) -> StatusSummary:
        """Generate a summary of AI tool status across worktrees."""
        all_statuses = self.get_all_statuses()

        if worktree_names:
            all_statuses = [s for s in all_statuses if s.worktree_name in worktree_names]

        summary = StatusSummary(
            timestamp=datetime.now(),
            total_worktrees=len(worktree_names) if worktree_names else len(all_statuses),
            worktrees_with_status=len(all_statuses),
            statuses=all_statuses,
        )

        for status in all_statuses:
            if status.activity_status == AIActivityStatus.WORKING:
                summary.active_ai_sessions += 1
            elif status.activity_status in (AIActivityStatus.IDLE, AIActivityStatus.WAITING, AIActivityStatus.COMPLETED):
                summary.idle_ai_sessions += 1
            elif status.activity_status in (AIActivityStatus.BLOCKED, AIActivityStatus.ERROR):
                summary.blocked_ai_sessions += 1
            else:
                summary.unknown_status += 1

            if status.updated_at:
                if summary.most_recent_activity is None or status.updated_at > summary.most_recent_activity:
                    summary.most_recent_activity = status.updated_at

        return summary

    def cleanup_orphans(self, valid_worktree_names: list[str]) -> list[str]:
        """Remove status entries for worktrees that no longer exist.

        For each orphaned entry, also kills the associated tmux session if one
        is still running, keeping all three systems (git, tmux, SQLite) in sync.
        """
        all_statuses = self.get_all_statuses()
        orphans = [s for s in all_statuses if s.worktree_name not in valid_worktree_names]
        if not orphans:
            return []

        from open_orchestrator.core.tmux_manager import TmuxManager

        tmux = TmuxManager()
        removed = []
        for s in orphans:
            # Kill the tmux session before removing the status entry
            session_name = s.tmux_session or tmux.generate_session_name(s.worktree_name)
            try:
                tmux.kill_session(session_name)
                logger.debug("Killed orphan tmux session %s", session_name)
            except Exception as e:
                logger.debug("Could not kill tmux session %s: %s", session_name, e)

            self.remove_status(s.worktree_name)
            removed.append(s.worktree_name)
        return removed

    def get_metadata(self, key: str) -> str | None:
        """Retrieve a metadata value by key."""
        row = self._conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        """Store a metadata key-value pair."""
        self._conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def delete_metadata(self, key: str) -> None:
        """Delete a metadata entry by key."""
        self._conn.execute("DELETE FROM metadata WHERE key = ?", (key,))
        self._conn.commit()

    def get_current_worktree_name(self) -> str | None:
        """Get the worktree name for the current directory."""
        current = Path.cwd()

        for status in self.get_all_statuses():
            try:
                current.relative_to(status.worktree_path)
                return status.worktree_name
            except ValueError:
                continue

        return None

    # -- Peer messaging --------------------------------------------------

    def store_message(self, from_peer: str, to_peer: str, message: str) -> int:
        """Store a peer message. Returns the message ID."""
        cursor = self._conn.execute(
            "INSERT INTO peer_messages (from_peer, to_peer, message, created_at) "
            "VALUES (?, ?, ?, ?)",
            (from_peer, to_peer, message, datetime.now().isoformat()),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_unread_messages(self, worktree_name: str) -> list[dict[str, str | int]]:
        """Get unread peer messages for a worktree."""
        rows = self._conn.execute(
            "SELECT id, from_peer, message, created_at FROM peer_messages "
            "WHERE to_peer = ? AND read = 0 ORDER BY created_at",
            (worktree_name,),
        ).fetchall()
        return [
            {"id": r[0], "from_peer": r[1], "message": r[2], "created_at": r[3]}
            for r in rows
        ]

    def mark_messages_read(self, message_ids: list[int]) -> None:
        """Mark peer messages as read."""
        if not message_ids:
            return
        placeholders = ",".join("?" * len(message_ids))
        self._conn.execute(
            f"UPDATE peer_messages SET read = 1 WHERE id IN ({placeholders})",  # noqa: S608
            message_ids,
        )
        self._conn.commit()

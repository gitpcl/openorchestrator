"""
Status tracking service for worktree AI tool sessions.

SQLite backend — replaces the previous JSON + file-locking approach.
WAL mode allows concurrent reads/writes from the switchboard and hooks.
"""

import json
import logging
import os
import sqlite3
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

    def __post_init__(self) -> None:
        pass


class StatusTracker:
    """
    Tracks and persists AI tool activity status for worktrees.

    Uses SQLite with WAL mode for concurrent access from the switchboard
    UI and hook-driven writes from multiple agents.
    """

    DEFAULT_STATUS_FILENAME = "status.db"

    def __init__(self, config: StatusConfig | None = None):
        self.config = config or StatusConfig()
        self._storage_path = self.config.storage_path or self._get_default_path()
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._storage_path), isolation_level="DEFERRED"
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()
        self._migrate_json()
        # Set restrictive permissions on the DB file
        try:
            os.chmod(self._storage_path, 0o600)
        except (PermissionError, OSError):
            pass

    def _get_default_path(self) -> Path:
        """Get default path for status storage in user's home directory."""
        return Path.home() / ".open-orchestrator" / self.DEFAULT_STATUS_FILENAME

    def _ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.execute(
            "INSERT OR IGNORE INTO metadata (key, value) VALUES ('version', '3.0')"
        )
        self._conn.commit()

    def _migrate_json(self) -> None:
        """Import data from legacy ai_status.json if it exists."""
        from open_orchestrator.utils.io import safe_read_json

        json_path = self._storage_path.parent / "ai_status.json"
        if not json_path.exists():
            return
        try:
            data = safe_read_json(json_path)
            if data is None:
                return
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
            # Rename JSON to .bak
            bak_path = json_path.with_suffix(".json.bak")
            json_path.rename(bak_path)
            logger.info("Migrated %s → SQLite, backup at %s", json_path, bak_path)
        except (OSError, ValueError) as e:
            logger.warning("Failed to migrate %s: %s", json_path, e)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

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
            elif status.activity_status == AIActivityStatus.IDLE:
                summary.idle_ai_sessions += 1
            elif status.activity_status == AIActivityStatus.BLOCKED:
                summary.blocked_ai_sessions += 1
            else:
                summary.unknown_status += 1

            if status.updated_at:
                if summary.most_recent_activity is None or status.updated_at > summary.most_recent_activity:
                    summary.most_recent_activity = status.updated_at

        return summary

    def cleanup_orphans(self, valid_worktree_names: list[str]) -> list[str]:
        """Remove status entries for worktrees that no longer exist."""
        removed = []
        for s in self.get_all_statuses():
            if s.worktree_name not in valid_worktree_names:
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
        current_path = str(Path.cwd())

        for status in self.get_all_statuses():
            if current_path.startswith(status.worktree_path):
                return status.worktree_name

        return None

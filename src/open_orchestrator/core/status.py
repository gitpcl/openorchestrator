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
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from open_orchestrator.core import status_policy
from open_orchestrator.models.status import (
    AIActivityStatus,
    StatusSummary,
    WorktreeAIStatus,
)

if TYPE_CHECKING:
    from open_orchestrator.models.backend import BackendSession

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
"""

# Exported for mcp_peer.py to avoid duplicate schema definitions
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


def _dt_to_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _str_to_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _row_to_status(row: sqlite3.Row) -> WorktreeAIStatus:
    # sqlite3.Row.keys() is the safe portable way to introspect columns;
    # older DBs created before Sprint 025 P7 may not have backend_* columns.
    columns = set(row.keys())
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
        backend_kind=(row["backend_kind"] if "backend_kind" in columns else None) or "tmux",
        backend_session_id=row["backend_session_id"] if "backend_session_id" in columns else None,
        backend_meta=json.loads((row["backend_meta"] if "backend_meta" in columns else None) or "{}"),
        session_type=(row["session_type"] if "session_type" in columns else None) or "worktree",
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


class SQLiteStatusRepository:
    """SQLite-backed persistence for status tracking."""

    def __init__(self, config: StatusConfig | None = None):
        self.config = config or StatusConfig()
        self.storage_path = self.config.storage_path or default_status_path()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.storage_path), isolation_level="DEFERRED")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()
        try:
            os.chmod(self.storage_path, 0o600)
        except (PermissionError, OSError):
            pass

    def _ensure_schema(self) -> None:
        """Create tables if they don't exist and migrate columns added later."""
        self.conn.executescript(_SCHEMA_SQL)
        self._migrate_columns()
        self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('version', '3.2')")
        self.conn.commit()

    def _migrate_columns(self) -> None:
        """Add columns introduced in later sprints to pre-existing DBs.

        Sprint 025 P7 added the ``backend_*`` columns; Sprint 026 P1 adds
        ``session_type`` so doctor can tell worktree-mode and branch-mode
        rows apart without crossing the git layer.
        """
        existing_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(worktree_status)").fetchall()}
        if "backend_kind" not in existing_cols:
            self.conn.execute("ALTER TABLE worktree_status ADD COLUMN backend_kind TEXT DEFAULT 'tmux'")
        if "backend_session_id" not in existing_cols:
            self.conn.execute("ALTER TABLE worktree_status ADD COLUMN backend_session_id TEXT")
        if "backend_meta" not in existing_cols:
            self.conn.execute("ALTER TABLE worktree_status ADD COLUMN backend_meta TEXT DEFAULT '{}'")
        if "session_type" not in existing_cols:
            self.conn.execute("ALTER TABLE worktree_status ADD COLUMN session_type TEXT DEFAULT 'worktree'")

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
            for name, s in data.get("statuses", {}).items():  # type: ignore[attr-defined]
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
            for note in data.get("shared_notes", []):  # type: ignore[attr-defined]
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

    def get_generation(self) -> str:
        """Return a generation token for change detection.

        Combines MAX(updated_at) and COUNT(*) into a single token so both
        updates and row additions/deletions are detected. Cheap single query.
        """
        row = self._conn.execute("SELECT COALESCE(MAX(updated_at), '') || ':' || COUNT(*) as gen FROM worktree_status").fetchone()
        return row["gen"] or ":0"

    def has_changed_since(self, generation: str) -> bool:
        """Check if any status has changed since the given generation token."""
        return self.get_generation() != generation

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
                _dt_to_str(s.last_task_update),
                s.notes,
                json.dumps(s.modified_files),
                s.backend_kind,
                s.backend_session_id,
                json.dumps(s.backend_meta),
                s.session_type,
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
        ai_tool: str = "claude",
        *,
        backend_kind: str = "tmux",
        backend_session_id: str | None = None,
        backend_meta: dict[str, str] | None = None,
        session_type: str = "worktree",
    ) -> WorktreeAIStatus:
        """Initialize status tracking for a new worktree.

        ``backend_kind`` records which multiplexer hosts the session so
        ``owt attach``/``owt send``/``owt delete`` can pick the right
        backend later without a CLI flag.

        ``session_type`` records whether the session is a git worktree
        (default) or an in-place branch — Sprint 026 P1 added this so
        ``owt doctor`` can reconcile branch rows against the branch list
        instead of mis-flagging them as orphaned worktrees.
        """
        st = session_type if session_type in {"worktree", "branch"} else "worktree"
        status = WorktreeAIStatus(
            worktree_name=worktree_name,
            worktree_path=worktree_path,
            branch=branch,
            tmux_session=tmux_session,
            ai_tool=ai_tool,
            activity_status=AIActivityStatus.IDLE,
            backend_kind=backend_kind,
            backend_session_id=backend_session_id,
            backend_meta=backend_meta or {},
            session_type=st,  # type: ignore[arg-type]
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self._upsert_status(status)
        return status

    def get_backend_session(self, worktree_name: str) -> "BackendSession | None":
        """Reconstruct a :class:`BackendSession` from the DB row, if any.

        Returns ``None`` when no row exists or the row carries no backend
        session id (legacy rows without backend bookkeeping).
        """
        from open_orchestrator.models.backend import BackendKind, BackendSession

        wt_status = self.get_status(worktree_name)
        if wt_status is None:
            return None
        # Prefer explicit backend_session_id; fall back to tmux_session for
        # legacy rows written before Sprint 025 P7.
        session_id = wt_status.backend_session_id or wt_status.tmux_session
        if not session_id:
            return None
        try:
            kind = BackendKind(wt_status.backend_kind)
        except ValueError:
            kind = BackendKind.TMUX
        return BackendSession(
            kind=kind,
            id=session_id,
            worktree_name=wt_status.worktree_name,
            meta=dict(wt_status.backend_meta),
        )

    def update_task(
        self,
        worktree_name: str,
        task: str,
        status: AIActivityStatus = AIActivityStatus.WORKING,
        *,
        backend: object | None = None,
    ) -> WorktreeAIStatus | None:
        """Update the current task for a worktree.

        ``backend`` is an optional :class:`MultiplexerBackend` (Sprint 025).
        When provided, the new state is forwarded to its sidebar via
        ``backend.report_agent_state``. SQLite remains source of truth —
        backend forwarding is best-effort and non-fatal.
        """
        wt_status = self.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.update_task(task, status)
        self._upsert_status(wt_status)
        if backend is not None:
            self._forward_to_backend(backend, wt_status, status, task)
        return wt_status

    def _forward_to_backend(
        self,
        backend: object,
        wt_status: WorktreeAIStatus,
        status: AIActivityStatus,
        message: str,
    ) -> None:
        """Best-effort push to a backend's sidebar (Sprint 025)."""
        try:
            from open_orchestrator.models.backend import BackendSession  # local import to avoid cycle

            # Prefer the backend-native session id; fall back to legacy
            # tmux_session, then worktree_name as a last resort.
            session_id = wt_status.backend_session_id or wt_status.tmux_session or wt_status.worktree_name
            session = BackendSession(
                kind=getattr(backend, "kind"),
                id=session_id,
                worktree_name=wt_status.worktree_name,
                meta=dict(wt_status.backend_meta),
            )
            report = getattr(backend, "report_agent_state", None)
            if report is None:
                return
            report(session, status.value, message)
        except Exception as err:  # noqa: BLE001
            logger.debug("backend.report_agent_state forwarding failed: %s", err)

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
        rows = self._conn.execute("SELECT note FROM shared_notes ORDER BY id").fetchall()
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
            bucket = status_policy.summary_bucket(status.activity_status)
            if bucket == "active":
                summary.active_ai_sessions += 1
            elif bucket == "idle":
                summary.idle_ai_sessions += 1
            elif bucket == "blocked":
                summary.blocked_ai_sessions += 1
            else:
                summary.unknown_status += 1

            if status.updated_at:
                if summary.most_recent_activity is None or status.updated_at > summary.most_recent_activity:
                    summary.most_recent_activity = status.updated_at

        return summary

    def cleanup_orphans(self, valid_worktree_names: list[str]) -> list[str]:
        """Remove status entries for worktrees that no longer exist.

        For each orphaned entry, also kills the associated backend session
        (tmux or herdr, per the row's ``backend_kind``) so all three
        systems — git, multiplexer, SQLite — stay in sync.
        """
        all_statuses = self.get_all_statuses()
        orphans = [s for s in all_statuses if s.worktree_name not in valid_worktree_names]
        if not orphans:
            return []

        from open_orchestrator.core.backend_factory import select_backend_for_session
        from open_orchestrator.models.backend import BackendKind, BackendSession

        removed = []
        for s in orphans:
            try:
                kind = s.backend_kind if s.backend_kind in {BackendKind.TMUX.value, BackendKind.HERDR.value} else "tmux"
                session_id = s.backend_session_id or s.tmux_session
                if session_id:
                    session = BackendSession(
                        kind=BackendKind(kind),
                        id=session_id,
                        worktree_name=s.worktree_name,
                        meta=dict(s.backend_meta),
                    )
                    # Use the session-aware factory so a non-default herdr
                    # socket (recorded on the row) is honored on kill.
                    backend = select_backend_for_session(session)
                    backend.kill(session)
                    logger.debug("Killed orphan %s session %s", kind, session_id)
            except Exception as e:  # noqa: BLE001
                logger.debug("Could not kill backend session for %s: %s", s.worktree_name, e)

            self.remove_status(s.worktree_name)
            removed.append(s.worktree_name)
        return removed

    def get_metadata(self, key: str) -> str | None:
        """Retrieve a metadata value by key."""
        row = self._conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
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
            "INSERT INTO peer_messages (from_peer, to_peer, message, created_at) VALUES (?, ?, ?, ?)",
            (from_peer, to_peer, message, datetime.now().isoformat()),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_unread_messages(self, worktree_name: str) -> list[dict[str, str | int]]:
        """Get unread peer messages for a worktree."""
        rows = self._conn.execute(
            "SELECT id, from_peer, message, created_at FROM peer_messages WHERE to_peer = ? AND read = 0 ORDER BY created_at",
            (worktree_name,),
        ).fetchall()
        return [{"id": r[0], "from_peer": r[1], "message": r[2], "created_at": r[3]} for r in rows]

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

    # -- Database maintenance -----------------------------------------------

    def purge_old_messages(self, days: int = 30) -> int:
        """Delete peer messages older than the given number of days.

        Returns the number of deleted rows.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM peer_messages WHERE created_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("Purged %d peer message(s) older than %d days", deleted, days)
        return deleted

    def vacuum(self) -> None:
        """Run PRAGMA optimize and VACUUM to reclaim space."""
        self._conn.execute("PRAGMA optimize")
        self._conn.execute("VACUUM")
        logger.info("Database optimized and vacuumed: %s", self._storage_path)

    def health_check(self) -> dict[str, object]:
        """Return database health diagnostics (read-only)."""
        schema_version = self.get_metadata("version") or "unknown"
        worktree_count = self._conn.execute("SELECT COUNT(*) FROM worktree_status").fetchone()[0]
        peer_msg_count = self._conn.execute("SELECT COUNT(*) FROM peer_messages").fetchone()[0]
        unread_count = self._conn.execute("SELECT COUNT(*) FROM peer_messages WHERE read = 0").fetchone()[0]
        db_size = self._storage_path.stat().st_size if self._storage_path.exists() else 0
        wal_row = self._conn.execute("PRAGMA journal_mode").fetchone()
        wal_mode = wal_row[0] if wal_row else "unknown"
        return {
            "schema_version": schema_version,
            "worktree_count": worktree_count,
            "peer_message_count": peer_msg_count,
            "unread_message_count": unread_count,
            "db_size_bytes": db_size,
            "wal_mode": wal_mode,
        }

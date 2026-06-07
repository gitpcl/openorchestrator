"""
Status tracking service for worktree AI tool sessions.

SQLite backend — replaces the previous JSON + file-locking approach.
WAL mode allows concurrent reads/writes from the switchboard and hooks.

Schema, migrations, path resolution, and the persistence repository live
in :mod:`open_orchestrator.core.status_schema`; this module focuses on the
runtime :class:`StatusTracker` behavior.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from open_orchestrator.core import status_policy
from open_orchestrator.core import status_schema as _schema
from open_orchestrator.core.status_schema import (
    DEFAULT_STATUS_FILENAME,
    PEER_MESSAGES_SCHEMA,
    STATUS_DB_ENV_VAR,
    SQLiteStatusRepository,
    StatusConfig,
    default_status_path,
    runtime_status_config,
)
from open_orchestrator.models.status import (
    AIActivityStatus,
    StatusSummary,
    WorktreeAIStatus,
)

if TYPE_CHECKING:
    from open_orchestrator.models.backend import BackendSession

# Aliased re-exports kept as module-level bindings to avoid 5x ruff-split imports.
_insert_shared_note = _schema.insert_shared_note
_migrate_legacy_json = _schema.migrate_legacy_json
_row_to_status = _schema.row_to_status
_upsert_status_row = _schema.upsert_status_row
_record_usage = _schema.record_usage
_usage_counts = _schema.usage_counts

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_STATUS_FILENAME",
    "PEER_MESSAGES_SCHEMA",
    "STATUS_DB_ENV_VAR",
    "SQLiteStatusRepository",
    "StatusConfig",
    "StatusTracker",
    "default_status_path",
    "runtime_status_config",
]


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

    def _migrate_json(self) -> None:
        """Import data from legacy ai_status.json if it exists."""
        _migrate_legacy_json(self._conn, self._storage_path)

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
        """Insert or replace a status row (delegates to schema module)."""
        _upsert_status_row(self._conn, s)

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

        ``backend_kind`` records which multiplexer hosts the session.
        ``session_type`` distinguishes worktree-mode from in-place branch
        rows (Sprint 026 P1) so ``owt doctor`` reconciles them correctly.
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

        When ``backend`` is provided, the state is forwarded to its sidebar
        via ``backend.report_agent_state`` (best-effort, non-fatal).
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

            # Prefer backend-native session id, fall back to tmux/worktree name.
            session_id = wt_status.backend_session_id or wt_status.tmux_session or wt_status.worktree_name
            session = BackendSession(
                kind=backend.kind,  # type: ignore[attr-defined]
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

    def mark_stalled(self, worktree_name: str, reason: str | None = None) -> WorktreeAIStatus | None:
        """Mark a worktree as stalled (called from subprocess-timeout boundaries).

        Returns ``None`` when no status row exists — unknown worktrees are
        logged by the caller, not persisted here.
        """
        wt_status = self.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.mark_stalled(reason=reason)
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
        _insert_shared_note(self._conn, note)

    def clear_shared_notes(self) -> None:
        """Clear all shared notes."""
        self._conn.execute("DELETE FROM shared_notes")
        self._conn.commit()

    def record_usage(self, event: str) -> None:
        """Record a local usage event. Failure-isolated — never raises."""
        try:
            _record_usage(self._conn, event)
        except Exception:  # noqa: BLE001
            logger.debug("record_usage(%s) failed", event, exc_info=True)

    def usage_counts(self, *, days: int = 30) -> dict[str, int]:
        """Per-event usage counts over the last ``days`` days. Never raises."""
        try:
            return _usage_counts(self._conn, days=days)
        except Exception:  # noqa: BLE001
            logger.debug("usage_counts failed", exc_info=True)
            return {}

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

            if status.updated_at and (summary.most_recent_activity is None or status.updated_at > summary.most_recent_activity):
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
        """Delete transient rows (peer messages, usage events) older than ``days``.

        Returns the total number of deleted rows.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        deleted = 0
        for table in ("peer_messages", "usage_events"):
            cursor = self._conn.execute(
                f"DELETE FROM {table} WHERE created_at < ?",  # noqa: S608 — table name is a fixed literal
                (cutoff,),
            )
            deleted += cursor.rowcount
        self._conn.commit()
        if deleted:
            logger.info("Purged %d transient row(s) older than %d days", deleted, days)
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

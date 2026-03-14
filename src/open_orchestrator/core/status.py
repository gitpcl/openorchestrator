"""
Status tracking service for worktree AI tool sessions.

This module provides functionality to:
- Track what AI tools (Claude, OpenCode, Droid) are doing in each worktree
- Record commands sent between worktrees
- Generate status summaries across all worktrees
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from open_orchestrator.config import AITool
from open_orchestrator.models.status import (
    AIActivityStatus,
    StatusStore,
    StatusSummary,
    WorktreeAIStatus,
)
from open_orchestrator.utils.io import atomic_write_text, exclusive_file_lock, shared_file_lock


@dataclass
class StatusConfig:
    """Configuration for status tracking."""

    storage_path: Path | None = None

    def __post_init__(self) -> None:
        pass


class StatusTracker:
    """
    Tracks and persists AI tool activity status for worktrees.

    This service maintains a JSON store of what AI tools are doing
    in each worktree, allowing the main worktree to see activity
    across all parallel development sessions.
    """

    DEFAULT_STATUS_FILENAME = "ai_status.json"

    def __init__(self, config: StatusConfig | None = None):
        self.config = config or StatusConfig()
        self._storage_path = self.config.storage_path or self._get_default_path()
        self._store: StatusStore = StatusStore()
        self._removed_keys: set[str] = set()
        self._load_store()

    def _get_default_path(self) -> Path:
        """Get default path for status storage in user's home directory."""
        return Path.home() / ".open-orchestrator" / self.DEFAULT_STATUS_FILENAME

    def _load_store(self) -> None:
        """Load status store from persistent storage."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path) as f:
                    with shared_file_lock(f):
                        data = json.load(f)
                        self._store = StatusStore.model_validate(data)
            except (OSError, json.JSONDecodeError, ValueError):
                self._store = StatusStore()
        else:
            self._store = StatusStore()

    def _save_store(self) -> None:
        """Persist status store with exclusive lock to prevent lost updates."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._storage_path.with_suffix(".lock")
        try:
            with open(lock_path, "w") as lock_f:
                with exclusive_file_lock(lock_f):
                    # Re-read to merge concurrent changes
                    if self._storage_path.exists():
                        try:
                            with open(self._storage_path) as f:
                                disk_data = json.load(f)
                                disk_store = StatusStore.model_validate(disk_data)
                                for name, status in disk_store.statuses.items():
                                    if name not in self._store.statuses and name not in self._removed_keys:
                                        self._store.statuses[name] = status
                        except (OSError, json.JSONDecodeError, ValueError):
                            pass
                    data = json.dumps(
                        self._store.model_dump(mode="json"),
                        indent=2,
                        default=str,
                    )
                    atomic_write_text(self._storage_path, data, perms=0o600)
        except OSError:
            # Fallback: write without lock
            data = json.dumps(
                self._store.model_dump(mode="json"),
                indent=2,
                default=str,
            )
            atomic_write_text(self._storage_path, data, perms=0o600)

    def reload(self) -> None:
        """Re-read status store from disk."""
        self._load_store()

    def get_status(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Get status for a specific worktree."""
        return self._store.get_status(worktree_name)

    def get_all_statuses(self) -> list[WorktreeAIStatus]:
        """Get statuses for all tracked worktrees."""
        return self._store.get_all_statuses()

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
        self._store.set_status(status)
        self._save_store()
        return status

    def update_task(
        self, worktree_name: str, task: str, status: AIActivityStatus = AIActivityStatus.WORKING
    ) -> WorktreeAIStatus | None:
        """Update the current task for a worktree."""
        wt_status = self._store.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.update_task(task, status)
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def record_command(
        self, target_worktree: str, command: str, source_worktree: str | None = None, pane_index: int = 0, window_index: int = 0
    ) -> WorktreeAIStatus | None:
        """Record a command sent to a worktree and mark it as working."""
        wt_status = self._store.get_status(target_worktree)
        if not wt_status:
            return None

        if wt_status.activity_status in (
            AIActivityStatus.IDLE,
            AIActivityStatus.WAITING,
            AIActivityStatus.BLOCKED,
        ):
            wt_status.activity_status = AIActivityStatus.WORKING

        wt_status.updated_at = datetime.now()
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def mark_completed(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Mark a worktree's current task as completed."""
        wt_status = self._store.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.mark_completed()
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def mark_idle(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Mark a worktree as idle."""
        wt_status = self._store.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.mark_idle()
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def set_notes(self, worktree_name: str, notes: str) -> WorktreeAIStatus | None:
        """Set notes for a worktree."""
        wt_status = self._store.get_status(worktree_name)
        if not wt_status:
            return None

        wt_status.notes = notes
        wt_status.updated_at = datetime.now()
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def remove_status(self, worktree_name: str) -> bool:
        """Remove status tracking for a worktree."""
        removed = self._store.remove_status(worktree_name)
        if removed:
            self._removed_keys.add(worktree_name)
            self._save_store()
        return removed

    def set_status(self, status: WorktreeAIStatus) -> None:
        """Public API to persist a WorktreeAIStatus update."""
        self._store.set_status(status)
        self._save_store()

    def get_shared_notes(self) -> list[str]:
        """Get all shared notes."""
        return list(self._store.shared_notes)

    def add_shared_note(self, note: str) -> None:
        """Add a shared note."""
        self._store.shared_notes.append(note)
        self._save_store()

    def clear_shared_notes(self) -> None:
        """Clear all shared notes."""
        self._store.shared_notes = []
        self._save_store()

    def get_summary(self, worktree_names: list[str] | None = None) -> StatusSummary:
        """Generate a summary of AI tool status across worktrees."""
        all_statuses = self._store.get_all_statuses()

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
        current_names = [s.worktree_name for s in self._store.get_all_statuses()]

        for name in current_names:
            if name not in valid_worktree_names:
                self._store.remove_status(name)
                self._removed_keys.add(name)
                removed.append(name)

        if removed:
            self._save_store()

        return removed

    def get_current_worktree_name(self) -> str | None:
        """Get the worktree name for the current directory."""
        current_path = str(Path.cwd())

        for status in self._store.get_all_statuses():
            if current_path.startswith(status.worktree_path):
                return status.worktree_name

        return None

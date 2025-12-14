"""
Status tracking service for worktree Claude sessions.

This module provides functionality to:
- Track what Claude is doing in each worktree
- Record commands sent between worktrees
- Generate status summaries across all worktrees
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from claude_orchestrator.models.status import (
    ClaudeActivityStatus,
    CommandRecord,
    StatusStore,
    StatusSummary,
    WorktreeClaudeStatus,
)


@dataclass
class StatusConfig:
    """Configuration for status tracking."""

    storage_path: Optional[Path] = None
    max_command_history: int = 20
    auto_cleanup_orphans: bool = True

    def __post_init__(self):
        if self.max_command_history < 1:
            raise ValueError("max_command_history must be at least 1")


class StatusTracker:
    """
    Tracks and persists Claude activity status for worktrees.

    This service maintains a JSON store of what Claude is doing
    in each worktree, allowing the main worktree to see activity
    across all parallel development sessions.
    """

    DEFAULT_STATUS_FILENAME = "claude_status.json"

    def __init__(self, config: Optional[StatusConfig] = None):
        self.config = config or StatusConfig()
        self._storage_path = self.config.storage_path or self._get_default_path()
        self._store: Optional[StatusStore] = None
        self._load_store()

    def _get_default_path(self) -> Path:
        """Get default path for status storage in user's home directory."""
        return Path.home() / ".claude-orchestrator" / self.DEFAULT_STATUS_FILENAME

    def _load_store(self) -> None:
        """Load status store from persistent storage."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path, "r") as f:
                    data = json.load(f)
                    self._store = StatusStore.model_validate(data)
            except (json.JSONDecodeError, IOError, ValueError):
                self._store = StatusStore()
        else:
            self._store = StatusStore()

    def _save_store(self) -> None:
        """Persist status store to storage."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self._storage_path, "w") as f:
            json.dump(
                self._store.model_dump(mode="json"),
                f,
                indent=2,
                default=str
            )

    def get_status(self, worktree_name: str) -> Optional[WorktreeClaudeStatus]:
        """Get status for a specific worktree."""
        return self._store.get_status(worktree_name)

    def get_all_statuses(self) -> List[WorktreeClaudeStatus]:
        """Get statuses for all tracked worktrees."""
        return self._store.get_all_statuses()

    def initialize_status(
        self,
        worktree_name: str,
        worktree_path: str,
        branch: str,
        tmux_session: Optional[str] = None
    ) -> WorktreeClaudeStatus:
        """
        Initialize status tracking for a new worktree.

        Args:
            worktree_name: Name of the worktree
            worktree_path: Absolute path to the worktree
            branch: Git branch name
            tmux_session: Associated tmux session name

        Returns:
            The newly created WorktreeClaudeStatus
        """
        status = WorktreeClaudeStatus(
            worktree_name=worktree_name,
            worktree_path=worktree_path,
            branch=branch,
            tmux_session=tmux_session,
            activity_status=ClaudeActivityStatus.IDLE,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        self._store.set_status(status)
        self._save_store()
        return status

    def update_task(
        self,
        worktree_name: str,
        task: str,
        status: ClaudeActivityStatus = ClaudeActivityStatus.WORKING
    ) -> Optional[WorktreeClaudeStatus]:
        """
        Update the current task for a worktree.

        Args:
            worktree_name: Name of the worktree
            task: Description of the current task
            status: Activity status (default: WORKING)

        Returns:
            Updated WorktreeClaudeStatus or None if not found
        """
        wt_status = self._store.get_status(worktree_name)

        if not wt_status:
            return None

        wt_status.update_task(task, status)
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def record_command(
        self,
        target_worktree: str,
        command: str,
        source_worktree: Optional[str] = None,
        pane_index: int = 0,
        window_index: int = 0
    ) -> Optional[WorktreeClaudeStatus]:
        """
        Record a command sent to a worktree.

        Args:
            target_worktree: Name of the worktree receiving the command
            command: The command that was sent
            source_worktree: Name of the worktree that sent the command (None if manual)
            pane_index: Target pane index
            window_index: Target window index

        Returns:
            Updated WorktreeClaudeStatus or None if not found
        """
        wt_status = self._store.get_status(target_worktree)

        if not wt_status:
            return None

        wt_status.add_command(
            command=command,
            source_worktree=source_worktree,
            pane_index=pane_index,
            window_index=window_index,
            max_history=self.config.max_command_history
        )

        if wt_status.activity_status == ClaudeActivityStatus.IDLE:
            wt_status.activity_status = ClaudeActivityStatus.WORKING

        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def mark_completed(self, worktree_name: str) -> Optional[WorktreeClaudeStatus]:
        """Mark a worktree's current task as completed."""
        wt_status = self._store.get_status(worktree_name)

        if not wt_status:
            return None

        wt_status.mark_completed()
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def mark_idle(self, worktree_name: str) -> Optional[WorktreeClaudeStatus]:
        """Mark a worktree as idle."""
        wt_status = self._store.get_status(worktree_name)

        if not wt_status:
            return None

        wt_status.mark_idle()
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def set_notes(
        self,
        worktree_name: str,
        notes: str
    ) -> Optional[WorktreeClaudeStatus]:
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
        """
        Remove status tracking for a worktree.

        Returns:
            True if removed, False if not found
        """
        removed = self._store.remove_status(worktree_name)

        if removed:
            self._save_store()

        return removed

    def get_summary(
        self,
        worktree_names: Optional[List[str]] = None
    ) -> StatusSummary:
        """
        Generate a summary of Claude status across worktrees.

        Args:
            worktree_names: Optional list of worktree names to filter by.
                          If None, includes all tracked worktrees.

        Returns:
            StatusSummary with aggregated statistics
        """
        all_statuses = self._store.get_all_statuses()

        if worktree_names:
            all_statuses = [
                s for s in all_statuses
                if s.worktree_name in worktree_names
            ]

        summary = StatusSummary(
            timestamp=datetime.now(),
            total_worktrees=len(worktree_names) if worktree_names else len(all_statuses),
            worktrees_with_status=len(all_statuses),
            statuses=all_statuses
        )

        for status in all_statuses:
            if status.activity_status == ClaudeActivityStatus.WORKING:
                summary.active_claudes += 1
            elif status.activity_status == ClaudeActivityStatus.IDLE:
                summary.idle_claudes += 1
            elif status.activity_status == ClaudeActivityStatus.BLOCKED:
                summary.blocked_claudes += 1
            else:
                summary.unknown_status += 1

            summary.total_commands_sent += len(status.recent_commands)

            if status.updated_at:
                if (
                    summary.most_recent_activity is None or
                    status.updated_at > summary.most_recent_activity
                ):
                    summary.most_recent_activity = status.updated_at

        return summary

    def cleanup_orphans(self, valid_worktree_names: List[str]) -> List[str]:
        """
        Remove status entries for worktrees that no longer exist.

        Args:
            valid_worktree_names: List of currently valid worktree names

        Returns:
            List of removed worktree names
        """
        removed = []
        current_names = [s.worktree_name for s in self._store.get_all_statuses()]

        for name in current_names:
            if name not in valid_worktree_names:
                self._store.remove_status(name)
                removed.append(name)

        if removed:
            self._save_store()

        return removed

    def get_current_worktree_name(self) -> Optional[str]:
        """
        Get the worktree name for the current directory.

        Returns:
            Worktree name if in a tracked worktree, None otherwise
        """
        current_path = str(Path.cwd())

        for status in self._store.get_all_statuses():
            if current_path.startswith(status.worktree_path):
                return status.worktree_name

        return None

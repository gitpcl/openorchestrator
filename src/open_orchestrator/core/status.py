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
from open_orchestrator.utils.io import atomic_write_text, shared_file_lock


@dataclass
class StatusConfig:
    """Configuration for status tracking."""

    storage_path: Path | None = None
    max_command_history: int = 20
    auto_cleanup_orphans: bool = True
    store_commands: bool = True
    redact_commands: bool = True

    def __post_init__(self) -> None:
        if self.max_command_history < 1:
            raise ValueError("max_command_history must be at least 1")


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
        """Persist status store to storage using atomic write and 0o600 perms."""
        data = json.dumps(
            self._store.model_dump(mode="json"),
            indent=2,
            default=str,
        )
        atomic_write_text(self._storage_path, data, perms=0o600)

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
        """
        Initialize status tracking for a new worktree.

        Args:
            worktree_name: Name of the worktree
            worktree_path: Absolute path to the worktree
            branch: Git branch name
            tmux_session: Associated tmux session name
            ai_tool: AI tool being used (claude, opencode, droid)

        Returns:
            The newly created WorktreeAIStatus
        """
        ai_tool_str = ai_tool.value if isinstance(ai_tool, AITool) else ai_tool

        status = WorktreeAIStatus(
            worktree_name=worktree_name,
            worktree_path=worktree_path,
            branch=branch,
            tmux_session=tmux_session,
            ai_tool=ai_tool_str,
            activity_status=AIActivityStatus.IDLE,
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
        status: AIActivityStatus = AIActivityStatus.WORKING
    ) -> WorktreeAIStatus | None:
        """
        Update the current task for a worktree.

        Args:
            worktree_name: Name of the worktree
            task: Description of the current task
            status: Activity status (default: WORKING)

        Returns:
            Updated WorktreeAIStatus or None if not found
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
        source_worktree: str | None = None,
        pane_index: int = 0,
        window_index: int = 0
    ) -> WorktreeAIStatus | None:
        """
        Record a command sent to a worktree.

        Args:
            target_worktree: Name of the worktree receiving the command
            command: The command that was sent
            source_worktree: Name of the worktree that sent the command (None if manual)
            pane_index: Target pane index
            window_index: Target window index

        Returns:
            Updated WorktreeAIStatus or None if not found
        """
        wt_status = self._store.get_status(target_worktree)

        if not wt_status:
            return None

        if self.config.redact_commands:
            command_to_store = self._sanitize_command(command)
        else:
            command_to_store = command

        if self.config.store_commands:
            wt_status.add_command(
                command=command_to_store,
                source_worktree=source_worktree,
                pane_index=pane_index,
                window_index=window_index,
                max_history=self.config.max_command_history
            )

        if wt_status.activity_status == AIActivityStatus.IDLE:
            wt_status.activity_status = AIActivityStatus.WORKING

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

    def set_notes(
        self,
        worktree_name: str,
        notes: str
    ) -> WorktreeAIStatus | None:
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
        worktree_names: list[str] | None = None
    ) -> StatusSummary:
        """
        Generate a summary of AI tool status across worktrees.

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
            if status.activity_status == AIActivityStatus.WORKING:
                summary.active_ai_sessions += 1
            elif status.activity_status == AIActivityStatus.IDLE:
                summary.idle_ai_sessions += 1
            elif status.activity_status == AIActivityStatus.BLOCKED:
                summary.blocked_ai_sessions += 1
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

    def cleanup_orphans(self, valid_worktree_names: list[str]) -> list[str]:
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

    def set_status(self, status: WorktreeAIStatus) -> None:
        """Public API to persist a WorktreeAIStatus update."""
        self._store.set_status(status)
        self._save_store()

    def _sanitize_command(self, text: str) -> str:
        """Best-effort redaction of secrets in commands."""
        import re as _re
        redactions = [
            # Authorization: Bearer <token>
            (r"(Authorization\s*:\s*Bearer\s+)[^\s]+", r"\1[REDACTED]"),
            # password=... or password: ... (with optional quotes)
            (r'(?i)(password\s*[:=]\s*)["\']?([^"\'\s]+)["\']?', r"\1[REDACTED]"),
            # api key/token patterns (with optional quotes)
            (r'(?i)(api[_-]?key\s*[:=]\s*)["\']?([^"\'\s]+)["\']?', r"\1[REDACTED]"),
            (r'(?i)(token\s*[:=]\s*)["\']?([^"\'\s]+)["\']?', r"\1[REDACTED]"),
            (r'(?i)(secret\s*[:=]\s*)["\']?([^"\'\s]+)["\']?', r"\1[REDACTED]"),
            # URLs with embedded credentials (user:pass@host)
            (r"(https?://)[^/:@\s]+:[^/:@\s]+@", r"\1[REDACTED]:[REDACTED]@"),
            # JWT tokens (three base64 segments separated by dots)
            (r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[JWT REDACTED]"),
            # AWS Access Key ID pattern
            (r"AKIA[0-9A-Z]{16}", "AKIA[REDACTED]"),
            # AWS Secret Access Key (40 character base64)
            (r"(?i)(aws_secret_access_key\s*[:=]\s*)[A-Za-z0-9/+=]{40}", r"\1[REDACTED]"),
            # Private key block markers
            (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC )?PRIVATE KEY-----", "[PRIVATE KEY REDACTED]"),
        ]
        redacted = text
        for pat, repl in redactions:
            redacted = _re.sub(pat, repl, redacted)
        return redacted

    def get_current_worktree_name(self) -> str | None:
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

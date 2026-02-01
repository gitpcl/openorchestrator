"""
Session management service for Claude session data.

This module provides functionality to:
- Copy Claude session data between worktrees
- Track session lineage (which worktree a session came from)
- Support session resume across worktrees
"""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from open_orchestrator.models.session import (
    SessionCopyResult,
    SessionCopyStatus,
    SessionData,
    SessionStore,
)
from open_orchestrator.utils.io import atomic_write_text, shared_file_lock


class SessionError(Exception):
    """Base exception for session operations."""


class SessionNotFoundError(SessionError):
    """Raised when a session cannot be found."""


class SessionCopyError(SessionError):
    """Raised when session copy fails."""


@dataclass
class SessionConfig:
    """Configuration for session management."""

    storage_path: Path | None = None
    claude_dir_name: str = ".claude"
    projects_subdir: str = "projects"
    copy_conversation_history: bool = True
    copy_session_settings: bool = True
    excluded_files: list[str] = field(
        default_factory=lambda: [
            "*.log",
            "*.tmp",
            "__pycache__",
        ]
    )


class SessionManager:
    """
    Manages Claude session data across worktrees.

    This service allows copying Claude session data from one worktree
    to another, preserving conversation context and session state.
    """

    DEFAULT_STORAGE_FILENAME = "sessions.json"

    def __init__(self, config: SessionConfig | None = None):
        self.config = config or SessionConfig()
        self._storage_path = self.config.storage_path or self._get_default_path()
        self._store: SessionStore = SessionStore()
        self._load_store()

    def _get_default_path(self) -> Path:
        """Get default path for session storage in user's home directory."""
        return Path.home() / ".open-orchestrator" / self.DEFAULT_STORAGE_FILENAME

    def _load_store(self) -> None:
        """Load session store from persistent storage."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path) as f:
                    with shared_file_lock(f):
                        data = json.load(f)
                        self._store = SessionStore.model_validate(data)
            except (OSError, json.JSONDecodeError, ValueError):
                self._store = SessionStore()
        else:
            self._store = SessionStore()

    def _save_store(self) -> None:
        """Persist session store to storage using atomic write."""
        data = json.dumps(
            self._store.model_dump(mode="json"),
            indent=2,
            default=str,
        )
        atomic_write_text(self._storage_path, data, perms=0o600)

    def get_claude_dir(self, worktree_path: str) -> Path:
        """Get the .claude directory path for a worktree."""
        return Path(worktree_path) / self.config.claude_dir_name

    def get_projects_dir(self, worktree_path: str) -> Path:
        """Get the .claude/projects directory path for a worktree."""
        return self.get_claude_dir(worktree_path) / self.config.projects_subdir

    def find_session_files(self, worktree_path: str) -> list[Path]:
        """
        Find all Claude session files in a worktree.

        Returns:
            List of paths to session-related files
        """
        claude_dir = self.get_claude_dir(worktree_path)

        if not claude_dir.exists():
            return []

        session_files = []
        excluded = set(self.config.excluded_files)

        for item in claude_dir.rglob("*"):
            if item.is_file():
                # Skip excluded patterns
                skip = False
                for pattern in excluded:
                    if item.match(pattern):
                        skip = True
                        break
                if not skip:
                    session_files.append(item)

        return session_files

    def get_latest_session_id(self, worktree_path: str) -> str | None:
        """
        Get the most recent Claude session ID for a worktree.

        Looks for session files in .claude/projects/ directory
        and returns the most recent session ID based on modification time.
        """
        projects_dir = self.get_projects_dir(worktree_path)

        if not projects_dir.exists():
            return None

        # Look for .jsonl files which contain session transcripts
        session_files = list(projects_dir.glob("**/*.jsonl"))

        if not session_files:
            return None

        # Sort by modification time (most recent first)
        session_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        # Session ID is typically the file stem
        latest = session_files[0]
        return latest.stem

    def initialize_session(
        self,
        worktree_name: str,
        worktree_path: str,
    ) -> SessionData:
        """
        Initialize session tracking for a worktree.

        Args:
            worktree_name: Name of the worktree
            worktree_path: Absolute path to the worktree

        Returns:
            The newly created SessionData
        """
        claude_dir = self.get_claude_dir(worktree_path)
        session_id = self.get_latest_session_id(worktree_path)

        session = SessionData(
            worktree_name=worktree_name,
            worktree_path=worktree_path,
            session_id=session_id,
            session_dir=str(claude_dir) if claude_dir.exists() else None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        self._store.set_session(session)
        self._save_store()
        return session

    def get_session(self, worktree_name: str) -> SessionData | None:
        """Get session data for a specific worktree."""
        return self._store.get_session(worktree_name)

    def get_all_sessions(self) -> list[SessionData]:
        """Get all tracked sessions."""
        return self._store.get_all_sessions()

    def copy_session(
        self,
        source_worktree_name: str,
        source_worktree_path: str,
        target_worktree_name: str,
        target_worktree_path: str,
        overwrite: bool = False,
    ) -> SessionCopyResult:
        """
        Copy Claude session data from one worktree to another.

        This preserves the conversation history and context from the
        source worktree, allowing Claude to continue where it left off.

        Args:
            source_worktree_name: Name of the source worktree
            source_worktree_path: Path to the source worktree
            target_worktree_name: Name of the target worktree
            target_worktree_path: Path to the target worktree
            overwrite: Whether to overwrite existing session data

        Returns:
            SessionCopyResult with details of the operation
        """
        source_claude_dir = self.get_claude_dir(source_worktree_path)
        target_claude_dir = self.get_claude_dir(target_worktree_path)

        # Check if source has session data
        if not source_claude_dir.exists():
            return SessionCopyResult(
                status=SessionCopyStatus.NO_SESSION,
                source_worktree=source_worktree_name,
                target_worktree=target_worktree_name,
                message=f"No Claude session data found in {source_worktree_name}",
            )

        source_files = self.find_session_files(source_worktree_path)

        if not source_files:
            return SessionCopyResult(
                status=SessionCopyStatus.NO_SESSION,
                source_worktree=source_worktree_name,
                target_worktree=target_worktree_name,
                message="No session files found in source worktree",
            )

        # Check for existing data in target
        if target_claude_dir.exists() and not overwrite:
            target_files = self.find_session_files(target_worktree_path)
            if target_files:
                return SessionCopyResult(
                    status=SessionCopyStatus.FAILED,
                    source_worktree=source_worktree_name,
                    target_worktree=target_worktree_name,
                    message="Target already has session data. Use --overwrite to replace.",
                )

        files_copied: list[str] = []
        files_skipped: list[str] = []

        try:
            # Create target .claude directory
            target_claude_dir.mkdir(parents=True, exist_ok=True)

            # Copy files preserving directory structure
            for source_file in source_files:
                relative_path = source_file.relative_to(source_claude_dir)
                target_file = target_claude_dir / relative_path

                # Create parent directories
                target_file.parent.mkdir(parents=True, exist_ok=True)

                try:
                    shutil.copy2(source_file, target_file)
                    files_copied.append(str(relative_path))
                except (OSError, PermissionError) as e:
                    files_skipped.append(f"{relative_path}: {e}")

            # Get session ID from source
            session_id = self.get_latest_session_id(source_worktree_path)

            # Update session tracking for target
            target_session = SessionData(
                worktree_name=target_worktree_name,
                worktree_path=target_worktree_path,
                session_id=session_id,
                session_dir=str(target_claude_dir),
                copied_from=source_worktree_name,
                copied_at=datetime.now(),
                original_session_id=session_id,
                data_paths=files_copied,
            )
            self._store.set_session(target_session)
            self._save_store()

            status = (
                SessionCopyStatus.SUCCESS if not files_skipped
                else SessionCopyStatus.PARTIAL
            )

            return SessionCopyResult(
                status=status,
                source_worktree=source_worktree_name,
                target_worktree=target_worktree_name,
                files_copied=files_copied,
                files_skipped=files_skipped,
                session_id=session_id,
                message=f"Copied {len(files_copied)} file(s) from {source_worktree_name}",
            )

        except Exception as e:
            return SessionCopyResult(
                status=SessionCopyStatus.FAILED,
                source_worktree=source_worktree_name,
                target_worktree=target_worktree_name,
                files_copied=files_copied,
                files_skipped=files_skipped,
                message=f"Copy failed: {e}",
            )

    def remove_session(self, worktree_name: str) -> bool:
        """
        Remove session tracking for a worktree.

        Note: This only removes the tracking data, not the actual
        .claude directory in the worktree.

        Returns:
            True if removed, False if not found
        """
        removed = self._store.remove_session(worktree_name)

        if removed:
            self._save_store()

        return removed

    def get_resume_command(
        self,
        worktree_name: str,
        worktree_path: str,
    ) -> str | None:
        """
        Get the Claude resume command for a worktree.

        Returns the claude command with --resume flag if a session exists.

        Args:
            worktree_name: Name of the worktree
            worktree_path: Path to the worktree

        Returns:
            Claude command string with resume flag, or None if no session
        """
        session_id = self.get_latest_session_id(worktree_path)

        if not session_id:
            return None

        return f"claude --resume {session_id}"

    def get_continue_command(self, worktree_path: str) -> str:
        """
        Get the Claude continue command for a worktree.

        Uses --continue to resume the most recent session.
        """
        return "claude --continue"

    def cleanup_orphans(self, valid_worktree_names: list[str]) -> list[str]:
        """
        Remove session entries for worktrees that no longer exist.

        Args:
            valid_worktree_names: List of currently valid worktree names

        Returns:
            List of removed worktree names
        """
        removed = []
        current_names = [s.worktree_name for s in self._store.get_all_sessions()]

        for name in current_names:
            if name not in valid_worktree_names:
                self._store.remove_session(name)
                removed.append(name)

        if removed:
            self._save_store()

        return removed

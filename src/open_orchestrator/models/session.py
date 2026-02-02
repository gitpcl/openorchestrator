"""
Pydantic models for Claude session data management.

This module provides data models for:
- Tracking Claude session data copied between worktrees
- Session metadata and preservation
- Session resume functionality
"""

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class SessionCopyStatus(str, Enum):
    """Status of a session copy operation."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    NO_SESSION = "no_session"


class SessionData(BaseModel):
    """Represents Claude session data for a worktree."""

    model_config = ConfigDict(use_enum_values=True)

    worktree_name: str = Field(..., description="Name of the worktree")
    worktree_path: str = Field(..., description="Absolute path to the worktree")
    session_id: str | None = Field(default=None, description="Claude session ID if known")
    session_dir: str | None = Field(default=None, description="Path to the .claude directory containing session data")
    copied_from: str | None = Field(default=None, description="Source worktree name if this session was copied")
    copied_at: datetime | None = Field(default=None, description="When the session was copied")
    original_session_id: str | None = Field(default=None, description="Original session ID from source worktree")
    data_paths: list[str] = Field(default_factory=list, description="Paths of copied session data files")
    metadata: dict[str, str] = Field(default_factory=dict, description="Custom session metadata")
    created_at: datetime = Field(default_factory=datetime.now, description="When this record was created")
    updated_at: datetime = Field(default_factory=datetime.now, description="When this record was last updated")

    @property
    def has_session(self) -> bool:
        """Check if this worktree has Claude session data."""
        if not self.session_dir:
            return False
        session_path = Path(self.session_dir)
        return session_path.exists() and any(session_path.iterdir())

    @property
    def is_copied(self) -> bool:
        """Check if this session was copied from another worktree."""
        return self.copied_from is not None


class SessionCopyResult(BaseModel):
    """Result of a session copy operation."""

    model_config = ConfigDict(use_enum_values=True)

    status: SessionCopyStatus = Field(..., description="Status of the copy operation")
    source_worktree: str = Field(..., description="Source worktree name")
    target_worktree: str = Field(..., description="Target worktree name")
    files_copied: list[str] = Field(default_factory=list, description="List of files that were copied")
    files_skipped: list[str] = Field(default_factory=list, description="List of files that were skipped")
    message: str = Field(default="", description="Human-readable result message")
    session_id: str | None = Field(default=None, description="Session ID of the copied session")
    copied_at: datetime = Field(default_factory=datetime.now, description="When the copy was performed")


class SessionStore(BaseModel):
    """Persistent storage for session data across worktrees."""

    version: str = Field(default="1.0", description="Storage format version")
    updated_at: datetime = Field(default_factory=datetime.now, description="When the store was last updated")
    sessions: dict[str, SessionData] = Field(default_factory=dict, description="Map of worktree name to session data")

    def get_session(self, worktree_name: str) -> SessionData | None:
        """Get session data for a specific worktree."""
        return self.sessions.get(worktree_name)

    def set_session(self, session: SessionData) -> None:
        """Set session data for a worktree."""
        self.sessions[session.worktree_name] = session
        self.updated_at = datetime.now()

    def remove_session(self, worktree_name: str) -> bool:
        """Remove session data for a worktree. Returns True if removed."""
        if worktree_name in self.sessions:
            del self.sessions[worktree_name]
            self.updated_at = datetime.now()
            return True
        return False

    def get_all_sessions(self) -> list[SessionData]:
        """Get all session data entries."""
        return list(self.sessions.values())

    def get_sessions_copied_from(self, source_worktree: str) -> list[SessionData]:
        """Get all sessions that were copied from a specific worktree."""
        return [s for s in self.sessions.values() if s.copied_from == source_worktree]

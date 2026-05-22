"""Pydantic models for worktree information."""

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class WorktreeInfo(BaseModel):
    """Information about a git worktree."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path = Field(description="Absolute path to the worktree directory")
    branch: str = Field(description="Branch name checked out in this worktree")
    head_commit: str = Field(description="Short SHA of the HEAD commit")
    is_main: bool = Field(default=False, description="Whether this is the main worktree")
    is_detached: bool = Field(default=False, description="Whether HEAD is detached")
    created_at: datetime | None = Field(default=None, description="When the worktree was created")
    template_name: str | None = Field(default=None, description="Template used to create this worktree")

    @property
    def name(self) -> str:
        """Get the worktree directory name."""
        return self.path.name

    @property
    def short_path(self) -> str:
        """Get a shortened display path."""
        return f"~/{self.path.relative_to(Path.home())}" if self.path.is_relative_to(Path.home()) else str(self.path)


class SessionType(str, Enum):
    """How a workspace is isolated on disk."""

    WORKTREE = "worktree"  # git worktree (full clone on disk)
    BRANCH = "branch"  # branch in current checkout (no extra disk)


class SessionInfo(BaseModel):
    """Carrier through the AgentLauncher pipeline describing how a session
    is provisioned.

    Replaces plain ``WorktreeInfo`` usage in places where the session
    could be either a git worktree or an in-checkout branch.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_type: SessionType = SessionType.WORKTREE
    name: str = Field(description="Display name (worktree dir name or short branch)")
    branch: str = Field(description="Branch name checked out")
    worktree_path: str | None = Field(default=None, description="Filesystem path (None for branch mode until checkout)")
    repo_root: str = Field(description="Repository root path")
    base_branch: str | None = Field(default=None, description="Base branch the session was created from")
    head_commit: str | None = Field(default=None, description="Head commit SHA")
    is_main: bool = Field(default=False, description="Whether this is the main worktree")


class WorktreeCreateResult(BaseModel):
    """Result of creating a new worktree."""

    worktree: WorktreeInfo
    created_branch: bool = Field(default=False, description="Whether a new branch was created")
    deps_installed: bool = Field(default=False, description="Whether dependencies were installed")
    tmux_session: str | None = Field(default=None, description="Name of the tmux session if created")
    template_applied: str | None = Field(default=None, description="Name of template that was applied")

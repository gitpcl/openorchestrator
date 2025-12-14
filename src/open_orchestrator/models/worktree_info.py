"""Pydantic models for worktree information."""

from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class WorktreeInfo(BaseModel):
    """Information about a git worktree."""

    path: Path = Field(description="Absolute path to the worktree directory")
    branch: str = Field(description="Branch name checked out in this worktree")
    head_commit: str = Field(description="Short SHA of the HEAD commit")
    is_main: bool = Field(default=False, description="Whether this is the main worktree")
    is_detached: bool = Field(default=False, description="Whether HEAD is detached")
    created_at: Optional[datetime] = Field(
        default=None, description="When the worktree was created"
    )

    class Config:
        arbitrary_types_allowed = True

    @property
    def name(self) -> str:
        """Get the worktree directory name."""
        return self.path.name

    @property
    def short_path(self) -> str:
        """Get a shortened display path."""
        return f"~/{self.path.relative_to(Path.home())}" if self.path.is_relative_to(
            Path.home()
        ) else str(self.path)


class WorktreeCreateResult(BaseModel):
    """Result of creating a new worktree."""

    worktree: WorktreeInfo
    created_branch: bool = Field(
        default=False, description="Whether a new branch was created"
    )
    deps_installed: bool = Field(
        default=False, description="Whether dependencies were installed"
    )
    tmux_session: Optional[str] = Field(
        default=None, description="Name of the tmux session if created"
    )

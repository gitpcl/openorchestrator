"""
Pydantic models for worktree AI tool status tracking.

This module provides data models for:
- Tracking what AI tools (Claude, OpenCode, Droid) are doing in each worktree
- Aggregating status across all worktrees
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AIActivityStatus(str, Enum):
    """Status of AI tool activity in a worktree."""

    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"
    UNKNOWN = "unknown"


class WorktreeAIStatus(BaseModel):
    """Status of AI tool activity in a worktree."""

    worktree_name: str = Field(..., description="Name of the worktree")
    worktree_path: str = Field(..., description="Absolute path to the worktree")
    branch: str = Field(..., description="Current git branch")
    tmux_session: str | None = Field(default=None, description="Associated tmux session name")
    ai_tool: str = Field(default="claude", description="AI tool being used (claude, opencode, droid)")
    activity_status: AIActivityStatus = Field(default=AIActivityStatus.UNKNOWN, description="Current activity status")
    current_task: str | None = Field(default=None, description="Description of current task AI is working on")
    last_task_update: datetime | None = Field(default=None, description="When the task was last updated")
    notes: str | None = Field(default=None, description="Additional notes or context")
    modified_files: list[str] = Field(default_factory=list, description="Files modified vs base branch")
    created_at: datetime = Field(default_factory=datetime.now, description="When this status record was created")
    updated_at: datetime = Field(default_factory=datetime.now, description="When this status was last updated")

    def update_task(self, task: str, status: AIActivityStatus = AIActivityStatus.WORKING) -> None:
        """Update the current task."""
        self.current_task = task
        self.activity_status = status
        self.last_task_update = datetime.now()
        self.updated_at = datetime.now()

    def mark_completed(self) -> None:
        """Mark the current task as completed."""
        self.activity_status = AIActivityStatus.COMPLETED
        self.updated_at = datetime.now()

    def mark_idle(self) -> None:
        """Mark the worktree as idle."""
        self.activity_status = AIActivityStatus.IDLE
        self.current_task = None
        self.updated_at = datetime.now()


class StatusSummary(BaseModel):
    """Summary of AI tool status across all worktrees."""

    timestamp: datetime = Field(default_factory=datetime.now, description="When this summary was generated")
    total_worktrees: int = Field(default=0, ge=0)
    worktrees_with_status: int = Field(default=0, ge=0)
    active_ai_sessions: int = Field(default=0, ge=0, description="Number of worktrees where AI is working")
    idle_ai_sessions: int = Field(default=0, ge=0, description="Number of worktrees where AI is idle")
    blocked_ai_sessions: int = Field(default=0, ge=0, description="Number of worktrees where AI is blocked")
    unknown_status: int = Field(default=0, ge=0, description="Number of worktrees with unknown status")
    most_recent_activity: datetime | None = Field(default=None, description="Most recent activity across all worktrees")
    statuses: list[WorktreeAIStatus] = Field(default_factory=list, description="Individual status for each worktree")

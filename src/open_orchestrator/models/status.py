"""
Pydantic models for worktree AI tool status tracking.

This module provides data models for:
- Tracking what AI tools (Claude, OpenCode, Droid) are doing in each worktree
- Aggregating status across all worktrees
- Recording commands sent between worktrees
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


class CommandRecord(BaseModel):
    """Record of a command sent to a worktree."""

    timestamp: datetime = Field(..., description="When the command was sent")
    command: str = Field(..., description="The command that was sent")
    source_worktree: str | None = Field(
        default=None,
        description="Worktree that sent the command (None if manual)"
    )
    pane_index: int = Field(default=0, description="Target pane index")
    window_index: int = Field(default=0, description="Target window index")


class WorktreeAIStatus(BaseModel):
    """Status of AI tool activity in a worktree."""

    worktree_name: str = Field(..., description="Name of the worktree")
    worktree_path: str = Field(..., description="Absolute path to the worktree")
    branch: str = Field(..., description="Current git branch")
    tmux_session: str | None = Field(
        default=None,
        description="Associated tmux session name"
    )
    ai_tool: str = Field(
        default="claude",
        description="AI tool being used (claude, opencode, droid)"
    )
    activity_status: AIActivityStatus = Field(
        default=AIActivityStatus.UNKNOWN,
        description="Current activity status"
    )
    current_task: str | None = Field(
        default=None,
        description="Description of current task AI is working on"
    )
    last_task_update: datetime | None = Field(
        default=None,
        description="When the task was last updated"
    )
    recent_commands: list[CommandRecord] = Field(
        default_factory=list,
        description="Recent commands sent to this worktree"
    )
    notes: str | None = Field(
        default=None,
        description="Additional notes or context"
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="When this status record was created"
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        description="When this status was last updated"
    )

    class Config:
        use_enum_values = True

    def add_command(
        self,
        command: str,
        source_worktree: str | None = None,
        pane_index: int = 0,
        window_index: int = 0,
        max_history: int = 20
    ) -> None:
        """Add a command to the recent commands history."""
        record = CommandRecord(
            timestamp=datetime.now(),
            command=command,
            source_worktree=source_worktree,
            pane_index=pane_index,
            window_index=window_index
        )
        self.recent_commands.append(record)
        self.updated_at = datetime.now()

        if len(self.recent_commands) > max_history:
            self.recent_commands = self.recent_commands[-max_history:]

    def update_task(
        self,
        task: str,
        status: AIActivityStatus = AIActivityStatus.WORKING
    ) -> None:
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

    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When this summary was generated"
    )
    total_worktrees: int = Field(default=0, ge=0)
    worktrees_with_status: int = Field(default=0, ge=0)
    active_ai_sessions: int = Field(
        default=0,
        ge=0,
        description="Number of worktrees where AI is working"
    )
    idle_ai_sessions: int = Field(
        default=0,
        ge=0,
        description="Number of worktrees where AI is idle"
    )
    blocked_ai_sessions: int = Field(
        default=0,
        ge=0,
        description="Number of worktrees where AI is blocked"
    )
    unknown_status: int = Field(
        default=0,
        ge=0,
        description="Number of worktrees with unknown status"
    )
    total_commands_sent: int = Field(
        default=0,
        ge=0,
        description="Total commands sent across all worktrees"
    )
    most_recent_activity: datetime | None = Field(
        default=None,
        description="Most recent activity across all worktrees"
    )
    statuses: list[WorktreeAIStatus] = Field(
        default_factory=list,
        description="Individual status for each worktree"
    )


class StatusStore(BaseModel):
    """Persistent storage for worktree statuses."""

    version: str = Field(default="1.1", description="Storage format version")
    updated_at: datetime = Field(
        default_factory=datetime.now,
        description="When the store was last updated"
    )
    statuses: dict[str, WorktreeAIStatus] = Field(
        default_factory=dict,
        description="Map of worktree name to status"
    )

    def get_status(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Get status for a specific worktree."""
        return self.statuses.get(worktree_name)

    def set_status(self, status: WorktreeAIStatus) -> None:
        """Set status for a worktree."""
        self.statuses[status.worktree_name] = status
        self.updated_at = datetime.now()

    def remove_status(self, worktree_name: str) -> bool:
        """Remove status for a worktree. Returns True if removed."""
        if worktree_name in self.statuses:
            del self.statuses[worktree_name]
            self.updated_at = datetime.now()
            return True
        return False

    def get_all_statuses(self) -> list[WorktreeAIStatus]:
        """Get all worktree statuses."""
        return list(self.statuses.values())

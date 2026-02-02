"""
Pydantic models for worktree AI tool status tracking.

This module provides data models for:
- Tracking what AI tools (Claude, OpenCode, Droid) are doing in each worktree
- Aggregating status across all worktrees
- Recording commands sent between worktrees
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AIActivityStatus(str, Enum):
    """Status of AI tool activity in a worktree."""

    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"
    UNKNOWN = "unknown"


class TokenUsage(BaseModel):
    """Token usage tracking for AI tool sessions."""

    input_tokens: int = Field(default=0, ge=0, description="Total input tokens used")
    output_tokens: int = Field(default=0, ge=0, description="Total output tokens used")
    cache_read_tokens: int = Field(default=0, ge=0, description="Tokens read from cache")
    cache_write_tokens: int = Field(default=0, ge=0, description="Tokens written to cache")
    last_updated: datetime = Field(default_factory=datetime.now, description="When token usage was last updated")

    @property
    def total_tokens(self) -> int:
        """Get total tokens (input + output)."""
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate cost in USD (based on Claude Opus pricing)."""
        # Claude Opus pricing: $15/1M input, $75/1M output (as of 2024)
        input_cost = (self.input_tokens / 1_000_000) * 15
        output_cost = (self.output_tokens / 1_000_000) * 75
        return input_cost + output_cost


class CommandRecord(BaseModel):
    """Record of a command sent to a worktree."""

    timestamp: datetime = Field(..., description="When the command was sent")
    command: str = Field(..., description="The command that was sent")
    source_worktree: str | None = Field(default=None, description="Worktree that sent the command (None if manual)")
    pane_index: int = Field(default=0, description="Target pane index")
    window_index: int = Field(default=0, description="Target window index")


class WorktreeAIStatus(BaseModel):
    """Status of AI tool activity in a worktree."""

    model_config = ConfigDict(use_enum_values=True)

    worktree_name: str = Field(..., description="Name of the worktree")
    worktree_path: str = Field(..., description="Absolute path to the worktree")
    branch: str = Field(..., description="Current git branch")
    tmux_session: str | None = Field(default=None, description="Associated tmux session name")
    ai_tool: str = Field(default="claude", description="AI tool being used (claude, opencode, droid)")
    activity_status: AIActivityStatus = Field(default=AIActivityStatus.UNKNOWN, description="Current activity status")
    current_task: str | None = Field(default=None, description="Description of current task AI is working on")
    last_task_update: datetime | None = Field(default=None, description="When the task was last updated")
    recent_commands: list[CommandRecord] = Field(default_factory=list, description="Recent commands sent to this worktree")
    notes: str | None = Field(default=None, description="Additional notes or context")
    token_usage: TokenUsage = Field(default_factory=TokenUsage, description="Token usage for this worktree's AI sessions")
    created_at: datetime = Field(default_factory=datetime.now, description="When this status record was created")
    updated_at: datetime = Field(default_factory=datetime.now, description="When this status was last updated")

    def add_command(
        self, command: str, source_worktree: str | None = None, pane_index: int = 0, window_index: int = 0, max_history: int = 20
    ) -> None:
        """Add a command to the recent commands history."""
        record = CommandRecord(
            timestamp=datetime.now(),
            command=command,
            source_worktree=source_worktree,
            pane_index=pane_index,
            window_index=window_index,
        )
        self.recent_commands.append(record)
        self.updated_at = datetime.now()

        if len(self.recent_commands) > max_history:
            self.recent_commands = self.recent_commands[-max_history:]

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

    def update_token_usage(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        """Update token usage by adding to existing counts."""
        self.token_usage.input_tokens += input_tokens
        self.token_usage.output_tokens += output_tokens
        self.token_usage.cache_read_tokens += cache_read_tokens
        self.token_usage.cache_write_tokens += cache_write_tokens
        self.token_usage.last_updated = datetime.now()
        self.updated_at = datetime.now()

    def reset_token_usage(self) -> None:
        """Reset token usage to zero."""
        self.token_usage = TokenUsage()
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
    total_commands_sent: int = Field(default=0, ge=0, description="Total commands sent across all worktrees")
    total_input_tokens: int = Field(default=0, ge=0, description="Total input tokens across all worktrees")
    total_output_tokens: int = Field(default=0, ge=0, description="Total output tokens across all worktrees")
    total_estimated_cost_usd: float = Field(default=0.0, ge=0, description="Total estimated cost in USD")
    most_recent_activity: datetime | None = Field(default=None, description="Most recent activity across all worktrees")
    statuses: list[WorktreeAIStatus] = Field(default_factory=list, description="Individual status for each worktree")


class StatusStore(BaseModel):
    """Persistent storage for worktree statuses."""

    version: str = Field(default="1.1", description="Storage format version")
    updated_at: datetime = Field(default_factory=datetime.now, description="When the store was last updated")
    statuses: dict[str, WorktreeAIStatus] = Field(default_factory=dict, description="Map of worktree name to status")

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

"""Pydantic models for subagent fork-join lifecycle.

Subagents are lightweight child agents spawned within an existing
worktree session. They run in tmux panes (not separate worktrees),
making them cheap to create and tear down.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SubagentRole(str, Enum):
    """Role classification for subagent specialization."""

    RESEARCH = "research"
    SYNTHESIS = "synthesis"
    CRITIC = "critic"
    WORKER = "worker"
    PLANNER = "planner"


class SubagentStatus(str, Enum):
    """Lifecycle status of a subagent."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class SubagentState(BaseModel):
    """Tracked state of a single subagent."""

    id: str = Field(description="Unique identifier (parent_name:role:index)")
    parent_name: str = Field(description="Name of the parent worktree/session")
    role: SubagentRole = Field(description="Role specialization")
    prompt: str = Field(description="Task prompt sent to the subagent")
    status: SubagentStatus = Field(default=SubagentStatus.PENDING)
    tmux_session: str | None = Field(default=None, description="tmux session hosting this subagent")
    tmux_pane_id: str | None = Field(default=None, description="tmux pane ID within the session")
    output: str | None = Field(default=None, description="Collected output from the subagent")
    error: str | None = Field(default=None, description="Error message if failed")
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    last_heartbeat: datetime | None = Field(default=None)
    timeout_seconds: int = Field(default=300, description="Max runtime before timeout")

    @property
    def is_terminal(self) -> bool:
        """Whether the subagent has reached a terminal state."""
        return self.status in (SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.TIMED_OUT)

    @property
    def elapsed_seconds(self) -> float:
        """Seconds since the subagent started (0 if not started)."""
        if not self.started_at:
            return 0.0
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()

    @property
    def is_timed_out(self) -> bool:
        """Whether the subagent has exceeded its timeout."""
        if self.status != SubagentStatus.RUNNING:
            return False
        return self.elapsed_seconds > self.timeout_seconds


class SubagentResult(BaseModel):
    """Collected result from a completed subagent."""

    id: str = Field(description="Subagent ID")
    role: SubagentRole = Field(description="Role of the subagent")
    status: SubagentStatus = Field(description="Terminal status")
    output: str = Field(default="", description="Collected output")
    elapsed_seconds: float = Field(default=0.0)


class ForkJoinRequest(BaseModel):
    """Specification for a fork-join operation."""

    parent_name: str = Field(description="Parent worktree or session name")
    agents: list[ForkSpec] = Field(description="Subagents to fork")
    timeout_seconds: int = Field(default=300, description="Global timeout for all agents")
    context: str = Field(default="", description="Shared context to inject into all agents")


class ForkSpec(BaseModel):
    """Specification for a single subagent to fork."""

    role: SubagentRole = Field(description="Role for the subagent")
    prompt: str = Field(description="Task prompt")
    timeout_seconds: int | None = Field(default=None, description="Override per-agent timeout")

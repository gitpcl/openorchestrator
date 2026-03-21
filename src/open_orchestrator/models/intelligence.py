"""Pydantic models for the Agno intelligence layer.

Structured output schemas used by AgnoPlanner, AgnoQualityGate,
and AgnoConflictResolver agents.
"""

from pydantic import BaseModel, Field


class PlannedTask(BaseModel):
    """A single task in an AI-generated plan."""

    id: str = Field(..., description="Short, descriptive task ID (lowercase, hyphens)")
    description: str = Field(..., description="Complete instruction an AI agent can act on")
    depends_on: list[str] = Field(default_factory=list, description="IDs of tasks that must complete first")
    estimated_files: list[str] = Field(default_factory=list, description="Files likely to be modified (overlap detection)")
    ai_tool: str = Field(default="claude", description="AI tool to use for this task")


class TaskPlan(BaseModel):
    """Structured output from the planner agent."""

    goal: str = Field(..., description="The original goal being decomposed")
    tasks: list[PlannedTask] = Field(..., min_length=1, max_length=12, description="Decomposed tasks")
    max_concurrent: int = Field(default=3, ge=1, le=8, description="Maximum parallel tasks")
    rationale: str = Field(..., description="Explains decomposition strategy and dependency choices")


class QualityVerdict(BaseModel):
    """Structured output from the quality gate agent."""

    score: float = Field(..., ge=0.0, le=1.0, description="Quality score")
    passed: bool = Field(..., description="Whether the changes pass the quality gate")
    summary: str = Field(..., description="One-line summary of the review")
    issues: list[str] = Field(default_factory=list, description="Identified issues")
    suggestions: list[str] = Field(default_factory=list, description="Improvement suggestions")
    cross_worktree_conflicts: list[str] = Field(
        default_factory=list,
        description="Potential conflicts with other active worktrees",
    )


class ConflictResolution(BaseModel):
    """Structured output from the conflict resolver agent."""

    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in the resolution")
    resolutions: dict[str, str] = Field(default_factory=dict, description="file_path -> resolved content")
    explanation: str = Field(..., description="Explanation of how conflicts were resolved")
    requires_human: bool = Field(..., description="Whether human review is recommended")


class CoordinationAction(BaseModel):
    """Structured output from the coordinator agent."""

    target_worktrees: list[str] = Field(..., description="Worktree names to send this message to")
    message: str = Field(..., description="Context message to inject into worktree CLAUDE.md")
    urgency: str = Field(default="info", description="info | warning | critical")
    rationale: str = Field(default="", description="Why this coordination is needed")

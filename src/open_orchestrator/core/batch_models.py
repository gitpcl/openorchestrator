"""Data models and validation for batch task execution.

Extracted from batch.py to keep file sizes manageable.
Provides BatchStatus, BatchTask, BatchResult, BatchConfig dataclasses
and Pydantic validation models for TOML parsing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BatchStatus(str, Enum):
    """Status of a batch task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SHIPPED = "shipped"
    FAILED = "failed"


@dataclass
class BatchTask:
    """A single task in a batch."""

    description: str
    id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    branch: str | None = None
    ai_tool: str = "claude"
    plan_mode: bool = False
    auto_ship: bool = False


@dataclass
class BatchResult:
    """Result of a batch task execution."""

    task: BatchTask
    worktree_name: str | None = None
    status: BatchStatus = BatchStatus.PENDING
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 1
    completion_summary: str | None = None
    parent_summaries: list[str] = field(default_factory=list)
    started_at: float | None = None  # time.monotonic() when task started
    ship_failed: bool = False  # True when work completed but merge failed


@dataclass
class BatchConfig:
    """Configuration for a batch run."""

    tasks: list[BatchTask] = field(default_factory=list)
    max_concurrent: int = 3
    auto_ship: bool = False
    poll_interval: int = 30  # seconds
    min_agent_runtime: int = 60


def _parse_tasks(data: dict[str, Any]) -> list[BatchTask]:
    """Parse BatchTask list from raw TOML data dict.

    Used by plan_tasks() to validate AI-generated TOML before writing.
    """
    batch_section = data.get("batch", {})
    return [
        BatchTask(
            description=t["description"],
            id=t.get("id"),
            depends_on=t.get("depends_on", []),
            branch=t.get("branch"),
            ai_tool=t.get("ai_tool", "claude"),
            plan_mode=t.get("plan_mode", False),
            auto_ship=t.get("auto_ship", batch_section.get("auto_ship", False)),
        )
        for t in data.get("tasks", [])
    ]


class BatchTaskModel(BaseModel):
    """Pydantic validation model for a single batch task entry."""

    model_config = ConfigDict(extra="forbid")

    description: str
    id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    branch: str | None = None
    ai_tool: str = "claude"
    plan_mode: bool = False
    auto_ship: bool = False


class BatchSectionModel(BaseModel):
    """Pydantic validation model for the [batch] section."""

    model_config = ConfigDict(extra="forbid")

    max_concurrent: int = 3
    auto_ship: bool = False
    poll_interval: int = 30
    min_agent_runtime: int = 60


class BatchFileModel(BaseModel):
    """Pydantic validation model for the entire batch TOML file."""

    model_config = ConfigDict(extra="forbid")

    batch: BatchSectionModel = Field(default_factory=BatchSectionModel)
    tasks: list[BatchTaskModel] = Field(default_factory=list)


def _batch_file_to_config(model: BatchFileModel) -> BatchConfig:
    """Convert validated BatchFileModel to runtime BatchConfig dataclass."""
    tasks = [
        BatchTask(
            description=t.description,
            id=t.id,
            depends_on=t.depends_on,
            branch=t.branch,
            ai_tool=t.ai_tool,
            plan_mode=t.plan_mode,
            auto_ship=t.auto_ship if t.auto_ship else model.batch.auto_ship,
        )
        for t in model.tasks
    ]
    return BatchConfig(
        tasks=tasks,
        max_concurrent=model.batch.max_concurrent,
        auto_ship=model.batch.auto_ship,
        poll_interval=model.batch.poll_interval,
        min_agent_runtime=model.batch.min_agent_runtime,
    )

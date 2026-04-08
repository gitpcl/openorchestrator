"""Pydantic models for swarm-mode multi-agent coordination.

A swarm is a coordinator agent plus N specialized workers that collaborate
on a single goal within one worktree. Swarm workers are implemented as
subagents (tmux panes in the coordinator's session), so fork/join is cheap.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SwarmRole(str, Enum):
    """Specialization roles for swarm members.

    - COORDINATOR: owns the goal, decomposes it, delegates to workers
    - RESEARCHER: gathers information and reports findings
    - IMPLEMENTER: writes production code to address the goal
    - REVIEWER: read-only review of implementer output
    - TESTER: writes and runs tests against implementer output
    """

    COORDINATOR = "coordinator"
    RESEARCHER = "researcher"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    TESTER = "tester"


class SwarmWorkerStatus(str, Enum):
    """Lifecycle status of a swarm worker."""

    PENDING = "pending"
    WORKING = "working"
    IDLE = "idle"
    DONE = "done"
    FAILED = "failed"


class SwarmWorker(BaseModel):
    """A single worker in a swarm (coordinator or specialist)."""

    id: str = Field(description="Unique worker id, e.g. 'swarm-abc:researcher:0'")
    role: SwarmRole = Field(description="Role assigned to this worker")
    prompt: str = Field(description="Role-specialized prompt")
    status: SwarmWorkerStatus = Field(default=SwarmWorkerStatus.PENDING)
    tmux_session: str | None = Field(default=None)
    tmux_pane_id: str | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=datetime.now)


class SwarmState(BaseModel):
    """Tracked state of a swarm (coordinator + workers)."""

    swarm_id: str = Field(description="Unique swarm identifier")
    goal: str = Field(description="High-level goal the swarm is pursuing")
    worktree: str = Field(description="Worktree the swarm runs in")
    coordinator_id: str = Field(description="Worker id of the coordinator")
    workers: list[SwarmWorker] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @property
    def worker_ids(self) -> list[str]:
        return [w.id for w in self.workers]

    @property
    def coordinator(self) -> SwarmWorker | None:
        for w in self.workers:
            if w.id == self.coordinator_id:
                return w
        return None

    @property
    def specialists(self) -> list[SwarmWorker]:
        return [w for w in self.workers if w.id != self.coordinator_id]

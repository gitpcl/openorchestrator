"""Autopilot loop orchestration for batch task execution.

Implements Karpathy-style autonomous loops: define a batch of tasks,
OWT creates worktrees, starts agents, monitors status, and auto-ships
completed work before starting the next task.

Usage:
    owt batch tasks.toml
    owt batch tasks.toml --auto-ship
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import toml

from open_orchestrator.config import AITool
from open_orchestrator.core.pane_actions import PaneActionError, create_pane
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)


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


@dataclass
class BatchConfig:
    """Configuration for a batch run."""

    tasks: list[BatchTask] = field(default_factory=list)
    max_concurrent: int = 3
    auto_ship: bool = False
    poll_interval: int = 30  # seconds


def load_batch_config(path: str | Path) -> BatchConfig:
    """Load batch configuration from a TOML file.

    Expected format:
        [batch]
        max_concurrent = 3
        auto_ship = true
        poll_interval = 30

        [[tasks]]
        description = "Add user authentication"
        branch = "feat/auth"

        [[tasks]]
        description = "Fix login redirect bug"
    """
    data = toml.load(str(path))

    batch_section = data.get("batch", {})
    tasks_data = data.get("tasks", [])

    tasks = [
        BatchTask(
            description=t["description"],
            branch=t.get("branch"),
            ai_tool=t.get("ai_tool", "claude"),
            plan_mode=t.get("plan_mode", False),
            auto_ship=t.get("auto_ship", batch_section.get("auto_ship", False)),
        )
        for t in tasks_data
    ]

    return BatchConfig(
        tasks=tasks,
        max_concurrent=batch_section.get("max_concurrent", 3),
        auto_ship=batch_section.get("auto_ship", False),
        poll_interval=batch_section.get("poll_interval", 30),
    )


class BatchRunner:
    """Orchestrates batch task execution with monitoring loop."""

    def __init__(self, config: BatchConfig, repo_path: str):
        self.config = config
        self.repo_path = repo_path
        self.results: list[BatchResult] = [
            BatchResult(task=t) for t in config.tasks
        ]
        self.tracker = StatusTracker()

    def run(self, on_status: Callable[[list[BatchResult]], None] | None = None) -> list[BatchResult]:
        """Execute all tasks with concurrency control.

        Args:
            on_status: Callback(results) called each poll cycle.

        Returns:
            List of BatchResult with final statuses.
        """
        pending = list(range(len(self.results)))
        running: list[int] = []

        while pending or running:
            # Start new tasks up to max_concurrent
            while pending and len(running) < self.config.max_concurrent:
                idx = pending.pop(0)
                self._start_task(idx)
                running.append(idx)

            # Poll running tasks (reload from disk to see hook-pushed updates)
            self.tracker.reload()
            still_running: list[int] = []
            for idx in running:
                result = self.results[idx]
                if result.worktree_name:
                    status = self.tracker.get_status(result.worktree_name)
                    if status and status.activity_status in (
                        AIActivityStatus.WAITING,
                        AIActivityStatus.COMPLETED,
                    ):
                        if result.task.auto_ship or self.config.auto_ship:
                            self._ship_task(idx)
                        else:
                            result.status = BatchStatus.COMPLETED
                    elif status and status.activity_status == AIActivityStatus.ERROR:
                        result.status = BatchStatus.FAILED
                        result.error = "Agent reported error"
                    else:
                        still_running.append(idx)
                else:
                    still_running.append(idx)

            running = still_running

            if on_status:
                on_status(self.results)

            if running:
                time.sleep(self.config.poll_interval)

        return self.results

    def _start_task(self, idx: int) -> None:
        """Start a single batch task."""
        result = self.results[idx]
        task = result.task

        from open_orchestrator.core.branch_namer import generate_branch_name

        branch = task.branch
        if not branch:
            try:
                branch = generate_branch_name(task.description)
            except ValueError:
                branch = f"batch/task-{idx}"

        try:
            pane = create_pane(
                session_name=f"batch-{idx}",
                repo_path=self.repo_path,
                branch=branch,
                ai_tool=AITool(task.ai_tool),
                plan_mode=task.plan_mode,
                ai_instructions=task.description,
            )
            result.worktree_name = pane.worktree_name
            result.status = BatchStatus.RUNNING
        except PaneActionError as e:
            result.status = BatchStatus.FAILED
            result.error = str(e)

    def _ship_task(self, idx: int) -> None:
        """Ship a completed batch task."""
        result = self.results[idx]
        if not result.worktree_name:
            return

        try:
            from open_orchestrator.core.merge import MergeManager

            merge_mgr = MergeManager()
            merge_mgr.merge(
                worktree_name=result.worktree_name,
                delete_worktree=True,
            )
            from open_orchestrator.core.tmux_manager import TmuxManager

            tmux = TmuxManager()
            session_name = tmux.generate_session_name(result.worktree_name)
            try:
                if tmux.session_exists(session_name):
                    tmux.kill_session(session_name)
            except Exception:
                pass
            self.tracker.remove_status(result.worktree_name)
            result.status = BatchStatus.SHIPPED
        except Exception as e:
            result.status = BatchStatus.COMPLETED
            result.error = f"Ship failed: {e}"

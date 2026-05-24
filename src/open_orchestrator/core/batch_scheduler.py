"""DAG scheduling for batch task execution.

Pure scheduling logic split out of ``batch.py``:

* ``build_task_index`` / ``validate_dag`` — topological-sort + ID indexing
* ``BatchScheduler`` — dependency resolution, ready-task selection,
  parent-context capture, and DAG progress metadata
* ``BatchStateStore`` — save/load the autopilot loop's persisted state

The autopilot loop and the AI planner remain in ``batch.py``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections import deque
from pathlib import Path

from open_orchestrator.core.batch_models import (
    BatchConfig,
    BatchResult,
    BatchStatus,
    BatchTask,
)
from open_orchestrator.core.merge import MergeManager
from open_orchestrator.core.status import StatusTracker

logger = logging.getLogger(__name__)

__all__ = [
    "BatchScheduler",
    "BatchStateStore",
    "build_task_index",
    "validate_dag",
]


# ─── DAG construction ──────────────────────────────────────────────────────


def build_task_index(tasks: list[BatchTask]) -> dict[str, int]:
    """Map task IDs to indices, auto-assigning IDs where missing."""
    index: dict[str, int] = {}
    for i, task in enumerate(tasks):
        if task.id is None:
            task.id = f"task-{i}"
        if task.id in index:
            raise ValueError(f"Duplicate task ID: {task.id!r}")
        index[task.id] = i
    return index


def validate_dag(tasks: list[BatchTask], index: dict[str, int]) -> list[int]:
    """Validate DAG has no cycles using Kahn's algorithm.

    Returns topological order as list of task indices.
    Raises ValueError on cycles or missing dependency references.
    """
    n = len(tasks)
    in_degree = [0] * n
    children: list[list[int]] = [[] for _ in range(n)]

    for i, task in enumerate(tasks):
        for dep_id in task.depends_on:
            if dep_id not in index:
                raise ValueError(f"Task {task.id!r} depends on unknown ID {dep_id!r}")
            parent_idx = index[dep_id]
            children[parent_idx].append(i)
            in_degree[i] += 1

    queue = deque(i for i in range(n) if in_degree[i] == 0)
    order: list[int] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if len(order) != n:
        cycle_ids = [tasks[i].id for i in range(n) if i not in set(order)]
        raise ValueError(f"Circular dependency detected among tasks: {cycle_ids}")
    return order


# ─── Scheduling state ──────────────────────────────────────────────────────


class BatchScheduler:
    """Dependency-aware scheduler for ``BatchRunner``.

    Owns the topological order, task-id index, and DAG progress metadata.
    All dependency-resolution decisions (``deps_satisfied``, ``deps_failed``,
    ``select_ready``) flow through here so the autopilot loop in
    ``batch.py`` stays free of scheduling internals.
    """

    def __init__(
        self,
        tasks: list[BatchTask],
        results: list[BatchResult],
        tracker: StatusTracker,
    ) -> None:
        self.results = results
        self.tracker = tracker
        self.task_index = build_task_index(tasks)
        self.topo_order = validate_dag(tasks, self.task_index)
        self.has_deps = any(t.depends_on for t in tasks)
        self._last_dag_progress = ""

    # ─── dependency resolution ──────────────────────────────────────────

    def deps_satisfied(self, idx: int) -> bool:
        """Check if all dependencies of a task are completed/shipped.

        Ship-failed tasks (work done, merge failed) also satisfy deps
        since the work exists in the branch.
        """
        task = self.results[idx].task
        for dep_id in task.depends_on:
            dep_idx = self.task_index[dep_id]
            dep_result = self.results[dep_idx]
            if dep_result.status in (BatchStatus.COMPLETED, BatchStatus.SHIPPED):
                continue
            if dep_result.status == BatchStatus.FAILED and dep_result.ship_failed:
                continue
            return False
        return True

    def deps_failed(self, idx: int) -> bool:
        """Check if any dependency's WORK failed (not just ship).

        Ship failures (work complete but merge failed) don't cascade — the
        work exists in the branch and dependents can still proceed.
        """
        task = self.results[idx].task
        for dep_id in task.depends_on:
            dep_idx = self.task_index[dep_id]
            dep_result = self.results[dep_idx]
            if dep_result.status == BatchStatus.FAILED and not dep_result.ship_failed:
                return True
        return False

    def select_ready(self, pending: list[int]) -> int | None:
        """Select next pending task whose deps are satisfied (mutates ``pending``)."""
        for i, idx in enumerate(pending):
            if self.deps_satisfied(idx):
                return pending.pop(i)
        return None

    # ─── parent-context propagation ─────────────────────────────────────

    def collect_parent_summaries(self, idx: int) -> None:
        """Collect parent task summaries onto ``self.results[idx].parent_summaries``."""
        task = self.results[idx].task
        summaries: list[str] = []
        for dep_id in task.depends_on:
            dep_idx = self.task_index[dep_id]
            dep_result = self.results[dep_idx]
            if dep_result.completion_summary:
                summaries.append(dep_result.completion_summary)
        self.results[idx].parent_summaries = summaries

    def capture_summary(
        self,
        worktree_name: str,
        merge_manager_factory: type | object,
    ) -> str | None:
        """Capture a task-aware summary from a completed worktree for DAG context.

        Includes task description, key file changes (diff stat), and commit count
        instead of raw git log output.
        """
        status = self.tracker.get_status(worktree_name)
        if not status:
            return None

        task_desc = status.current_task or "completed"
        for r in self.results:
            if r.worktree_name == worktree_name:
                task_desc = r.task.description
                break

        try:
            base_ref = resolve_base_ref(worktree_name, merge_manager_factory)
            cwd = status.worktree_path

            log_result = subprocess.run(
                ["git", "rev-list", "--count", f"{base_ref}..{status.branch}"],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=5,
            )
            commit_count = log_result.stdout.strip() if log_result.returncode == 0 else "?"

            diff_result = subprocess.run(
                ["git", "diff", "--stat", "--stat-width=60", f"{base_ref}..{status.branch}"],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=5,
            )
            diff_stat = diff_result.stdout.strip() if diff_result.returncode == 0 else ""

            parts = [
                f"## Completed Parent Task: {worktree_name}",
                f"**Branch:** {status.branch}",
                f"**Task:** {task_desc}",
            ]
            if diff_stat:
                parts.append(f"**Key changes:**\n{diff_stat}")
            parts.append(f"**Status:** {commit_count} commit(s)")
            return "\n".join(parts)

        except (subprocess.TimeoutExpired, OSError):
            logger.debug("Failed to capture summary for %s", worktree_name, exc_info=True)
        return f"## Completed Parent Task: {worktree_name}\n**Task:** {task_desc}"

    # ─── DAG progress metadata ──────────────────────────────────────────

    def update_progress(self, completed: int, total: int) -> None:
        """Write DAG progress to SQLite metadata table (only on change)."""
        if not self.has_deps:
            return
        progress = f"{completed}/{total}"
        if progress != self._last_dag_progress:
            self.tracker.set_metadata("dag_progress", progress)
            self._last_dag_progress = progress

    def clear_progress(self) -> None:
        """Remove DAG progress from metadata."""
        self.tracker.delete_metadata("dag_progress")
        self._last_dag_progress = ""


def resolve_base_ref(worktree_name: str, merge_manager_factory: object) -> str:
    """Resolve the base ref used for runtime commit detection."""
    merge_mgr: MergeManager = merge_manager_factory()  # type: ignore[operator]
    wt = merge_mgr.wt_manager.get(worktree_name)
    return merge_mgr.get_base_branch(wt.branch)


# ─── State persistence ─────────────────────────────────────────────────────


class BatchStateStore:
    """Serialize / deserialize ``BatchRunner`` state for resume."""

    STATE_FILENAME = ".owt-batch-state.json"

    @classmethod
    def state_path(cls, repo_path: str) -> Path:
        return Path(repo_path) / cls.STATE_FILENAME

    @classmethod
    def save(cls, repo_path: str, config: BatchConfig, results: list[BatchResult]) -> None:
        state = {
            "repo_path": repo_path,
            "config": {
                "max_concurrent": config.max_concurrent,
                "auto_ship": config.auto_ship,
                "poll_interval": config.poll_interval,
                "min_agent_runtime": config.min_agent_runtime,
            },
            "results": [
                {
                    "task": {
                        "description": r.task.description,
                        "id": r.task.id,
                        "depends_on": r.task.depends_on,
                        "branch": r.task.branch,
                        "ai_tool": r.task.ai_tool,
                        "plan_mode": r.task.plan_mode,
                        "auto_ship": r.task.auto_ship,
                    },
                    "worktree_name": r.worktree_name,
                    "status": r.status.value,
                    "error": r.error,
                    "retry_count": r.retry_count,
                    "started_at": r.started_at,
                    "ship_failed": r.ship_failed,
                }
                for r in results
            ],
        }
        cls.state_path(repo_path).write_text(json.dumps(state, indent=2))

    @classmethod
    def load(cls, repo_path: str) -> tuple[BatchConfig, list[BatchResult]]:
        state_path = cls.state_path(repo_path)
        if not state_path.exists():
            raise FileNotFoundError(f"No batch state found at {state_path}")

        data = json.loads(state_path.read_text())
        config = BatchConfig(
            tasks=[],
            max_concurrent=data["config"]["max_concurrent"],
            auto_ship=data["config"]["auto_ship"],
            poll_interval=data["config"]["poll_interval"],
            min_agent_runtime=data["config"]["min_agent_runtime"],
        )

        results: list[BatchResult] = []
        tasks: list[BatchTask] = []
        for r in data["results"]:
            task = BatchTask(**r["task"])
            tasks.append(task)
            results.append(
                BatchResult(
                    task=task,
                    worktree_name=r.get("worktree_name"),
                    status=BatchStatus(r["status"]),
                    error=r.get("error"),
                    retry_count=r.get("retry_count", 0),
                    started_at=r.get("started_at"),
                    ship_failed=r.get("ship_failed", False),
                )
            )

        config.tasks = tasks
        return config, results

    @classmethod
    def clear(cls, repo_path: str) -> None:
        cls.state_path(repo_path).unlink(missing_ok=True)

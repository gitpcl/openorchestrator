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
from collections import deque
from collections.abc import Callable
from pathlib import Path

import toml

from open_orchestrator.config import AITool
from open_orchestrator.core.batch_models import (
    BatchConfig,
    BatchFileModel,
    BatchResult,
    BatchStatus,
    BatchTask,
    _batch_file_to_config,
    _parse_tasks,
)
from open_orchestrator.core.merge import MergeManager
from open_orchestrator.core.pane_actions import PaneActionError, build_agent_prompt, create_pane
from open_orchestrator.core.runtime import RuntimeOutcome, TaskRuntimeCoordinator
from open_orchestrator.core.status import StatusTracker, runtime_status_config
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)

# Re-export model types for backward compatibility
__all__ = [
    "BatchConfig",
    "BatchFileModel",
    "BatchResult",
    "BatchRunner",
    "BatchStatus",
    "BatchTask",
    "_batch_file_to_config",
    "_parse_tasks",
    "_build_task_index",
    "_validate_dag",
    "load_batch_config",
    "plan_tasks",
]


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

    Raises ValidationError on unknown keys or invalid values.
    """
    data = toml.load(str(path))
    model = BatchFileModel(**data)
    return _batch_file_to_config(model)


def _extract_toml(text: str) -> str:
    """Extract TOML content from a fenced code block in AI output."""
    import re

    match = re.search(r"```toml\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: try unfenced TOML (starts with [batch] or [[tasks]])
    match = re.search(r"(\[batch\].*)", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"(\[\[tasks\]\].*)", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    raise ValueError("No TOML block found in AI output")


def plan_tasks(
    goal: str,
    repo_path: str,
    ai_tool: str = "claude",
    output_path: str | Path | None = None,
) -> Path:
    """Use an AI tool to decompose a goal into a dependency-aware task DAG.

    Tries Agno-powered planner first (if installed and enabled), then falls
    back to subprocess-based AI tool invocation.

    Args:
        goal: The feature/goal description to decompose.
        repo_path: Path to the repository for context.
        ai_tool: AI tool to use for planning (default: claude).
        output_path: Where to write the TOML file. Defaults to plan.toml in repo.

    Returns:
        Path to the generated TOML file.

    Raises:
        ValueError: If the AI output cannot be parsed as valid TOML.
        RuntimeError: If the AI tool fails to run.
    """
    import subprocess

    try:
        from open_orchestrator.config import load_config

        config = load_config()
        if config.agno.enabled:
            from open_orchestrator.core.intelligence import AgnoPlanner

            return AgnoPlanner(config.agno, repo_path=repo_path).plan(goal, repo_path, output_path, ai_tool)
    except ImportError:
        logger.debug("Agno not installed, falling back to subprocess planner")
    except Exception as e:
        logger.warning("Agno planner failed, falling back: %s", e)

    output_path = Path(output_path) if output_path else Path(repo_path) / "plan.toml"

    prompt = f"""You are a software architect. Decompose this goal into parallel tasks for AI coding agents.

GOAL: {goal}

Output ONLY a valid TOML file with this exact format. Each task gets its own worktree and AI agent.
Tasks with no dependencies run in parallel. Use `depends_on` to specify ordering.

```toml
[batch]
max_concurrent = 3
auto_ship = true

[[tasks]]
id = "unique-id"
description = "Clear, actionable task description for an AI coding agent"
ai_tool = "{ai_tool}"
depends_on = []

[[tasks]]
id = "another-id"
description = "Another task"
ai_tool = "{ai_tool}"
depends_on = ["unique-id"]
```

Rules:
- Each task should be completable independently in its own git branch
- Keep tasks focused (1-3 files each)
- Maximize parallelism — only add depends_on when truly needed
- Use short, descriptive IDs (lowercase, hyphens)
- 3-8 tasks is ideal
- Description should be a complete instruction an AI agent can act on
- Every task MUST have ai_tool = "{ai_tool}"
"""

    cmd = [ai_tool, "--print", "-p", prompt]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Planning timed out after 5 minutes")
    except FileNotFoundError:
        raise RuntimeError(f"AI tool '{ai_tool}' not found. Ensure it's installed and in PATH.")

    stdout = result.stdout
    stderr = result.stderr

    if result.returncode != 0:
        raise RuntimeError(f"AI tool failed: {stderr or 'unknown error'}")

    toml_text = _extract_toml(stdout)

    # Validate in-memory before writing
    try:
        data = toml.loads(toml_text)
        tasks = _parse_tasks(data)
        index = _build_task_index(list(tasks))
        _validate_dag(list(tasks), index)
    except Exception as e:
        raise ValueError(f"AI generated invalid task plan: {e}") from e

    output_path.write_text(toml_text)
    return output_path


def _build_task_index(tasks: list[BatchTask]) -> dict[str, int]:
    """Map task IDs to indices, auto-assigning IDs where missing."""
    index: dict[str, int] = {}
    for i, task in enumerate(tasks):
        if task.id is None:
            task.id = f"task-{i}"
        if task.id in index:
            raise ValueError(f"Duplicate task ID: {task.id!r}")
        index[task.id] = i
    return index


def _validate_dag(tasks: list[BatchTask], index: dict[str, int]) -> list[int]:
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

    # Kahn's algorithm
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


class BatchRunner:
    """Orchestrates batch task execution with monitoring loop."""

    def __init__(
        self,
        config: BatchConfig,
        repo_path: str,
        tracker: StatusTracker | None = None,
        tmux: TmuxManager | None = None,
        merge_manager_factory: Callable[[], MergeManager] | None = None,
        results: list[BatchResult] | None = None,
    ):
        self.config = config
        self.repo_path = repo_path
        self.results: list[BatchResult] = results or [BatchResult(task=t) for t in config.tasks]
        self.tracker = tracker or StatusTracker(runtime_status_config(repo_path))
        self._tmux = tmux or TmuxManager()
        self._merge_manager_factory = merge_manager_factory or (lambda: MergeManager(repo_path=Path(self.repo_path)))
        self._runtime = TaskRuntimeCoordinator(
            tmux=self._tmux,
            merge_manager_factory=self._merge_manager_factory,
        )
        self._task_index = _build_task_index(config.tasks)
        self._topo_order = _validate_dag(config.tasks, self._task_index)
        self._has_deps = any(t.depends_on for t in config.tasks)
        self._last_dag_progress = ""

    @staticmethod
    def _state_path(repo_path: str) -> Path:
        return Path(repo_path) / ".owt-batch-state.json"

    def _save_state(self) -> None:
        """Persist current batch results to JSON for resume."""
        import json

        state = {
            "repo_path": self.repo_path,
            "config": {
                "max_concurrent": self.config.max_concurrent,
                "auto_ship": self.config.auto_ship,
                "poll_interval": self.config.poll_interval,
                "min_agent_runtime": self.config.min_agent_runtime,
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
                for r in self.results
            ],
        }
        self._state_path(self.repo_path).write_text(json.dumps(state, indent=2))

    @classmethod
    def resume(cls, repo_path: str) -> BatchRunner:
        """Resume a batch run from saved state."""
        import json

        state_path = cls._state_path(repo_path)
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
        runner = cls(config, repo_path, results=results)

        # Clean up state file
        state_path.unlink(missing_ok=True)
        return runner

    def _deps_satisfied(self, idx: int) -> bool:
        """Check if all dependencies of a task are completed/shipped.

        Ship-failed tasks (work done, merge failed) also satisfy deps
        since the work exists in the branch.
        """
        task = self.results[idx].task
        for dep_id in task.depends_on:
            dep_idx = self._task_index[dep_id]
            dep_result = self.results[dep_idx]
            if dep_result.status in (BatchStatus.COMPLETED, BatchStatus.SHIPPED):
                continue
            if dep_result.status == BatchStatus.FAILED and dep_result.ship_failed:
                continue
            return False
        return True

    def _deps_failed(self, idx: int) -> bool:
        """Check if any dependency's WORK failed (not just ship).

        Ship failures (work complete but merge failed) don't cascade — the
        work exists in the branch and dependents can still proceed.
        """
        task = self.results[idx].task
        for dep_id in task.depends_on:
            dep_idx = self._task_index[dep_id]
            dep_result = self.results[dep_idx]
            if dep_result.status == BatchStatus.FAILED and not dep_result.ship_failed:
                return True
        return False

    def _select_ready(self, pending: list[int]) -> int | None:
        """Select next pending task whose deps are satisfied."""
        for i, idx in enumerate(pending):
            if self._deps_satisfied(idx):
                return pending.pop(i)
        return None

    def _update_dag_progress(self, completed: int, total: int) -> None:
        """Write DAG progress to SQLite metadata table (only on change)."""
        if not self._has_deps:
            return
        progress = f"{completed}/{total}"
        if progress != self._last_dag_progress:
            self.tracker.set_metadata("dag_progress", progress)
            self._last_dag_progress = progress

    def _clear_dag_progress(self) -> None:
        """Remove DAG progress from metadata."""
        self.tracker.delete_metadata("dag_progress")
        self._last_dag_progress = ""

    def run(self, on_status: Callable[[list[BatchResult]], None] | None = None) -> list[BatchResult]:
        """Execute all tasks with concurrency control and dependency ordering.

        Args:
            on_status: Callback(results) called each poll cycle.

        Returns:
            List of BatchResult with final statuses.
        """
        pending = list(self._topo_order)
        running: list[int] = []
        total = len(self.results)

        while pending or running:
            # Cascade failures: mark tasks with failed parents
            still_pending: list[int] = []
            for idx in pending:
                if self._deps_failed(idx):
                    self.results[idx].status = BatchStatus.FAILED
                    self.results[idx].error = "Parent task failed"
                else:
                    still_pending.append(idx)
            pending = still_pending

            # Start new tasks up to max_concurrent (only if deps satisfied)
            while pending and len(running) < self.config.max_concurrent:
                next_idx = self._select_ready(pending)
                if next_idx is None:
                    break  # No task has deps satisfied yet
                # Inject parent context before starting
                if self._has_deps:
                    self._inject_parent_context(next_idx)
                self._start_task(next_idx)
                running.append(next_idx)

            # Deadlock detection: nothing running, pending tasks exist but none ready
            if not running and pending:
                for idx in pending:
                    self.results[idx].status = BatchStatus.FAILED
                    self.results[idx].error = "Deadlock: dependencies cannot be satisfied"
                pending.clear()

            # Poll running tasks (reload from disk to see hook-pushed updates)
            self.tracker.reload()
            still_running: list[int] = []
            for idx in running:
                result = self.results[idx]
                if result.worktree_name:
                    status = self.tracker.get_status(result.worktree_name)
                    if not status:
                        still_running.append(idx)
                    elif status.activity_status in (
                        AIActivityStatus.WAITING,
                        AIActivityStatus.COMPLETED,
                    ):
                        # Capture summary before shipping (for child context)
                        if self._has_deps:
                            result.completion_summary = self._capture_summary(result.worktree_name)
                        if result.task.auto_ship or self.config.auto_ship:
                            self._ship_task(idx)
                        else:
                            self.tracker.mark_completed(result.worktree_name)
                            result.status = BatchStatus.COMPLETED
                    elif status.activity_status == AIActivityStatus.ERROR:
                        result.status = BatchStatus.FAILED
                        result.error = "Agent reported error"
                    else:
                        task_elapsed = time.monotonic() - result.started_at if result.started_at else 0
                        try:
                            base_ref = self._resolve_task_base_ref(result.worktree_name)
                        except Exception as e:
                            self._handle_batch_failure(
                                idx,
                                f"Base ref resolution failed: {e}",
                            )
                            continue
                        decision = self._runtime.evaluate_completion(
                            worktree_name=result.worktree_name,
                            base_ref=base_ref,
                            session_name=self._tmux.generate_session_name(result.worktree_name),
                            elapsed_seconds=task_elapsed,
                            activity_status=status.activity_status,
                            startup_grace_period=self.config.poll_interval,
                            min_agent_runtime=self.config.min_agent_runtime,
                        )
                        if decision.outcome == RuntimeOutcome.RUNNING:
                            still_running.append(idx)
                        elif decision.outcome == RuntimeOutcome.COMPLETED:
                            if self._has_deps:
                                result.completion_summary = self._capture_summary(result.worktree_name)
                            if result.task.auto_ship or self.config.auto_ship:
                                self._ship_task(idx)
                            else:
                                self.tracker.mark_completed(result.worktree_name)
                                result.status = BatchStatus.COMPLETED
                        else:
                            self._handle_batch_failure(
                                idx,
                                decision.reason or "Task failed",
                            )
                else:
                    still_running.append(idx)

            running = still_running

            # Update DAG progress
            done = sum(1 for r in self.results if r.status in (BatchStatus.COMPLETED, BatchStatus.SHIPPED, BatchStatus.FAILED))
            self._update_dag_progress(done, total)

            self._save_state()

            if on_status:
                on_status(self.results)

            if running:
                time.sleep(self.config.poll_interval)

        self._clear_dag_progress()
        # Clean up state file on successful completion
        self._state_path(self.repo_path).unlink(missing_ok=True)
        return self.results

    def _capture_summary(self, worktree_name: str) -> str | None:
        """Capture a task-aware summary from a completed worktree for DAG context.

        Includes task description, key file changes (diff stat), and commit count
        instead of raw git log output.
        """
        import subprocess

        status = self.tracker.get_status(worktree_name)
        if not status:
            return None

        # Find the matching task for the description
        task_desc = status.current_task or "completed"
        for r in self.results:
            if r.worktree_name == worktree_name:
                task_desc = r.task.description
                break

        try:
            base_ref = self._resolve_task_base_ref(worktree_name)
            cwd = status.worktree_path

            # Get commit count
            log_result = subprocess.run(
                ["git", "rev-list", "--count", f"{base_ref}..{status.branch}"],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=5,
            )
            commit_count = log_result.stdout.strip() if log_result.returncode == 0 else "?"

            # Get diff stat (file changes summary)
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

    def _inject_parent_context(self, idx: int) -> None:
        """Collect parent summaries and prepare them for injection after pane creation."""
        task = self.results[idx].task
        summaries: list[str] = []
        for dep_id in task.depends_on:
            dep_idx = self._task_index[dep_id]
            dep_result = self.results[dep_idx]
            if dep_result.completion_summary:
                summaries.append(dep_result.completion_summary)
        self.results[idx].parent_summaries = summaries

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
            try:
                ai_tool_enum = AITool(task.ai_tool)
            except ValueError:
                result.status = BatchStatus.FAILED
                result.error = f"Unknown ai_tool: {task.ai_tool!r}"
                return
            retry_context = None
            if result.retry_count > 0 and result.error:
                from open_orchestrator.core.prompt_builder import build_retry_context

                retry_context = build_retry_context(result.retry_count, result.max_retries, result.error)
            pane = create_pane(
                session_name=f"batch-{idx}",
                repo_path=self.repo_path,
                branch=branch,
                ai_tool=ai_tool_enum,
                plan_mode=task.plan_mode,
                ai_instructions=build_agent_prompt(task.description, retry_context),
                display_task=task.description,
                status_tracker=self.tracker,
            )
            result.worktree_name = pane.worktree_name
            result.status = BatchStatus.RUNNING
            result.started_at = time.monotonic()

            # Inject parent context into the worktree's CLAUDE.md
            parent_summaries = result.parent_summaries
            if parent_summaries:
                from open_orchestrator.core.environment import inject_dag_context

                try:
                    inject_dag_context(pane.worktree_path, parent_summaries)
                except Exception as e:
                    logger.warning("DAG context injection failed: %s", e)
        except PaneActionError as e:
            result.status = BatchStatus.FAILED
            result.error = str(e)

    def _handle_batch_failure(self, idx: int, reason: str) -> None:
        """Handle a batch task failure with optional retry."""
        result = self.results[idx]
        result.error = reason
        if result.retry_count < result.max_retries:
            result.retry_count += 1
            logger.info(
                "Task %d failed (%s) — retrying (%d/%d)",
                idx,
                reason,
                result.retry_count,
                result.max_retries,
            )
            if result.worktree_name:
                from open_orchestrator.core.pane_actions import teardown_worktree

                teardown_worktree(result.worktree_name, repo_path=self.repo_path)
                self.tracker.remove_status(result.worktree_name)
            result.worktree_name = None
            result.status = BatchStatus.PENDING
        else:
            result.status = BatchStatus.FAILED
            logger.warning("Task %d failed permanently: %s", idx, reason)

    def _ship_task(self, idx: int) -> None:
        """Ship a completed batch task."""
        result = self.results[idx]
        if not result.worktree_name:
            return

        try:
            from open_orchestrator.core.tmux_manager import TmuxManager

            # Kill tmux session BEFORE merge (agent may hold locks)
            tmux = TmuxManager()
            session_name = tmux.generate_session_name(result.worktree_name)
            try:
                if tmux.session_exists(session_name):
                    tmux.kill_session(session_name)
            except Exception:
                logger.debug("Failed to kill tmux session %s", session_name, exc_info=True)

            # Auto-commit any uncommitted work (safety net for agents
            # that create files but exit before committing)
            merge_mgr = self._merge_manager_factory()
            merge_mgr.auto_commit_worktree(result.worktree_name)

            # Guard: refuse to ship if branch has no new commits
            wt = merge_mgr.wt_manager.get(result.worktree_name)
            base = merge_mgr.get_base_branch(wt.branch)
            commits = merge_mgr.count_commits_ahead(wt.branch, base)
            if commits == 0:
                self._handle_batch_failure(idx, "No commits produced — nothing to ship")
                return

            merge_mgr.merge(
                worktree_name=result.worktree_name,
                delete_worktree=True,
            )
            self.tracker.remove_status(result.worktree_name)
            result.status = BatchStatus.SHIPPED
        except Exception as e:
            # Mark as ship-failed (work done, merge failed) — don't cascade
            result.status = BatchStatus.FAILED
            result.ship_failed = True
            result.error = f"Ship failed: {e}"
            logger.warning(
                "Task %s completed but merge failed — manual merge needed: owt merge %s",
                result.task.id or idx,
                result.worktree_name,
            )

    def _resolve_task_base_ref(self, worktree_name: str) -> str:
        """Resolve the base ref used for runtime commit detection."""
        merge_mgr = self._merge_manager_factory()
        wt = merge_mgr.wt_manager.get(worktree_name)
        return merge_mgr.get_base_branch(wt.branch)

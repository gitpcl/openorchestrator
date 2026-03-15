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
    completion_summary: str | None = None
    parent_summaries: list[str] = field(default_factory=list)


@dataclass
class BatchConfig:
    """Configuration for a batch run."""

    tasks: list[BatchTask] = field(default_factory=list)
    max_concurrent: int = 3
    auto_ship: bool = False
    poll_interval: int = 30  # seconds


def _parse_tasks(data: dict, batch_section: dict | None = None) -> list[BatchTask]:
    """Parse BatchTask list from TOML data dict."""
    if batch_section is None:
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
    tasks = _parse_tasks(data, batch_section)

    return BatchConfig(
        tasks=tasks,
        max_concurrent=batch_section.get("max_concurrent", 3),
        auto_ship=batch_section.get("auto_ship", False),
        poll_interval=batch_section.get("poll_interval", 30),
    )


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

    Runs the AI tool in non-interactive mode to generate a TOML batch file
    with task IDs and dependency relationships.

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
            capture_output=True, text=True, cwd=repo_path, timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Planning timed out after 5 minutes")
    except FileNotFoundError:
        raise RuntimeError(
            f"AI tool '{ai_tool}' not found. Ensure it's installed and in PATH."
        )

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
                raise ValueError(
                    f"Task {task.id!r} depends on unknown ID {dep_id!r}"
                )
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
        raise ValueError(
            f"Circular dependency detected among tasks: {cycle_ids}"
        )
    return order


class BatchRunner:
    """Orchestrates batch task execution with monitoring loop."""

    def __init__(self, config: BatchConfig, repo_path: str):
        self.config = config
        self.repo_path = repo_path
        self.results: list[BatchResult] = [
            BatchResult(task=t) for t in config.tasks
        ]
        self.tracker = StatusTracker()
        self._task_index = _build_task_index(config.tasks)
        self._topo_order = _validate_dag(config.tasks, self._task_index)
        self._has_deps = any(t.depends_on for t in config.tasks)
        self._last_dag_progress = ""

    def _deps_satisfied(self, idx: int) -> bool:
        """Check if all dependencies of a task are completed/shipped."""
        task = self.results[idx].task
        for dep_id in task.depends_on:
            dep_idx = self._task_index[dep_id]
            dep_status = self.results[dep_idx].status
            if dep_status not in (BatchStatus.COMPLETED, BatchStatus.SHIPPED):
                return False
        return True

    def _deps_failed(self, idx: int) -> bool:
        """Check if any dependency of a task has failed."""
        task = self.results[idx].task
        for dep_id in task.depends_on:
            dep_idx = self._task_index[dep_id]
            if self.results[dep_idx].status == BatchStatus.FAILED:
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
                idx = self._select_ready(pending)
                if idx is None:
                    break  # No task has deps satisfied yet
                # Inject parent context before starting
                if self._has_deps:
                    self._inject_parent_context(idx)
                self._start_task(idx)
                running.append(idx)

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
                    if status and status.activity_status in (
                        AIActivityStatus.WAITING,
                        AIActivityStatus.COMPLETED,
                    ):
                        # Capture summary before shipping (for child context)
                        if self._has_deps:
                            result.completion_summary = self._capture_summary(
                                result.worktree_name
                            )
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

            # Update DAG progress
            done = sum(
                1 for r in self.results
                if r.status in (BatchStatus.COMPLETED, BatchStatus.SHIPPED, BatchStatus.FAILED)
            )
            self._update_dag_progress(done, total)

            if on_status:
                on_status(self.results)

            if running:
                time.sleep(self.config.poll_interval)

        self._clear_dag_progress()
        return self.results

    def _capture_summary(self, worktree_name: str) -> str | None:
        """Capture a git log summary from a completed worktree for context passing."""
        import subprocess

        status = self.tracker.get_status(worktree_name)
        if not status:
            return None
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-10", f"main..{status.branch}"],
                capture_output=True, text=True,
                cwd=status.worktree_path, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return f"**{worktree_name}** ({status.branch}):\n{result.stdout.strip()}"
        except (subprocess.TimeoutExpired, OSError):
            pass
        # Fallback: use task description
        return f"**{worktree_name}**: {status.current_task or 'completed'}"

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
            pane = create_pane(
                session_name=f"batch-{idx}",
                repo_path=self.repo_path,
                branch=branch,
                ai_tool=ai_tool_enum,
                plan_mode=task.plan_mode,
                ai_instructions=(
                    task.description
                    + "\n\nIMPORTANT: When done, use /commit to commit your changes."
                ),
            )
            result.worktree_name = pane.worktree_name
            result.status = BatchStatus.RUNNING

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

    def _ship_task(self, idx: int) -> None:
        """Ship a completed batch task."""
        result = self.results[idx]
        if not result.worktree_name:
            return

        try:
            from open_orchestrator.core.merge import MergeManager
            from open_orchestrator.core.tmux_manager import TmuxManager

            # Kill tmux session BEFORE merge (agent may hold locks)
            tmux = TmuxManager()
            session_name = tmux.generate_session_name(result.worktree_name)
            try:
                if tmux.session_exists(session_name):
                    tmux.kill_session(session_name)
            except Exception:
                pass

            # Auto-commit any uncommitted work before merging
            merge_mgr = MergeManager(repo_path=Path(self.repo_path))
            dirty = merge_mgr.check_uncommitted_changes(result.worktree_name)
            if dirty:
                from git import Repo

                wt = merge_mgr.wt_manager.get(result.worktree_name)
                wt_repo = Repo(wt.path)
                wt_repo.git.add("-A")
                branch_desc = wt.branch.split("/")[-1].replace("-", " ")
                wt_repo.git.commit("-m", f"feat: {branch_desc}")

            merge_mgr.merge(
                worktree_name=result.worktree_name,
                delete_worktree=True,
            )
            self.tracker.remove_status(result.worktree_name)
            result.status = BatchStatus.SHIPPED
        except Exception as e:
            result.status = BatchStatus.FAILED
            result.error = f"Ship failed: {e}"

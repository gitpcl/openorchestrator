"""Autopilot loop orchestration for batch task execution.

Implements Karpathy-style autonomous loops: define a batch of tasks,
OWT creates worktrees, starts agents, monitors status, and auto-ships
completed work before starting the next task.

DAG scheduling and state persistence live in
:mod:`open_orchestrator.core.batch_scheduler`. This module owns the
autopilot loop and the AI planner.

Usage:
    owt batch tasks.toml
    owt batch tasks.toml --auto-ship
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import toml

from open_orchestrator.core import status_policy
from open_orchestrator.core.batch_models import (
    PLAN_PROMPT_TEMPLATE,
    BatchConfig,
    BatchFileModel,
    BatchResult,
    BatchStatus,
    BatchTask,
    _batch_file_to_config,
    _parse_tasks,
)
from open_orchestrator.core.batch_scheduler import (
    BatchScheduler,
    BatchStateStore,
    build_task_index,
    resolve_base_ref,
    validate_dag,
)
from open_orchestrator.core.merge import MergeManager
from open_orchestrator.core.pane_actions import PaneActionError, build_agent_prompt, create_pane
from open_orchestrator.core.runtime import RuntimeOutcome, TaskRuntimeCoordinator
from open_orchestrator.core.status import StatusTracker, runtime_status_config
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.core.tool_registry import get_registry
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)

# Backward-compat aliases — the leading underscore names are the
# pre-Phase-8 public surface used by ``intelligence.py``, ``orchestrator.py``
# and ``tests/test_batch_dag.py``. New code should import from
# ``batch_scheduler`` directly.
_build_task_index = build_task_index
_validate_dag = validate_dag

# Re-export model types for backward compatibility
__all__ = [
    "BatchConfig",
    "BatchFileModel",
    "BatchResult",
    "BatchRunner",
    "BatchStatus",
    "BatchTask",
    "_batch_file_to_config",
    "_build_task_index",
    "_parse_tasks",
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
    except Exception as e:  # noqa: BLE001 — Agno is an external optional dep; any failure must fall through to subprocess planner
        logger.warning("Agno planner failed, falling back to subprocess: %s", e, exc_info=True)

    output_path = Path(output_path) if output_path else Path(repo_path) / "plan.toml"

    prompt = PLAN_PROMPT_TEMPLATE.format(goal=goal, ai_tool=ai_tool)
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

    try:
        data = toml.loads(toml_text)
        tasks = _parse_tasks(data)
        index = build_task_index(list(tasks))
        validate_dag(list(tasks), index)
    except (toml.TomlDecodeError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"AI generated invalid task plan: {e}") from e

    output_path.write_text(toml_text)
    return output_path


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
        self._scheduler = BatchScheduler(config.tasks, self.results, self.tracker)

    # ─── state persistence ─────────────────────────────────────────────

    @staticmethod
    def _state_path(repo_path: str) -> Path:
        return BatchStateStore.state_path(repo_path)

    def _save_state(self) -> None:
        BatchStateStore.save(self.repo_path, self.config, self.results)

    @classmethod
    def resume(cls, repo_path: str) -> BatchRunner:
        """Resume a batch run from saved state."""
        config, results = BatchStateStore.load(repo_path)
        runner = cls(config, repo_path, results=results)
        BatchStateStore.clear(repo_path)
        return runner

    # ─── autopilot loop ────────────────────────────────────────────────

    def run(self, on_status: Callable[[list[BatchResult]], None] | None = None) -> list[BatchResult]:
        """Execute all tasks with concurrency control and dependency ordering.

        Args:
            on_status: Callback(results) called each poll cycle.

        Returns:
            List of BatchResult with final statuses.
        """
        pending = list(self._scheduler.topo_order)
        running: list[int] = []
        total = len(self.results)

        while pending or running:
            # Cascade failures: mark tasks with failed parents
            still_pending: list[int] = []
            for idx in pending:
                if self._scheduler.deps_failed(idx):
                    self.results[idx].status = BatchStatus.FAILED
                    self.results[idx].error = "Parent task failed"
                else:
                    still_pending.append(idx)
            pending = still_pending

            # Start new tasks up to max_concurrent (only if deps satisfied)
            while pending and len(running) < self.config.max_concurrent:
                next_idx = self._scheduler.select_ready(pending)
                if next_idx is None:
                    break
                if self._scheduler.has_deps:
                    self._scheduler.collect_parent_summaries(next_idx)
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
            running = self._poll_running_tasks(running)

            done = sum(1 for r in self.results if r.status in (BatchStatus.COMPLETED, BatchStatus.SHIPPED, BatchStatus.FAILED))
            self._scheduler.update_progress(done, total)

            self._save_state()

            if on_status:
                on_status(self.results)

            if running:
                time.sleep(self.config.poll_interval)

        self._scheduler.clear_progress()
        BatchStateStore.clear(self.repo_path)
        return self.results

    # ─── per-task lifecycle ────────────────────────────────────────────

    def _poll_running_tasks(self, running: list[int]) -> list[int]:
        """Poll running tasks and return those still running."""
        still_running: list[int] = []
        for idx in running:
            result = self.results[idx]
            if not result.worktree_name:
                still_running.append(idx)
                continue
            status = self.tracker.get_status(result.worktree_name)
            if not status:
                still_running.append(idx)
            elif status_policy.is_terminal(status.activity_status):
                if status.activity_status == AIActivityStatus.ERROR:
                    result.status = BatchStatus.FAILED
                    result.error = "Agent reported error"
                else:
                    self._complete_task(idx, result)
            else:
                self._evaluate_running_task(idx, result, status, still_running)
        return still_running

    def _complete_task(self, idx: int, result: BatchResult) -> None:
        """Handle task completion: capture summary, ship or mark completed."""
        wt_name = result.worktree_name or ""
        if self._scheduler.has_deps:
            result.completion_summary = self._scheduler.capture_summary(wt_name, self._merge_manager_factory)
        if result.task.auto_ship or self.config.auto_ship:
            self._ship_task(idx)
        else:
            self.tracker.mark_completed(wt_name)
            result.status = BatchStatus.COMPLETED

    def _evaluate_running_task(
        self,
        idx: int,
        result: BatchResult,
        status: object,
        still_running: list[int],
    ) -> None:
        """Evaluate a still-running task using the runtime coordinator."""
        from open_orchestrator.core.merge import MergeError
        from open_orchestrator.core.worktree import WorktreeError

        wt_name = result.worktree_name or ""
        task_elapsed = time.monotonic() - result.started_at if result.started_at else 0
        try:
            base_ref = resolve_base_ref(wt_name, self._merge_manager_factory)
        except (MergeError, WorktreeError, OSError, ValueError) as e:
            logger.exception("Base ref resolution failed for %s: %s", wt_name, e)
            self._handle_batch_failure(idx, f"Base ref resolution failed: {e}")
            return
        decision = self._runtime.evaluate_completion(
            worktree_name=wt_name,
            base_ref=base_ref,
            session_name=self._tmux.generate_session_name(wt_name),
            elapsed_seconds=task_elapsed,
            activity_status=status.activity_status,  # type: ignore[attr-defined]
            startup_grace_period=self.config.poll_interval,
            min_agent_runtime=self.config.min_agent_runtime,
        )
        if decision.outcome == RuntimeOutcome.RUNNING:
            still_running.append(idx)
        elif decision.outcome == RuntimeOutcome.COMPLETED:
            self._complete_task(idx, result)
        else:
            self._handle_batch_failure(idx, decision.reason or "Task failed")

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
            if get_registry().get(task.ai_tool) is None:
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
                ai_tool=task.ai_tool,
                plan_mode=task.plan_mode,
                ai_instructions=build_agent_prompt(task.description, retry_context),
                display_task=task.description,
                status_tracker=self.tracker,
            )
            result.worktree_name = pane.worktree_name
            result.status = BatchStatus.RUNNING
            result.started_at = time.monotonic()

            parent_summaries = result.parent_summaries
            if parent_summaries:
                from open_orchestrator.core.environment import inject_dag_context

                try:
                    inject_dag_context(pane.worktree_path, parent_summaries)
                except OSError as e:
                    logger.exception("DAG context injection failed: %s", e)
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
        from open_orchestrator.core.merge import MergeError
        from open_orchestrator.core.tmux_manager import TmuxError, TmuxManager
        from open_orchestrator.core.worktree import WorktreeError

        result = self.results[idx]
        if not result.worktree_name:
            return

        try:
            # Kill tmux session BEFORE merge (agent may hold locks)
            tmux = TmuxManager()
            session_name = tmux.generate_session_name(result.worktree_name)
            try:
                if tmux.session_exists(session_name):
                    tmux.kill_session(session_name)
            except (TmuxError, OSError):
                logger.exception("Failed to kill tmux session %s", session_name)

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
        except (MergeError, WorktreeError, TmuxError, PaneActionError, OSError, ValueError) as e:
            # Mark as ship-failed (work done, merge failed) — don't cascade
            result.status = BatchStatus.FAILED
            result.ship_failed = True
            result.error = f"Ship failed: {e}"
            logger.exception(
                "Task %s completed but merge failed — manual merge needed: owt merge %s",
                result.task.id or idx,
                result.worktree_name,
            )

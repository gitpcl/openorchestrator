"""Orchestrator agent for end-to-end plan execution.

Takes a plan.toml, drives it: creates worktrees, coordinates agents,
merges completed tasks into a feature branch for review, persists state
for stop/resume, and pauses when the user jumps into a worktree.

Usage:
    owt plan "Add JWT auth" --start --branch feat/auth-v2
    owt orchestrate plan.toml --branch feat/auth-v2
    owt orchestrate --resume
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from open_orchestrator.config import AgnoConfig, AITool, load_config
from open_orchestrator.core.batch import (
    _build_task_index,
    _parse_tasks,
    _validate_dag,
    load_batch_config,
)
from open_orchestrator.core.merge import MergeManager
from open_orchestrator.core.pane_actions import PaneActionError, build_agent_prompt, create_pane, teardown_worktree
from open_orchestrator.core.runtime import RuntimeOutcome, TaskRuntimeCoordinator
from open_orchestrator.core.status import (
    StatusTracker,
    default_status_path,
    runtime_status_config,
)
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)


# ─── State Models ──────────────────────────────────────────────────────────


class TaskPhase(str, Enum):
    """Typed lifecycle phases for orchestrator tasks."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SHIPPED = "shipped"
    FAILED = "failed"


class TaskState(BaseModel):
    """Per-task persistent state within the orchestrator."""

    id: str
    description: str
    depends_on: list[str] = []
    status: TaskPhase = TaskPhase.PENDING
    worktree_name: str | None = None
    branch: str | None = None
    retry_count: int = 0
    max_retries: int = 1
    failure_reason: str | None = None
    started_at: str | None = None
    last_heartbeat: str | None = None


class OrchestratorState(BaseModel):
    """Full orchestrator state — persisted to JSON each tick."""

    goal: str
    feature_branch: str
    repo_path: str
    plan_path: str
    max_concurrent: int = 3
    poll_interval: int = 30
    default_max_retries: int = 1
    default_task_timeout: int = 1800  # 30 minutes
    min_agent_runtime: int = 60  # Minimum seconds before declaring completion
    tasks: list[TaskState]
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ─── Orchestrator ──────────────────────────────────────────────────────────


def _load_agno_config() -> AgnoConfig | None:
    """Load Agno config, returning None if unavailable."""
    try:
        return load_config().agno
    except (OSError, ValueError, KeyError) as e:
        logger.debug("Agno config unavailable, intelligence features disabled: %s", e)
        return None


class Orchestrator:
    """Drives a plan end-to-end: start tasks, merge into feature branch, coordinate."""

    def __init__(
        self,
        state: OrchestratorState,
        agno_config: AgnoConfig | None = None,
        tracker: StatusTracker | None = None,
        tmux: TmuxManager | None = None,
        merge_manager_factory: Callable[[], MergeManager] | None = None,
    ):
        self.state = state
        self.agno_config = agno_config
        self.tracker = tracker or StatusTracker(runtime_status_config(state.repo_path))
        self.tmux = tmux or TmuxManager()
        self._merge_manager_factory = merge_manager_factory or (lambda: MergeManager(repo_path=Path(self.state.repo_path)))
        self._runtime = TaskRuntimeCoordinator(
            tmux=self.tmux,
            merge_manager_factory=self._merge_manager_factory,
        )
        self._task_index: dict[str, int] = {t.id: i for i, t in enumerate(state.tasks)}
        self._cooldowns: dict[str, float] = {}
        self._coordination_cooldown = 120  # seconds

    # ─── Constructors ──────────────────────────────────────────────────

    @classmethod
    def from_plan(
        cls,
        plan_path: str | Path,
        goal: str,
        feature_branch: str,
        repo_path: str,
        max_concurrent: int = 3,
        poll_interval: int = 30,
    ) -> Orchestrator:
        """Create orchestrator from a plan.toml file."""
        plan_path = Path(plan_path)
        config = load_batch_config(str(plan_path))

        tasks = [
            TaskState(
                id=t.id or f"task-{i}",
                description=t.description,
                depends_on=t.depends_on,
            )
            for i, t in enumerate(config.tasks)
        ]

        # Validate DAG
        batch_tasks = _parse_tasks(
            {"tasks": [{"id": t.id, "description": t.description, "depends_on": t.depends_on} for t in tasks]}
        )
        index = _build_task_index(list(batch_tasks))
        _validate_dag(list(batch_tasks), index)

        state = OrchestratorState(
            goal=goal,
            feature_branch=feature_branch,
            repo_path=repo_path,
            plan_path=str(plan_path),
            max_concurrent=max_concurrent,
            poll_interval=poll_interval,
            tasks=tasks,
        )

        orch = cls(state, agno_config=_load_agno_config())
        orch._save_state()
        return orch

    @classmethod
    def resume(cls, repo_path: str | None = None) -> Orchestrator:
        """Resume from saved state file."""
        state_path = cls._state_path(repo_path)
        if not state_path.exists():
            raise FileNotFoundError(f"No orchestrator state found at {state_path}")

        state = OrchestratorState.model_validate_json(state_path.read_text())
        orch = cls(state, agno_config=_load_agno_config())
        orch._reconcile_world_state()
        return orch

    # ─── Main Loop ─────────────────────────────────────────────────────

    def run(self, on_status: Callable[[OrchestratorState], None] | None = None) -> OrchestratorState:
        """Main orchestration loop."""
        self._ensure_feature_branch()

        try:
            while not self._all_done():
                self.tracker.reload()
                self._start_ready_tasks()
                self._poll_running_tasks()
                self._coordinate()
                self._save_state()
                if on_status:
                    on_status(self.state)
                time.sleep(self.state.poll_interval)
        except KeyboardInterrupt:
            self._save_state()
            logger.info("Orchestrator paused. Resume with: owt orchestrate --resume")

        return self.state

    def stop(self) -> None:
        """Graceful stop — save state, don't kill worktrees."""
        self._save_state()
        logger.info("Orchestrator stopped. Resume with: owt orchestrate --resume")

    # ─── Feature Branch ────────────────────────────────────────────────

    def _ensure_feature_branch(self) -> None:
        """Create feature branch from main if it doesn't exist."""
        from git import Repo
        from git.exc import GitCommandError

        repo = Repo(self.state.repo_path)
        try:
            repo.git.rev_parse("--verify", self.state.feature_branch)
            logger.info("Feature branch '%s' already exists", self.state.feature_branch)
        except GitCommandError:
            merge_mgr = MergeManager(Path(self.state.repo_path))
            base = merge_mgr.get_base_branch(self.state.feature_branch)
            repo.git.branch(self.state.feature_branch, base)
            logger.info("Created feature branch '%s' from '%s'", self.state.feature_branch, base)

    # ─── Reconciliation ────────────────────────────────────────────────

    def _reconcile_world_state(self) -> None:
        """Check running tasks against real-world state on resume.

        For each task marked as 'running':
        - If tmux session is dead and worktree has commits → mark completed
        - If tmux session is dead and no commits → mark failed
        - If tmux session is alive → leave as running
        """
        for task in self.state.tasks:
            if task.status != TaskPhase.RUNNING or not task.worktree_name:
                continue

            session_name = self.tmux.generate_session_name(task.worktree_name)
            session_alive = self.tmux.session_exists(session_name)

            if session_alive:
                continue

            # tmux is dead — check if the agent produced work
            try:
                inspection = self._runtime.inspect_worktree_commits(task.worktree_name, self.state.feature_branch)
                has_commits = inspection.has_commits
            except Exception:
                logger.debug("Failed to inspect commits for task '%s'", task.id, exc_info=True)
                has_commits = False

            if has_commits:
                task.status = TaskPhase.COMPLETED
                logger.warning(
                    "Reconciliation: task '%s' tmux dead but has commits → completed",
                    task.id,
                )
            else:
                task.status = TaskPhase.FAILED
                task.failure_reason = "Agent exited without producing commits (detected on resume)"
                logger.warning(
                    "Reconciliation: task '%s' tmux dead, no commits → failed",
                    task.id,
                )

        self._save_state()

    # ─── Task Scheduling ──────────────────────────────────────────────

    def _deps_satisfied(self, task: TaskState) -> bool:
        for dep_id in task.depends_on:
            idx = self._task_index.get(dep_id)
            if idx is None:
                return False
            if self.state.tasks[idx].status not in ("completed", "shipped"):
                return False
        return True

    def _deps_failed(self, task: TaskState) -> bool:
        for dep_id in task.depends_on:
            idx = self._task_index.get(dep_id)
            if idx is not None and self.state.tasks[idx].status == TaskPhase.FAILED:
                return True
        return False

    def _running_count(self) -> int:
        return sum(1 for t in self.state.tasks if t.status == TaskPhase.RUNNING)

    def _start_ready_tasks(self) -> None:
        """Start pending tasks whose dependencies are satisfied."""
        running = self._running_count()
        for task in self.state.tasks:
            if running >= self.state.max_concurrent:
                break
            if task.status != TaskPhase.PENDING:
                continue
            if self._deps_failed(task):
                task.status = TaskPhase.FAILED
                continue
            if not self._deps_satisfied(task):
                continue
            self._start_task(task)
            if task.status == TaskPhase.RUNNING:  # type: ignore[comparison-overlap]
                running += 1

    def _start_task(self, task: TaskState) -> None:
        """Start a single task by creating a worktree + agent."""
        from open_orchestrator.core.branch_namer import generate_branch_name

        branch = task.branch
        if not branch:
            try:
                branch = generate_branch_name(task.description)
            except ValueError:
                branch = f"orchestrator/{task.id}"

        try:
            retry_context = None
            if task.retry_count > 0 and task.failure_reason:
                from open_orchestrator.core.prompt_builder import build_retry_context

                retry_context = build_retry_context(task.retry_count, task.max_retries, task.failure_reason)
            pane = create_pane(
                session_name=f"orch-{task.id}",
                repo_path=self.state.repo_path,
                branch=branch,
                base_branch=self.state.feature_branch,
                ai_tool=AITool.CLAUDE,
                ai_instructions=build_agent_prompt(task.description, retry_context),
                display_task=task.description,
                status_tracker=self.tracker,
            )
            task.worktree_name = pane.worktree_name
            task.branch = pane.branch
            task.status = TaskPhase.RUNNING
            task.started_at = datetime.now(timezone.utc).isoformat()
            logger.info("Started task '%s' in worktree '%s'", task.id, pane.worktree_name)
        except PaneActionError as e:
            task.status = TaskPhase.FAILED
            logger.error("Failed to start task '%s': %s", task.id, e)

    # ─── Polling ───────────────────────────────────────────────────────

    def _poll_running_tasks(self) -> None:
        """Check running tasks for completion, merge into feature branch."""
        now = datetime.now(timezone.utc).isoformat()
        for task in self.state.tasks:
            if task.status != TaskPhase.RUNNING or not task.worktree_name:
                continue
            task.last_heartbeat = now

            # Check user presence — skip auto-actions if user is attached
            if self._user_in_worktree(task.worktree_name):
                logger.debug("User present in '%s', skipping auto-actions", task.worktree_name)
                continue

            # Timeout check — fail tasks that exceed the time limit
            if task.started_at:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(task.started_at)).total_seconds()
                if elapsed > self.state.default_task_timeout:
                    self._handle_task_failure(
                        task,
                        f"Timed out after {int(elapsed)}s",
                    )
                    continue

            # Calculate elapsed time for this task
            elapsed = 0.0
            if task.started_at:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(task.started_at)).total_seconds()

            status = self.tracker.get_status(task.worktree_name)
            if not status:
                continue

            decision = self._runtime.evaluate_completion(
                worktree_name=task.worktree_name,
                base_ref=self.state.feature_branch,
                session_name=self.tmux.generate_session_name(task.worktree_name),
                elapsed_seconds=elapsed,
                activity_status=status.activity_status,
                startup_grace_period=self.state.poll_interval,
                min_agent_runtime=self.state.min_agent_runtime,
            )
            if decision.outcome == RuntimeOutcome.RUNNING:
                continue

            if decision.outcome == RuntimeOutcome.COMPLETED:
                logger.info(
                    "Task '%s' completed (%s) in '%s' after %ds",
                    task.id,
                    decision.classification,
                    task.worktree_name,
                    int(elapsed),
                )
                task.status = TaskPhase.COMPLETED
                self._merge_to_feature_branch(task)
                continue

            self._handle_task_failure(task, decision.reason or "Task failed")

        # Progress tracking: update status with latest commit message
        self._update_running_progress()

    def _handle_task_failure(self, task: TaskState, reason: str) -> None:
        """Handle a task failure with optional retry.

        If retries remain, tears down the worktree and resets to pending.
        Otherwise marks as permanently failed.
        """
        task.failure_reason = reason
        if task.retry_count < task.max_retries:
            task.retry_count += 1
            logger.info(
                "Task '%s' failed (%s) — retrying (%d/%d)",
                task.id,
                reason,
                task.retry_count,
                task.max_retries,
            )
            # Tear down the failed worktree so a fresh one is created on retry
            if task.worktree_name:
                teardown_worktree(task.worktree_name, repo_path=self.state.repo_path)
                self.tracker.remove_status(task.worktree_name)
            task.worktree_name = None
            task.branch = None
            task.started_at = None
            task.status = TaskPhase.PENDING
        else:
            task.status = TaskPhase.FAILED
            logger.warning("Task '%s' failed permanently: %s", task.id, reason)

    def _check_worktree_has_commits(self, task: TaskState) -> bool:
        """Check if a worktree has commits, auto-committing any loose files.

        Runs directly in the worktree repo (not the main repo) to avoid
        branch resolution issues with count_commits_ahead.
        """
        if not task.worktree_name:
            return False
        try:
            inspection = self._runtime.inspect_worktree_commits(
                task.worktree_name,
                self.state.feature_branch,
            )
            return inspection.has_commits
        except Exception as e:
            logger.warning(
                "Commit check failed for '%s': %s",
                task.worktree_name,
                e,
            )
            return False

    def _merge_to_feature_branch(self, task: TaskState) -> None:
        """Merge a completed task's worktree into the feature branch."""
        if not task.worktree_name:
            return

        try:
            # Kill tmux session before merge
            session_name = self.tmux.generate_session_name(task.worktree_name)
            try:
                if self.tmux.session_exists(session_name):
                    self.tmux.kill_session(session_name)
            except Exception:
                logger.debug("Failed to kill tmux session for task %s", task.id, exc_info=True)

            # Auto-commit uncommitted work (safety net for agents that
            # create files but exit before committing)
            merge_mgr = self._merge_manager_factory()
            merge_mgr.auto_commit_worktree(task.worktree_name)

            # Quality gate (if Agno is available)
            if self.agno_config and self.agno_config.enabled:
                try:
                    from open_orchestrator.core.intelligence import AgnoQualityGate

                    wt = merge_mgr.wt_manager.get(task.worktree_name)
                    diff = merge_mgr.repo.git.diff(f"{self.state.feature_branch}...{wt.branch}")
                    if diff:
                        gate = AgnoQualityGate(
                            self.agno_config,
                            repo_path=self.state.repo_path,
                        )
                        verdict = gate.review(
                            diff=diff,
                            task_description=task.description,
                        )
                        if not verdict.passed:
                            self._handle_task_failure(
                                task,
                                f"Quality gate ({verdict.score:.1f}): {verdict.summary}",
                            )
                            return
                except ImportError:
                    pass
                except Exception as e:
                    logger.debug("Quality gate skipped: %s", e)

            # Guard: refuse to ship if branch has no new commits
            wt = merge_mgr.wt_manager.get(task.worktree_name)
            commits = merge_mgr.count_commits_ahead(wt.branch, self.state.feature_branch)
            if commits == 0:
                self._handle_task_failure(task, "No commits produced")
                return

            merge_mgr.merge(
                worktree_name=task.worktree_name,
                base_branch=self.state.feature_branch,
                delete_worktree=True,
            )
            self.tracker.remove_status(task.worktree_name)
            task.status = TaskPhase.SHIPPED
            logger.info("Shipped task '%s' (%d commits) into '%s'", task.id, commits, self.state.feature_branch)
        except Exception as e:
            self._handle_task_failure(task, f"Merge failed: {e}")

    # ─── Progress ──────────────────────────────────────────────────────

    def _update_running_progress(self) -> None:
        """Poll git log for running tasks and push latest commit to status tracker."""
        import subprocess

        try:
            merge_mgr = self._merge_manager_factory()
        except Exception:
            return

        for task in self.state.tasks:
            if task.status != "running" or not task.worktree_name or not task.branch:
                continue
            try:
                wt = merge_mgr.wt_manager.get(task.worktree_name)
                result = subprocess.run(
                    ["git", "log", "--oneline", "-1", f"{self.state.feature_branch}..{task.branch}"],
                    capture_output=True,
                    text=True,
                    cwd=str(wt.path),
                    timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    self.tracker.update_task(
                        task.worktree_name,
                        result.stdout.strip()[:100],
                    )
            except Exception:
                logger.debug("Task description extraction failed for %s", task.id, exc_info=True)

    # ─── User Presence ─────────────────────────────────────────────────

    def _user_in_worktree(self, worktree_name: str) -> bool:
        """Check if user has a tmux client attached to this worktree's session."""
        info = self.tmux.get_session_for_worktree(worktree_name)
        return info.attached if info else False

    # ─── Coordination ──────────────────────────────────────────────────

    def _coordinate(self) -> None:
        """Detect cross-worktree events and push context."""
        running_tasks = [t for t in self.state.tasks if t.status == TaskPhase.RUNNING and t.worktree_name]
        if len(running_tasks) < 2:
            return

        # Detect file overlaps between running worktrees
        events: list[tuple[str, str, list[str]]] = []  # (event_key, message, target_worktrees)
        merge_mgr = MergeManager(repo_path=Path(self.state.repo_path))

        for task in running_tasks:
            if not task.worktree_name:
                continue
            try:
                overlaps = merge_mgr.check_file_overlaps(
                    task.worktree_name,
                    self.state.feature_branch,
                )
                for file_path, other_wts in overlaps.items():
                    event_key = f"overlap:{file_path}"
                    if self._in_cooldown(event_key):
                        continue
                    targets = [task.worktree_name] + other_wts
                    who = ", ".join(targets)
                    msg = (
                        f"[WARNING] File conflict detected: `{file_path}`\n"
                        f"Also being modified by: {who}\n"
                        f"Limit your changes to sections the other agents aren't touching."
                    )
                    events.append((event_key, msg, targets))
            except Exception as e:
                logger.debug("File overlap check failed for %s: %s", task.worktree_name, e)
                continue

        if not events:
            return

        # Try Agno coordinator for richer context, fall back to template messages
        coordination_messages: dict[str, list[str]] = {}  # worktree -> messages

        if self.agno_config and self.agno_config.enabled:
            try:
                from open_orchestrator.core.intelligence import AgnoCoordinator

                coordinator = AgnoCoordinator(self.agno_config, repo_path=self.state.repo_path)
                running_context = [
                    {"name": t.worktree_name or "", "task": t.description, "branch": t.branch or ""} for t in running_tasks
                ]
                actions = coordinator.analyze(
                    events=[(key, msg) for key, msg, _ in events],
                    running_worktrees=running_context,
                )
                for action in actions:
                    for wt_name in action.target_worktrees:
                        coordination_messages.setdefault(wt_name, []).append(f"[{action.urgency.upper()}] {action.message}")
            except ImportError:
                logger.debug("Agno not available, using template coordination")
            except Exception as e:
                logger.warning("Agno coordinator failed: %s", e)

        # Fallback: use template messages for events not covered by Agno
        if not coordination_messages:
            for event_key, msg, targets in events:
                for wt_name in targets:
                    coordination_messages.setdefault(wt_name, []).append(msg)

        # Inject messages and set cooldowns
        from open_orchestrator.core.environment import inject_coordination_context

        for wt_name, messages in coordination_messages.items():
            # Only inject into WORKING worktrees
            status = self.tracker.get_status(wt_name)
            if not status or status.activity_status != AIActivityStatus.WORKING:
                continue
            try:
                inject_coordination_context(status.worktree_path, messages)
            except Exception as e:
                logger.debug("Coordination injection failed for %s: %s", wt_name, e)

        for event_key, _, _ in events:
            self._set_cooldown(event_key)

    def _in_cooldown(self, event_key: str) -> bool:
        expires = self._cooldowns.get(event_key, 0)
        return time.time() < expires

    def _set_cooldown(self, event_key: str) -> None:
        self._cooldowns[event_key] = time.time() + self._coordination_cooldown

    # ─── State Persistence ─────────────────────────────────────────────

    def _save_state(self) -> None:
        """Persist state to disk."""
        self.state.updated_at = datetime.now(timezone.utc).isoformat()
        path = self._state_path(self.state.repo_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.state.model_dump_json(indent=2))

    @staticmethod
    def _state_path(repo_path: str | None = None) -> Path:
        repo_name = Path(repo_path or ".").resolve().name
        state_root = runtime_status_config(repo_path).storage_path or default_status_path()
        return state_root.parent / f"orchestrator-{repo_name}.json"

    def _all_done(self) -> bool:
        return all(t.status in ("shipped", "failed") for t in self.state.tasks)

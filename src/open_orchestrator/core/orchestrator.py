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
from open_orchestrator.core.pane_actions import PaneActionError, create_pane
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)


# ─── State Models ──────────────────────────────────────────────────────────


class TaskState(BaseModel):
    """Per-task persistent state within the orchestrator."""

    id: str
    description: str
    depends_on: list[str] = []
    status: str = "pending"  # pending | running | completed | shipped | failed
    worktree_name: str | None = None
    branch: str | None = None


class OrchestratorState(BaseModel):
    """Full orchestrator state — persisted to JSON each tick."""

    goal: str
    feature_branch: str
    repo_path: str
    plan_path: str
    max_concurrent: int = 3
    poll_interval: int = 30
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

    def __init__(self, state: OrchestratorState, agno_config: AgnoConfig | None = None):
        self.state = state
        self.agno_config = agno_config
        self.tracker = StatusTracker()
        self.tmux = TmuxManager()
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
        batch_tasks = _parse_tasks({"tasks": [
            {"id": t.id, "description": t.description, "depends_on": t.depends_on}
            for t in tasks
        ]})
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
        return cls(state, agno_config=_load_agno_config())

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
            if idx is not None and self.state.tasks[idx].status == "failed":
                return True
        return False

    def _running_count(self) -> int:
        return sum(1 for t in self.state.tasks if t.status == "running")

    def _start_ready_tasks(self) -> None:
        """Start pending tasks whose dependencies are satisfied."""
        running = self._running_count()
        for task in self.state.tasks:
            if running >= self.state.max_concurrent:
                break
            if task.status != "pending":
                continue
            if self._deps_failed(task):
                task.status = "failed"
                continue
            if not self._deps_satisfied(task):
                continue
            self._start_task(task)
            if task.status == "running":
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
            pane = create_pane(
                session_name=f"orch-{task.id}",
                repo_path=self.state.repo_path,
                branch=branch,
                ai_tool=AITool.CLAUDE,
                ai_instructions=(
                    task.description
                    + "\n\nIMPORTANT: When you have completed all changes:"
                    + "\n1. Stage and commit: git add -A && git commit -m 'feat: <description>'"
                    + "\n2. Exit immediately: /exit"
                ),
            )
            task.worktree_name = pane.worktree_name
            task.branch = pane.branch
            task.status = "running"
            logger.info("Started task '%s' in worktree '%s'", task.id, pane.worktree_name)
        except PaneActionError as e:
            task.status = "failed"
            logger.error("Failed to start task '%s': %s", task.id, e)

    # ─── Polling ───────────────────────────────────────────────────────

    def _poll_running_tasks(self) -> None:
        """Check running tasks for completion, merge into feature branch."""
        for task in self.state.tasks:
            if task.status != "running" or not task.worktree_name:
                continue

            # Check user presence — skip auto-actions if user is attached
            if self._user_in_worktree(task.worktree_name):
                logger.debug("User present in '%s', skipping auto-actions", task.worktree_name)
                continue

            status = self.tracker.get_status(task.worktree_name)
            if not status:
                continue

            if status.activity_status in (AIActivityStatus.WAITING, AIActivityStatus.COMPLETED):
                task.status = "completed"
                logger.info("Task '%s' completed in '%s'", task.id, task.worktree_name)
                self._merge_to_feature_branch(task)

            elif status.activity_status == AIActivityStatus.ERROR:
                task.status = "failed"
                logger.warning("Task '%s' errored in '%s'", task.id, task.worktree_name)

            elif status.activity_status == AIActivityStatus.WORKING:
                # Fallback: if status is WORKING but the AI process has exited
                # (hook failed to fire), detect via tmux pane inspection.
                session_name = self.tmux.generate_session_name(task.worktree_name)
                if not self.tmux.is_ai_running_in_session(session_name):
                    task.status = "completed"
                    logger.info(
                        "Task '%s' completed (process exited) in '%s'",
                        task.id, task.worktree_name,
                    )
                    self._merge_to_feature_branch(task)

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
                pass

            # Auto-commit uncommitted work
            merge_mgr = MergeManager(repo_path=Path(self.state.repo_path))
            dirty = merge_mgr.check_uncommitted_changes(task.worktree_name)
            if dirty:
                from git import Repo

                wt = merge_mgr.wt_manager.get(task.worktree_name)
                wt_repo = Repo(wt.path)
                wt_repo.git.add("-A")
                branch_desc = wt.branch.split("/")[-1].replace("-", " ")
                wt_repo.git.commit("-m", f"feat: {branch_desc}")

            merge_mgr.merge(
                worktree_name=task.worktree_name,
                base_branch=self.state.feature_branch,
                delete_worktree=True,
            )
            self.tracker.remove_status(task.worktree_name)
            task.status = "shipped"
            logger.info("Shipped task '%s' into '%s'", task.id, self.state.feature_branch)
        except Exception as e:
            logger.error("Merge failed for task '%s': %s", task.id, e)
            task.status = "failed"

    # ─── User Presence ─────────────────────────────────────────────────

    def _user_in_worktree(self, worktree_name: str) -> bool:
        """Check if user has a tmux client attached to this worktree's session."""
        info = self.tmux.get_session_for_worktree(worktree_name)
        return info.attached if info else False

    # ─── Coordination ──────────────────────────────────────────────────

    def _coordinate(self) -> None:
        """Detect cross-worktree events and push context."""
        running_tasks = [t for t in self.state.tasks if t.status == "running" and t.worktree_name]
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
                    task.worktree_name, self.state.feature_branch,
                )
                for file_path, other_wts in overlaps.items():
                    event_key = f"overlap:{file_path}"
                    if self._in_cooldown(event_key):
                        continue
                    targets = [task.worktree_name] + other_wts
                    who = ", ".join(targets)
                    msg = f"[WARNING] File '{file_path}' is being modified by: {who}. Coordinate to avoid conflicts."
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
                    {"name": t.worktree_name or "", "task": t.description, "branch": t.branch or ""}
                    for t in running_tasks
                ]
                actions = coordinator.analyze(
                    events=[(key, msg) for key, msg, _ in events],
                    running_worktrees=running_context,
                )
                for action in actions:
                    for wt_name in action.target_worktrees:
                        coordination_messages.setdefault(wt_name, []).append(
                            f"[{action.urgency.upper()}] {action.message}"
                        )
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
        return Path.home() / ".open-orchestrator" / f"orchestrator-{repo_name}.json"

    def _all_done(self) -> bool:
        return all(t.status in ("shipped", "failed") for t in self.state.tasks)

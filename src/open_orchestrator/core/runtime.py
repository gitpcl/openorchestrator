"""Shared runtime evaluation for orchestrated agent tasks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from git import Repo

from open_orchestrator.core.merge import MergeManager
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)


class RuntimeOutcome(str, Enum):
    """High-level result of evaluating a running task."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class CommitInspection:
    """Details about commits detected in a worktree."""

    base_ref: str
    commit_count: int
    auto_committed_files: int

    @property
    def has_commits(self) -> bool:
        return self.commit_count > 0


@dataclass(frozen=True)
class RuntimeDecision:
    """Result of evaluating a worktree's runtime state."""

    outcome: RuntimeOutcome
    classification: str
    elapsed_seconds: float
    reason: str | None = None
    commit_inspection: CommitInspection | None = None


class RuntimeEvaluationError(Exception):
    """Raised when runtime inspection fails for infrastructure reasons."""


class TaskRuntimeCoordinator:
    """Evaluate task runtime state consistently across orchestrators."""

    def __init__(
        self,
        tmux: TmuxManager,
        merge_manager_factory: Callable[[], MergeManager],
    ):
        self._tmux = tmux
        self._merge_manager_factory = merge_manager_factory

    def inspect_worktree_commits(
        self,
        worktree_name: str,
        base_ref: str,
    ) -> CommitInspection:
        """Check whether a worktree has commits relative to a base ref."""
        try:
            merge_mgr = self._merge_manager_factory()
            auto_committed_files = merge_mgr.auto_commit_worktree(worktree_name)
            wt = merge_mgr.wt_manager.get(worktree_name)
            wt_repo = Repo(wt.path)
            log = wt_repo.git.log("--oneline", f"{base_ref}..HEAD")
            commit_count = len(log.strip().splitlines()) if log.strip() else 0
            inspection = CommitInspection(
                base_ref=base_ref,
                commit_count=commit_count,
                auto_committed_files=auto_committed_files,
            )
            if inspection.has_commits:
                logger.info(
                    "Detected %d commit(s) in '%s' relative to '%s'",
                    inspection.commit_count,
                    worktree_name,
                    base_ref,
                )
            return inspection
        except Exception as e:
            raise RuntimeEvaluationError(
                f"commit inspection failed for '{worktree_name}' against '{base_ref}': {e}"
            ) from e

    def evaluate_completion(
        self,
        *,
        worktree_name: str,
        base_ref: str,
        session_name: str,
        elapsed_seconds: float,
        activity_status: AIActivityStatus,
        startup_grace_period: float,
        min_agent_runtime: float,
    ) -> RuntimeDecision:
        """Evaluate whether a running task should stay running, complete, or fail."""
        if activity_status in (AIActivityStatus.WAITING, AIActivityStatus.COMPLETED):
            return RuntimeDecision(
                outcome=RuntimeOutcome.COMPLETED,
                classification="hook_completed",
                elapsed_seconds=elapsed_seconds,
                reason=f"Agent reported completion after {int(elapsed_seconds)}s",
            )

        if activity_status == AIActivityStatus.ERROR:
            return RuntimeDecision(
                outcome=RuntimeOutcome.FAILED,
                classification="agent_error",
                elapsed_seconds=elapsed_seconds,
                reason=f"Agent reported error after {int(elapsed_seconds)}s",
            )

        if activity_status != AIActivityStatus.WORKING:
            return RuntimeDecision(
                outcome=RuntimeOutcome.RUNNING,
                classification="non_working_status",
                elapsed_seconds=elapsed_seconds,
            )

        if elapsed_seconds < startup_grace_period:
            return RuntimeDecision(
                outcome=RuntimeOutcome.RUNNING,
                classification="startup_grace_period",
                elapsed_seconds=elapsed_seconds,
            )

        if self._tmux.is_ai_running_in_session(session_name):
            pane_activity = self._tmux.detect_session_activity(session_name)
            if pane_activity is not None:
                detected_status, _high_confidence = pane_activity
                if detected_status in (
                    AIActivityStatus.WAITING,
                    AIActivityStatus.COMPLETED,
                ):
                    return RuntimeDecision(
                        outcome=RuntimeOutcome.COMPLETED,
                        classification="pane_waiting",
                        elapsed_seconds=elapsed_seconds,
                        reason=f"Agent is waiting for input after {int(elapsed_seconds)}s",
                    )
                if detected_status == AIActivityStatus.BLOCKED:
                    return RuntimeDecision(
                        outcome=RuntimeOutcome.RUNNING,
                        classification="pane_blocked",
                        elapsed_seconds=elapsed_seconds,
                    )
            return RuntimeDecision(
                outcome=RuntimeOutcome.RUNNING,
                classification="ai_process_running",
                elapsed_seconds=elapsed_seconds,
            )

        try:
            inspection = self.inspect_worktree_commits(worktree_name, base_ref)
        except Exception as e:
            return RuntimeDecision(
                outcome=RuntimeOutcome.FAILED,
                classification="infra_error",
                elapsed_seconds=elapsed_seconds,
                reason=str(e),
            )

        if inspection.has_commits:
            return RuntimeDecision(
                outcome=RuntimeOutcome.COMPLETED,
                classification="process_exited_with_commits",
                elapsed_seconds=elapsed_seconds,
                reason=f"Agent exited after {int(elapsed_seconds)}s with commits",
                commit_inspection=inspection,
            )

        if elapsed_seconds < min_agent_runtime:
            return RuntimeDecision(
                outcome=RuntimeOutcome.FAILED,
                classification="premature_exit",
                elapsed_seconds=elapsed_seconds,
                reason=(
                    f"Agent exited after {int(elapsed_seconds)}s with no commits "
                    "— likely a silent failure"
                ),
                commit_inspection=inspection,
            )

        return RuntimeDecision(
            outcome=RuntimeOutcome.FAILED,
            classification="no_commits",
            elapsed_seconds=elapsed_seconds,
            reason=f"No commits produced after {int(elapsed_seconds)}s",
            commit_inspection=inspection,
        )

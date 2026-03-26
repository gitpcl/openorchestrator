"""Tests for shared runtime completion evaluation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from open_orchestrator.core.runtime import (
    CommitInspection,
    RuntimeOutcome,
    TaskRuntimeCoordinator,
)
from open_orchestrator.models.status import AIActivityStatus


class _FakeTmux:
    def __init__(self, running: bool = False):
        self.running = running
        self.calls = 0

    def is_ai_running_in_session(self, session_name: str) -> bool:
        self.calls += 1
        return self.running


class TestTaskRuntimeCoordinator:
    def test_startup_grace_period_skips_tmux_probe(self):
        tmux = _FakeTmux(running=False)
        coordinator = TaskRuntimeCoordinator(tmux=tmux, merge_manager_factory=MagicMock())

        decision = coordinator.evaluate_completion(
            worktree_name="wt-a",
            base_ref="main",
            session_name="owt-wt-a",
            elapsed_seconds=5,
            activity_status=AIActivityStatus.WORKING,
            startup_grace_period=30,
            min_agent_runtime=60,
        )

        assert decision.outcome == RuntimeOutcome.RUNNING
        assert decision.classification == "startup_grace_period"
        assert tmux.calls == 0

    def test_hook_reported_completion_skips_tmux_probe(self):
        tmux = _FakeTmux(running=False)
        coordinator = TaskRuntimeCoordinator(tmux=tmux, merge_manager_factory=MagicMock())

        decision = coordinator.evaluate_completion(
            worktree_name="wt-a",
            base_ref="main",
            session_name="owt-wt-a",
            elapsed_seconds=42,
            activity_status=AIActivityStatus.WAITING,
            startup_grace_period=30,
            min_agent_runtime=60,
        )

        assert decision.outcome == RuntimeOutcome.COMPLETED
        assert decision.classification == "hook_completed"
        assert tmux.calls == 0

    def test_process_exit_with_commits_completes(self):
        tmux = _FakeTmux(running=False)
        coordinator = TaskRuntimeCoordinator(tmux=tmux, merge_manager_factory=MagicMock())

        with patch.object(
            coordinator,
            "inspect_worktree_commits",
            return_value=CommitInspection(base_ref="main", commit_count=2, auto_committed_files=1),
        ):
            decision = coordinator.evaluate_completion(
                worktree_name="wt-a",
                base_ref="main",
                session_name="owt-wt-a",
                elapsed_seconds=25,
                activity_status=AIActivityStatus.WORKING,
                startup_grace_period=10,
                min_agent_runtime=60,
            )

        assert decision.outcome == RuntimeOutcome.COMPLETED
        assert decision.classification == "process_exited_with_commits"
        assert decision.commit_inspection is not None
        assert decision.commit_inspection.commit_count == 2

    def test_process_exit_without_commits_before_min_runtime_fails(self):
        tmux = _FakeTmux(running=False)
        coordinator = TaskRuntimeCoordinator(tmux=tmux, merge_manager_factory=MagicMock())

        with patch.object(
            coordinator,
            "inspect_worktree_commits",
            return_value=CommitInspection(base_ref="main", commit_count=0, auto_committed_files=0),
        ):
            decision = coordinator.evaluate_completion(
                worktree_name="wt-a",
                base_ref="main",
                session_name="owt-wt-a",
                elapsed_seconds=45,
                activity_status=AIActivityStatus.WORKING,
                startup_grace_period=10,
                min_agent_runtime=60,
            )

        assert decision.outcome == RuntimeOutcome.FAILED
        assert decision.classification == "premature_exit"
        assert decision.reason is not None
        assert "no commits" in decision.reason.lower()

    def test_commit_inspection_error_is_classified_as_infra_error(self):
        tmux = _FakeTmux(running=False)
        coordinator = TaskRuntimeCoordinator(tmux=tmux, merge_manager_factory=MagicMock())

        with patch.object(
            coordinator,
            "inspect_worktree_commits",
            side_effect=RuntimeError("git blew up"),
        ):
            decision = coordinator.evaluate_completion(
                worktree_name="wt-a",
                base_ref="main",
                session_name="owt-wt-a",
                elapsed_seconds=90,
                activity_status=AIActivityStatus.WORKING,
                startup_grace_period=10,
                min_agent_runtime=60,
            )

        assert decision.outcome == RuntimeOutcome.FAILED
        assert decision.classification == "infra_error"
        assert decision.reason is not None
        assert "git blew up" in decision.reason

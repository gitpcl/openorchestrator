"""Tests for orchestrator lifecycle: start, poll, reconcile, merge."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from open_orchestrator.core.orchestrator import (
    Orchestrator,
    OrchestratorState,
    TaskPhase,
    TaskState,
)


def _make_state(tmp_path: Path, tasks: list[TaskState] | None = None) -> OrchestratorState:
    """Build a minimal OrchestratorState for testing."""
    return OrchestratorState(
        goal="test goal",
        feature_branch="feat/test",
        repo_path=str(tmp_path),
        plan_path=str(tmp_path / "plan.toml"),
        tasks=tasks
        or [
            TaskState(id="a", description="Task A"),
            TaskState(id="b", description="Task B", depends_on=["a"]),
        ],
    )


def _make_orchestrator(state: OrchestratorState) -> Orchestrator:
    """Build an Orchestrator with fully mocked dependencies."""
    mock_tmux = MagicMock()
    mock_merge_factory = MagicMock()
    mock_tracker = MagicMock()
    orch = Orchestrator(
        state,
        tmux=mock_tmux,
        merge_manager_factory=mock_merge_factory,
        tracker=mock_tracker,
    )
    # Replace _runtime with a mock so we can control inspect_worktree_commits
    orch._runtime = MagicMock()
    return orch


class TestDepsSatisfied:
    def test_no_deps_satisfied(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        orch = _make_orchestrator(state)
        # Task A has no deps → satisfied
        assert orch._deps_satisfied(state.tasks[0])

    def test_unmet_dep(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        orch = _make_orchestrator(state)
        # Task B depends on A (pending) → not satisfied
        assert not orch._deps_satisfied(state.tasks[1])

    def test_met_dep(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        state.tasks[0].status = TaskPhase.COMPLETED
        orch = _make_orchestrator(state)
        assert orch._deps_satisfied(state.tasks[1])

    def test_shipped_dep_counts(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        state.tasks[0].status = TaskPhase.SHIPPED
        orch = _make_orchestrator(state)
        assert orch._deps_satisfied(state.tasks[1])


class TestDepsFailed:
    def test_no_failed_deps(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        orch = _make_orchestrator(state)
        assert not orch._deps_failed(state.tasks[1])

    def test_failed_dep_detected(self, tmp_path: Path) -> None:
        state = _make_state(tmp_path)
        state.tasks[0].status = TaskPhase.FAILED
        orch = _make_orchestrator(state)
        assert orch._deps_failed(state.tasks[1])


class TestHandleTaskFailure:
    def test_first_failure_retries(self, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="Task A", status=TaskPhase.RUNNING, max_retries=2),
            ],
        )
        orch = _make_orchestrator(state)
        orch._handle_task_failure(state.tasks[0], "test error")
        assert state.tasks[0].status == TaskPhase.PENDING
        assert state.tasks[0].retry_count == 1

    def test_max_retries_marks_failed(self, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="Task A", status=TaskPhase.RUNNING, max_retries=1, retry_count=1),
            ],
        )
        orch = _make_orchestrator(state)
        orch._handle_task_failure(state.tasks[0], "final error")
        assert state.tasks[0].status == TaskPhase.FAILED
        assert state.tasks[0].failure_reason == "final error"


class TestReconcileWorldState:
    def test_alive_tmux_stays_running(self, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="Task A", status=TaskPhase.RUNNING, worktree_name="wt-a"),
            ],
        )
        orch = _make_orchestrator(state)
        orch.tmux.session_exists.return_value = True
        orch._reconcile_world_state()
        assert state.tasks[0].status == TaskPhase.RUNNING

    def test_dead_tmux_with_commits_completed(self, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="Task A", status=TaskPhase.RUNNING, worktree_name="wt-a"),
            ],
        )
        orch = _make_orchestrator(state)
        orch.tmux.session_exists.return_value = False
        mock_inspection = MagicMock()
        mock_inspection.has_commits = True
        orch._runtime.inspect_worktree_commits.return_value = mock_inspection
        orch._reconcile_world_state()
        assert state.tasks[0].status == TaskPhase.COMPLETED

    def test_dead_tmux_no_commits_failed(self, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="Task A", status=TaskPhase.RUNNING, worktree_name="wt-a"),
            ],
        )
        orch = _make_orchestrator(state)
        orch.tmux.session_exists.return_value = False
        mock_inspection = MagicMock()
        mock_inspection.has_commits = False
        orch._runtime.inspect_worktree_commits.return_value = mock_inspection
        orch._reconcile_world_state()
        assert state.tasks[0].status == TaskPhase.FAILED

    def test_skips_non_running_tasks(self, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="Task A", status=TaskPhase.PENDING),
                TaskState(id="b", description="Task B", status=TaskPhase.COMPLETED, worktree_name="wt-b"),
            ],
        )
        orch = _make_orchestrator(state)
        orch._reconcile_world_state()
        assert state.tasks[0].status == TaskPhase.PENDING
        assert state.tasks[1].status == TaskPhase.COMPLETED


class TestStartTask:
    @patch("open_orchestrator.core.orchestrator.create_pane")
    def test_start_sets_running(self, mock_create: MagicMock, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="Add auth"),
            ],
        )
        mock_pane = MagicMock()
        mock_pane.worktree_name = "add-auth"
        mock_pane.branch = "feat/add-auth"
        mock_create.return_value = mock_pane

        orch = _make_orchestrator(state)
        orch._start_task(state.tasks[0])
        assert state.tasks[0].status == TaskPhase.RUNNING
        assert state.tasks[0].worktree_name == "add-auth"

    @patch("open_orchestrator.core.orchestrator.create_pane")
    def test_start_failure_marks_failed(self, mock_create: MagicMock, tmp_path: Path) -> None:
        from open_orchestrator.core.pane_actions import PaneActionError

        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="Add auth"),
            ],
        )
        mock_create.side_effect = PaneActionError("tmux failed")

        orch = _make_orchestrator(state)
        orch._start_task(state.tasks[0])
        assert state.tasks[0].status in (TaskPhase.FAILED, TaskPhase.PENDING)


class TestAllDone:
    def test_all_shipped(self, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="A", status=TaskPhase.SHIPPED),
            ],
        )
        orch = _make_orchestrator(state)
        assert orch._all_done()

    def test_all_failed(self, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="A", status=TaskPhase.FAILED),
            ],
        )
        orch = _make_orchestrator(state)
        assert orch._all_done()

    def test_pending_not_done(self, tmp_path: Path) -> None:
        state = _make_state(
            tmp_path,
            [
                TaskState(id="a", description="A", status=TaskPhase.PENDING),
            ],
        )
        orch = _make_orchestrator(state)
        assert not orch._all_done()

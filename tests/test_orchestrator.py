"""Tests for the Orchestrator agent and related components.

Tests cover: OrchestratorState model, Orchestrator lifecycle,
AgnoCoordinator, and inject_coordination_context.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.config import AgnoConfig
from open_orchestrator.models.intelligence import CoordinationAction
from open_orchestrator.models.status import AIActivityStatus

# ─── Agno mock fixtures ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_agno_modules():
    """Pre-seed sys.modules so 'from agno.xxx import ...' works without install."""
    modules = {
        "agno": MagicMock(),
        "agno.agent": MagicMock(),
        "agno.models": MagicMock(),
        "agno.models.anthropic": MagicMock(),
        "agno.models.openai": MagicMock(),
        "agno.models.google": MagicMock(),
        "agno.db": MagicMock(),
        "agno.db.sqlite": MagicMock(),
    }
    with patch.dict(sys.modules, modules):
        yield


# ─── CoordinationAction Model ────────────────────────────────────────────


class TestCoordinationAction:
    def test_defaults(self):
        action = CoordinationAction(
            target_worktrees=["auth-jwt"],
            message="File overlap on routes.py",
        )
        assert action.urgency == "info"
        assert action.rationale == ""

    def test_full_fields(self):
        action = CoordinationAction(
            target_worktrees=["auth-jwt", "db-models"],
            message="Both modifying user.py",
            urgency="warning",
            rationale="Potential merge conflict",
        )
        assert len(action.target_worktrees) == 2
        assert action.urgency == "warning"


# ─── inject_coordination_context ──────────────────────────────────────────


class TestInjectCoordinationContext:
    def test_injects_section(self, tmp_path: Path):
        from open_orchestrator.core.environment import inject_coordination_context

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        claude_md = claude_dir / "CLAUDE.md"
        claude_md.write_text("# Project\n\nExisting content.")

        inject_coordination_context(tmp_path, ["File overlap on routes.py", "Check models.py"])

        content = claude_md.read_text()
        assert "Coordinator Alerts (OWT)" in content
        assert "File overlap on routes.py" in content
        assert "Check models.py" in content

    def test_replaces_existing_section(self, tmp_path: Path):
        from open_orchestrator.core.environment import inject_coordination_context

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        claude_md = claude_dir / "CLAUDE.md"
        claude_md.write_text("# Project\n\nExisting content.")

        inject_coordination_context(tmp_path, ["Old message"])
        inject_coordination_context(tmp_path, ["New message"])

        content = claude_md.read_text()
        assert "Old message" not in content
        assert "New message" in content

    def test_clears_section_with_empty_list(self, tmp_path: Path):
        from open_orchestrator.core.environment import inject_coordination_context

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        claude_md = claude_dir / "CLAUDE.md"
        claude_md.write_text("# Project\n\nExisting content.")

        inject_coordination_context(tmp_path, ["Alert!"])
        inject_coordination_context(tmp_path, [])

        content = claude_md.read_text()
        assert "Coordinator Alerts" not in content

    def test_noop_without_claude_md(self, tmp_path: Path):
        from open_orchestrator.core.environment import inject_coordination_context

        # Should not raise
        inject_coordination_context(tmp_path, ["Message"])


# ─── OrchestratorState Model ─────────────────────────────────────────────


class TestOrchestratorState:
    def test_roundtrip_json(self):
        from open_orchestrator.core.orchestrator import OrchestratorState, TaskState

        state = OrchestratorState(
            goal="Add auth",
            feature_branch="feat/auth",
            repo_path="/tmp/repo",
            plan_path="/tmp/plan.toml",
            tasks=[
                TaskState(id="models", description="Create models"),
                TaskState(id="api", description="Create API", depends_on=["models"]),
            ],
        )

        json_str = state.model_dump_json()
        loaded = OrchestratorState.model_validate_json(json_str)
        assert loaded.goal == "Add auth"
        assert len(loaded.tasks) == 2
        assert loaded.tasks[1].depends_on == ["models"]

    def test_task_state_defaults(self):
        from open_orchestrator.core.orchestrator import TaskState

        task = TaskState(id="test", description="Test task")
        assert task.status == "pending"
        assert task.worktree_name is None
        assert task.branch is None
        assert task.depends_on == []


# ─── Orchestrator ─────────────────────────────────────────────────────────


class TestOrchestrator:
    def _make_state(self, tasks=None):
        from open_orchestrator.core.orchestrator import OrchestratorState, TaskState

        if tasks is None:
            tasks = [
                TaskState(id="a", description="Task A"),
                TaskState(id="b", description="Task B", depends_on=["a"]),
            ]
        return OrchestratorState(
            goal="Test",
            feature_branch="feat/test",
            repo_path="/tmp/repo",
            plan_path="/tmp/plan.toml",
            tasks=tasks,
        )

    def test_deps_satisfied(self):
        from open_orchestrator.core.orchestrator import Orchestrator

        state = self._make_state()
        orch = Orchestrator(state)

        # Task A has no deps — satisfied
        assert orch._deps_satisfied(state.tasks[0])
        # Task B depends on A (pending) — not satisfied
        assert not orch._deps_satisfied(state.tasks[1])

        # Mark A as shipped
        state.tasks[0].status = "shipped"
        assert orch._deps_satisfied(state.tasks[1])

    def test_deps_failed(self):
        from open_orchestrator.core.orchestrator import Orchestrator

        state = self._make_state()
        orch = Orchestrator(state)

        assert not orch._deps_failed(state.tasks[1])
        state.tasks[0].status = "failed"
        assert orch._deps_failed(state.tasks[1])

    def test_all_done(self):
        from open_orchestrator.core.orchestrator import Orchestrator

        state = self._make_state()
        orch = Orchestrator(state)

        assert not orch._all_done()
        state.tasks[0].status = "shipped"
        state.tasks[1].status = "failed"
        assert orch._all_done()

    def test_user_in_worktree_false_when_no_session(self):
        from open_orchestrator.core.orchestrator import Orchestrator

        state = self._make_state()
        orch = Orchestrator(state)

        with patch.object(orch.tmux, "get_session_for_worktree", return_value=None):
            assert not orch._user_in_worktree("nonexistent")

    def test_user_in_worktree_true_when_attached(self):
        from open_orchestrator.core.orchestrator import Orchestrator

        state = self._make_state()
        orch = Orchestrator(state)

        mock_info = MagicMock()
        mock_info.attached = True
        with patch.object(orch.tmux, "get_session_for_worktree", return_value=mock_info):
            assert orch._user_in_worktree("my-worktree")

    def test_save_and_resume(self, tmp_path: Path):
        from open_orchestrator.core.orchestrator import Orchestrator

        state = self._make_state()
        state.repo_path = str(tmp_path)
        orch = Orchestrator(state)
        orch._save_state()

        state_path = Orchestrator._state_path(str(tmp_path))
        assert state_path.exists()

        # Resume
        resumed = Orchestrator.resume(str(tmp_path))
        assert resumed.state.goal == "Test"
        assert len(resumed.state.tasks) == 2

    def test_state_path_uses_repo_name(self, tmp_path: Path):
        from open_orchestrator.core.orchestrator import Orchestrator

        path = Orchestrator._state_path(str(tmp_path))
        assert tmp_path.name in path.name

    def test_resume_raises_when_no_state(self, tmp_path: Path):
        from open_orchestrator.core.orchestrator import Orchestrator

        with pytest.raises(FileNotFoundError):
            Orchestrator.resume(str(tmp_path))

    def test_start_ready_tasks_respects_concurrency(self):
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState

        tasks = [
            TaskState(id="a", description="A"),
            TaskState(id="b", description="B"),
            TaskState(id="c", description="C"),
        ]
        state = self._make_state(tasks)
        state.max_concurrent = 1
        orch = Orchestrator(state)

        def fake_start(task):
            task.status = "running"

        with patch.object(orch, "_start_task", side_effect=fake_start) as mock_start:
            orch._start_ready_tasks()
            # Only 1 should be started (max_concurrent=1)
            assert mock_start.call_count == 1

    def test_start_ready_tasks_cascades_failures(self):
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState

        tasks = [
            TaskState(id="a", description="A", status="failed"),
            TaskState(id="b", description="B", depends_on=["a"]),
        ]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        with patch.object(orch, "_start_task"):
            orch._start_ready_tasks()

        assert state.tasks[1].status == "failed"

    def test_cooldown_mechanism(self):
        from open_orchestrator.core.orchestrator import Orchestrator

        state = self._make_state()
        orch = Orchestrator(state)

        assert not orch._in_cooldown("test-event")
        orch._set_cooldown("test-event")
        assert orch._in_cooldown("test-event")

    def test_poll_fallback_detects_exited_process(self):
        """Issue 4: when status is WORKING but AI process has exited,
        the orchestrator should detect completion via tmux inspection."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState
        from open_orchestrator.core.runtime import RuntimeDecision, RuntimeOutcome

        # started_at must be far enough in the past to pass min_agent_runtime guard
        old_start = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        tasks = [TaskState(id="a", description="Task A", status="running", worktree_name="wt-a", started_at=old_start)]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        mock_status = MagicMock()
        mock_status.activity_status = AIActivityStatus.WORKING

        with (
            patch.object(orch, "_user_in_worktree", return_value=False),
            patch.object(orch.tracker, "get_status", return_value=mock_status),
            patch.object(
                orch._runtime,
                "evaluate_completion",
                return_value=RuntimeDecision(
                    outcome=RuntimeOutcome.COMPLETED,
                    classification="process_exited_with_commits",
                    elapsed_seconds=300,
                ),
            ),
            patch.object(orch, "_merge_to_feature_branch"),
        ):
            orch._poll_running_tasks()

        assert state.tasks[0].status == "completed"

    def test_poll_grace_period_skips_new_tasks(self):
        """Issue 17: tasks started in the same tick should not be polled for exit."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState

        # started_at is very recent (within poll_interval grace period)
        recent_start = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        tasks = [TaskState(id="a", description="Task A", status="running", worktree_name="wt-a", started_at=recent_start)]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        mock_status = MagicMock()
        mock_status.activity_status = AIActivityStatus.WORKING

        with (
            patch.object(orch, "_user_in_worktree", return_value=False),
            patch.object(orch.tracker, "get_status", return_value=mock_status),
            patch.object(orch.tmux, "is_ai_running_in_session") as mock_ai,
        ):
            orch._poll_running_tasks()

        # Should NOT have checked tmux — grace period skipped it
        mock_ai.assert_not_called()
        assert state.tasks[0].status == "running"

    def test_poll_fallback_premature_exit_no_commits_fails(self):
        """Issue 16: agent exits quickly with no commits → silent failure."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState
        from open_orchestrator.core.runtime import RuntimeDecision, RuntimeOutcome

        # started_at past grace period (>30s) but under min_agent_runtime (60s)
        recent_start = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
        tasks = [TaskState(id="a", description="Task A", status="running", worktree_name="wt-a", started_at=recent_start)]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        mock_status = MagicMock()
        mock_status.activity_status = AIActivityStatus.WORKING

        with (
            patch.object(orch, "_user_in_worktree", return_value=False),
            patch.object(orch.tracker, "get_status", return_value=mock_status),
            patch.object(
                orch._runtime,
                "evaluate_completion",
                return_value=RuntimeDecision(
                    outcome=RuntimeOutcome.FAILED,
                    classification="premature_exit",
                    elapsed_seconds=45,
                    reason="Agent exited after 45s with no commits — likely a silent failure",
                ),
            ),
            patch.object(orch, "_handle_task_failure") as mock_fail,
        ):
            orch._poll_running_tasks()

        mock_fail.assert_called_once()
        assert "no commits" in mock_fail.call_args[0][1].lower()

    def test_poll_fallback_fast_agent_with_commits_succeeds(self):
        """Issue 18: fast agent (25s) with commits should succeed, not fail."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState
        from open_orchestrator.core.runtime import RuntimeDecision, RuntimeOutcome

        recent_start = (datetime.now(timezone.utc) - timedelta(seconds=25)).isoformat()
        tasks = [TaskState(id="a", description="Task A", status="running", worktree_name="wt-a", started_at=recent_start)]
        state = self._make_state(tasks)
        state.poll_interval = 10
        orch = Orchestrator(state)

        mock_status = MagicMock()
        mock_status.activity_status = AIActivityStatus.WORKING

        with (
            patch.object(orch, "_user_in_worktree", return_value=False),
            patch.object(orch.tracker, "get_status", return_value=mock_status),
            patch.object(
                orch._runtime,
                "evaluate_completion",
                return_value=RuntimeDecision(
                    outcome=RuntimeOutcome.COMPLETED,
                    classification="process_exited_with_commits",
                    elapsed_seconds=25,
                ),
            ),
            patch.object(orch, "_merge_to_feature_branch"),
        ):
            orch._poll_running_tasks()

        assert state.tasks[0].status == "completed"

    def test_poll_no_fallback_when_ai_still_running(self):
        """When status is WORKING and AI process is still running, task stays running."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState

        tasks = [TaskState(id="a", description="Task A", status="running", worktree_name="wt-a")]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        mock_status = MagicMock()
        mock_status.activity_status = AIActivityStatus.WORKING

        with (
            patch.object(orch, "_user_in_worktree", return_value=False),
            patch.object(orch.tracker, "get_status", return_value=mock_status),
            patch.object(orch.tmux, "generate_session_name", return_value="owt-wt-a"),
            patch.object(orch.tmux, "is_ai_running_in_session", return_value=True),
        ):
            orch._poll_running_tasks()

        assert state.tasks[0].status == "running"

    def test_start_task_prompt_uses_git_commit(self):
        """Issue 3: prompt should instruct agent to commit with git, not /commit."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState

        tasks = [TaskState(id="a", description="Implement feature X")]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        with (
            patch("open_orchestrator.core.orchestrator.create_pane") as mock_create,
            patch("open_orchestrator.core.branch_namer.generate_branch_name", return_value="feat/x"),
        ):
            mock_create.return_value = MagicMock(worktree_name="wt-a", branch="feat/x")
            orch._start_task(state.tasks[0])

            call_kwargs = mock_create.call_args[1]
            instructions = call_kwargs["ai_instructions"]
            assert "git add -A && git commit" in instructions
            assert "NEVER use /commit" in instructions

    def test_empty_branch_retries_then_fails(self):
        """Issue 10: empty branch triggers retry, then fails permanently."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState

        tasks = [TaskState(id="a", description="Do X", status="completed", worktree_name="wt-a", branch="feat/x")]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        mock_wt = MagicMock()
        mock_wt.branch = "feat/x"

        with (
            patch("open_orchestrator.core.orchestrator.MergeManager") as MockMM,
            patch("open_orchestrator.core.orchestrator.teardown_worktree"),
            patch.object(orch.tmux, "session_exists", return_value=False),
            patch.object(orch.tracker, "remove_status"),
        ):
            mm = MockMM.return_value
            mm.auto_commit_worktree.return_value = 0
            mm.wt_manager.get.return_value = mock_wt
            mm.count_commits_ahead.return_value = 0

            # First call: should retry (max_retries=1 default)
            orch._merge_to_feature_branch(state.tasks[0])
            assert state.tasks[0].status == "pending"
            assert state.tasks[0].retry_count == 1

            # Simulate re-running after retry
            state.tasks[0].status = "completed"
            state.tasks[0].worktree_name = "wt-a-retry"
            orch._merge_to_feature_branch(state.tasks[0])
            assert state.tasks[0].status == "failed"  # no more retries

        mm.merge.assert_not_called()

    def test_merge_ships_branch_with_commits(self):
        """Branches with new commits should be shipped."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState

        tasks = [TaskState(id="a", description="Do X", status="completed", worktree_name="wt-a", branch="feat/x")]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        mock_wt = MagicMock()
        mock_wt.branch = "feat/x"

        with (
            patch("open_orchestrator.core.orchestrator.MergeManager") as MockMM,
            patch.object(orch.tmux, "session_exists", return_value=False),
            patch.object(orch.tracker, "remove_status"),
        ):
            mm = MockMM.return_value
            mm.auto_commit_worktree.return_value = 0
            mm.wt_manager.get.return_value = mock_wt
            mm.count_commits_ahead.return_value = 3

            orch._merge_to_feature_branch(state.tasks[0])

        assert state.tasks[0].status == "shipped"
        mm.merge.assert_called_once()

    def test_timeout_triggers_retry(self):
        """Tasks that exceed timeout should be retried then failed."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState

        past = (datetime.now(timezone.utc) - timedelta(seconds=3600)).isoformat()
        tasks = [TaskState(id="a", description="Slow task", status="running", worktree_name="wt-a", started_at=past)]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        with (
            patch("open_orchestrator.core.orchestrator.teardown_worktree"),
            patch.object(orch, "_user_in_worktree", return_value=False),
            patch.object(orch, "_update_running_progress"),
            patch.object(orch.tracker, "get_status", return_value=None),
            patch.object(orch.tracker, "remove_status"),
        ):
            orch._poll_running_tasks()

        assert state.tasks[0].status == "pending"  # retried
        assert state.tasks[0].retry_count == 1

    def test_start_task_prompt_includes_commit_instruction(self):
        """Prompt should instruct agent to commit (exit is handled by print mode)."""
        from open_orchestrator.core.orchestrator import Orchestrator, TaskState

        tasks = [TaskState(id="a", description="Implement feature X")]
        state = self._make_state(tasks)
        orch = Orchestrator(state)

        with (
            patch("open_orchestrator.core.orchestrator.create_pane") as mock_create,
            patch("open_orchestrator.core.branch_namer.generate_branch_name", return_value="feat/x"),
        ):
            mock_create.return_value = MagicMock(worktree_name="wt-a", branch="feat/x")
            orch._start_task(state.tasks[0])

            call_kwargs = mock_create.call_args[1]
            instructions = call_kwargs["ai_instructions"]
            assert "git add -A && git commit" in instructions
            assert "/exit" not in instructions


# ─── AgnoCoordinator ─────────────────────────────────────────────────────


class TestAgnoCoordinator:
    def test_analyze_returns_actions(self):
        from open_orchestrator.core.intelligence import AgnoCoordinator

        config = AgnoConfig(enabled=True)
        mock_actions = [
            CoordinationAction(
                target_worktrees=["auth-jwt"],
                message="routes.py overlap detected",
                urgency="warning",
            ),
        ]

        mock_response = MagicMock()
        mock_response.content = mock_actions

        mock_agent_cls = MagicMock()
        mock_agent_cls.return_value.run.return_value = mock_response

        with (
            patch("open_orchestrator.core.intelligence._resolve_model"),
            patch.dict(sys.modules, {"agno.agent": MagicMock(Agent=mock_agent_cls)}),
        ):
            coordinator = AgnoCoordinator(config, repo_path="/tmp/repo")
            result = coordinator.analyze(
                events=[("overlap:routes.py", "File overlap on routes.py")],
                running_worktrees=[
                    {"name": "auth-jwt", "task": "Add JWT", "branch": "feat/jwt"},
                    {"name": "db-models", "task": "Create models", "branch": "feat/models"},
                ],
            )
            assert len(result) == 1
            assert result[0].urgency == "warning"
            assert "auth-jwt" in result[0].target_worktrees

    def test_coordinator_uses_coordinator_model_id(self):
        from open_orchestrator.core.intelligence import AgnoCoordinator

        config = AgnoConfig(
            enabled=True,
            model_id="claude-sonnet-4-20250514",
            coordinator_model_id="claude-haiku-4-5-20251001",
        )

        mock_response = MagicMock()
        mock_response.content = []

        mock_agent_cls = MagicMock()
        mock_agent_cls.return_value.run.return_value = mock_response

        with (
            patch("open_orchestrator.core.intelligence._resolve_model") as mock_resolve,
            patch.dict(sys.modules, {"agno.agent": MagicMock(Agent=mock_agent_cls)}),
        ):
            coordinator = AgnoCoordinator(config)
            coordinator.analyze(events=[], running_worktrees=[])
            mock_resolve.assert_called_once_with(
                "claude-haiku-4-5-20251001",
                config.max_tokens,
                config.temperature,
            )

    def test_coordinator_passes_memory(self, tmp_path: Path):
        from open_orchestrator.core.intelligence import AgnoCoordinator

        config = AgnoConfig(
            enabled=True,
            memory_enabled=True,
            memory_db_path=str(tmp_path / "mem.db"),
        )

        mock_response = MagicMock()
        mock_response.content = []

        mock_agent_cls = MagicMock()
        mock_agent_cls.return_value.run.return_value = mock_response

        with (
            patch("open_orchestrator.core.intelligence._resolve_model"),
            patch.dict(sys.modules, {"agno.agent": MagicMock(Agent=mock_agent_cls)}),
        ):
            coordinator = AgnoCoordinator(config, repo_path=str(tmp_path))
            coordinator.analyze(events=[], running_worktrees=[])
            assert "db" in mock_agent_cls.call_args.kwargs
            assert mock_agent_cls.return_value.run.call_args.kwargs["session_id"] == "coordinator"

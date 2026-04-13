"""Tests for pane lifecycle: create_pane, teardown_worktree, build_agent_prompt, PaneTransaction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core.pane_actions import (
    PaneActionError,
    PaneTransaction,
    build_agent_prompt,
    create_pane,
    popup_result_path,
    remove_pane,
    teardown_worktree,
)
from open_orchestrator.models.status import AIActivityStatus

# ---------------------------------------------------------------------------
# PaneTransaction
# ---------------------------------------------------------------------------


class TestPaneTransaction:
    """Test PaneTransaction rollback behavior."""

    def test_rollback_with_no_resources(self) -> None:
        """Rollback is a no-op when no worktree_name is set."""
        txn = PaneTransaction()
        txn.rollback()  # should not raise

    @patch("open_orchestrator.core.pane_actions.teardown_worktree")
    def test_rollback_delegates_to_teardown(self, mock_teardown: MagicMock) -> None:
        """Rollback calls teardown_worktree with tracked resource flags."""
        txn = PaneTransaction(
            repo_path="/tmp/repo",
            worktree_name="auth-jwt",
            worktree_created=True,
            tmux_session_created=True,
            status_initialized=False,
        )
        txn.rollback()
        mock_teardown.assert_called_once_with(
            "auth-jwt",
            repo_path="/tmp/repo",
            kill_tmux=True,
            delete_git_worktree=True,
            clean_status=False,
            force=True,
        )

    @patch("open_orchestrator.core.pane_actions.teardown_worktree")
    def test_rollback_partial_resources(self, mock_teardown: MagicMock) -> None:
        """Rollback only cleans resources that were created."""
        txn = PaneTransaction(
            repo_path="/tmp/repo",
            worktree_name="api-fix",
            worktree_created=True,
            tmux_session_created=False,
            status_initialized=False,
        )
        txn.rollback()
        mock_teardown.assert_called_once_with(
            "api-fix",
            repo_path="/tmp/repo",
            kill_tmux=False,
            delete_git_worktree=True,
            clean_status=False,
            force=True,
        )


# ---------------------------------------------------------------------------
# build_agent_prompt
# ---------------------------------------------------------------------------


class TestBuildAgentPrompt:
    """Test prompt construction for automated agents."""

    def test_basic_prompt_contains_task(self) -> None:
        prompt = build_agent_prompt("Implement JWT authentication")
        assert "Implement JWT authentication" in prompt
        assert "TASK:" in prompt

    def test_prompt_contains_commit_safety(self) -> None:
        prompt = build_agent_prompt("Fix login bug")
        assert "COMMIT" in prompt.upper() or "commit" in prompt

    def test_prompt_contains_protocol(self) -> None:
        prompt = build_agent_prompt("Add user settings page")
        # Protocol section should be present (from get_protocol_for_task)
        assert len(prompt) > 200  # non-trivial prompt

    def test_retry_context_included_when_provided(self) -> None:
        prompt = build_agent_prompt(
            "Fix database migration",
            retry_context="Previous attempt failed: TypeError in migration step 3",
        )
        assert "TypeError in migration step 3" in prompt

    def test_retry_context_absent_when_not_provided(self) -> None:
        prompt = build_agent_prompt("Simple task")
        assert "retry" not in prompt.lower() or "Previous attempt" not in prompt


# ---------------------------------------------------------------------------
# create_pane
# ---------------------------------------------------------------------------


@patch("open_orchestrator.core.hooks.install_hooks")
@patch("open_orchestrator.core.pane_actions.TmuxManager")
@patch("open_orchestrator.core.pane_actions.ProjectDetector")
@patch("open_orchestrator.core.pane_actions.WorktreeManager")
@patch("open_orchestrator.core.pane_actions.load_config")
class TestCreatePane:
    """Test create_pane orchestration."""

    def _setup_mocks(
        self,
        mock_load_config: MagicMock,
        mock_wt_cls: MagicMock,
        mock_project_cls: MagicMock,
        mock_tmux_cls: MagicMock,
    ) -> tuple[MagicMock, MagicMock]:
        """Configure standard mock returns. Returns (wt_manager, tmux_manager)."""
        mock_load_config.return_value = SimpleNamespace(environment=SimpleNamespace(auto_install_deps=False, copy_env_file=False))
        mock_project_cls.return_value.detect.return_value = None

        worktree = SimpleNamespace(
            name="auth-jwt",
            path=Path("/tmp/auth-jwt"),
            branch="feat/auth-jwt",
        )
        wt_manager = mock_wt_cls.return_value
        wt_manager.list_all.return_value = []
        wt_manager.create.return_value = worktree

        tmux_manager = mock_tmux_cls.return_value
        tmux_manager.create_worktree_session.return_value = SimpleNamespace(session_name="owt-auth-jwt")
        return wt_manager, tmux_manager

    def test_create_pane_interactive_mode(
        self,
        mock_load_config: MagicMock,
        mock_wt_cls: MagicMock,
        mock_project_cls: MagicMock,
        mock_tmux_cls: MagicMock,
        _mock_hooks: MagicMock,
    ) -> None:
        """Interactive pane (no ai_instructions) creates session without prompt."""
        _, tmux_manager = self._setup_mocks(mock_load_config, mock_wt_cls, mock_project_cls, mock_tmux_cls)
        tracker = MagicMock()

        result = create_pane(
            session_name="orch-auth",
            repo_path="/tmp/repo",
            branch="feat/auth-jwt",
            ai_tool="claude",
            status_tracker=tracker,
        )

        assert result.worktree_name == "auth-jwt"
        assert result.ai_tool == "claude"
        # No prompt delivery for interactive mode
        tmux_manager.wait_for_ai_ready.assert_not_called()
        tmux_manager.paste_to_pane.assert_not_called()

    def test_create_pane_with_instructions(
        self,
        mock_load_config: MagicMock,
        mock_wt_cls: MagicMock,
        mock_project_cls: MagicMock,
        mock_tmux_cls: MagicMock,
        _mock_hooks: MagicMock,
    ) -> None:
        """Automated pane delivers prompt via wait_for_ai_ready + paste_to_pane."""
        _, tmux_manager = self._setup_mocks(mock_load_config, mock_wt_cls, mock_project_cls, mock_tmux_cls)
        tracker = MagicMock()

        create_pane(
            session_name="orch-auth",
            repo_path="/tmp/repo",
            branch="feat/auth-jwt",
            ai_tool="claude",
            ai_instructions="Implement JWT auth",
            display_task="JWT auth",
            status_tracker=tracker,
        )

        tmux_manager.wait_for_ai_ready.assert_called_once_with(session_name="owt-auth-jwt", timeout=15)
        tmux_manager.paste_to_pane.assert_called_once_with(session_name="owt-auth-jwt", text="Implement JWT auth")
        tracker.update_task.assert_called_once_with("auth-jwt", "JWT auth", AIActivityStatus.WORKING)

    def test_create_pane_duplicate_raises(
        self,
        mock_load_config: MagicMock,
        mock_wt_cls: MagicMock,
        mock_project_cls: MagicMock,
        mock_tmux_cls: MagicMock,
        _mock_hooks: MagicMock,
    ) -> None:
        """Duplicate worktree name raises PaneActionError."""
        mock_load_config.return_value = SimpleNamespace(environment=SimpleNamespace(auto_install_deps=False, copy_env_file=False))
        existing = SimpleNamespace(name="auth-jwt", path=Path("/tmp/auth-jwt"), branch="feat/auth-jwt")
        mock_wt_cls.return_value.list_all.return_value = [existing]

        with pytest.raises(PaneActionError, match="already exists"):
            create_pane(
                session_name="orch",
                repo_path="/tmp/repo",
                branch="feat/auth-jwt",
                status_tracker=MagicMock(),
            )

    @patch("open_orchestrator.core.pane_actions.PaneTransaction")
    def test_create_pane_rollback_on_tmux_failure(
        self,
        mock_txn_cls: MagicMock,
        mock_load_config: MagicMock,
        mock_wt_cls: MagicMock,
        mock_project_cls: MagicMock,
        mock_tmux_cls: MagicMock,
        _mock_hooks: MagicMock,
    ) -> None:
        """Tmux session failure triggers transaction rollback."""
        from open_orchestrator.core.tmux_manager import TmuxError

        mock_load_config.return_value = SimpleNamespace(environment=SimpleNamespace(auto_install_deps=False, copy_env_file=False))
        mock_project_cls.return_value.detect.return_value = None
        worktree = SimpleNamespace(name="auth-jwt", path=Path("/tmp/auth-jwt"), branch="feat/auth-jwt")
        mock_wt_cls.return_value.list_all.return_value = []
        mock_wt_cls.return_value.create.return_value = worktree
        mock_tmux_cls.return_value.create_worktree_session.side_effect = TmuxError("tmux not found")

        mock_txn = mock_txn_cls.return_value

        with pytest.raises(PaneActionError, match="Failed to create session"):
            create_pane(
                session_name="orch",
                repo_path="/tmp/repo",
                branch="feat/auth-jwt",
                status_tracker=MagicMock(),
            )

        mock_txn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# teardown_worktree
# ---------------------------------------------------------------------------


class TestTeardownWorktree:
    """Test best-effort worktree cleanup."""

    @patch("open_orchestrator.core.pane_actions.StatusTracker")
    @patch("open_orchestrator.core.pane_actions.WorktreeManager")
    @patch("open_orchestrator.core.pane_actions.TmuxManager")
    def test_teardown_full_cleanup(
        self,
        mock_tmux_cls: MagicMock,
        mock_wt_cls: MagicMock,
        mock_tracker_cls: MagicMock,
    ) -> None:
        """Full teardown kills tmux, deletes worktree, cleans status."""
        mock_tmux_cls.return_value.session_exists.return_value = True

        errors = teardown_worktree("auth-jwt", repo_path="/tmp/repo")

        assert errors == []
        mock_tmux_cls.return_value.kill_session.assert_called_once()
        mock_wt_cls.return_value.delete.assert_called_once_with("auth-jwt", force=False)
        mock_tracker_cls.return_value.remove_status.assert_called_once_with("auth-jwt")

    @patch("open_orchestrator.core.pane_actions.StatusTracker")
    @patch("open_orchestrator.core.pane_actions.WorktreeManager")
    @patch("open_orchestrator.core.pane_actions.TmuxManager")
    def test_teardown_skip_tmux(
        self,
        mock_tmux_cls: MagicMock,
        mock_wt_cls: MagicMock,
        mock_tracker_cls: MagicMock,
    ) -> None:
        """Teardown with kill_tmux=False skips session cleanup."""
        errors = teardown_worktree("auth-jwt", repo_path="/tmp/repo", kill_tmux=False)

        assert errors == []
        mock_tmux_cls.return_value.kill_session.assert_not_called()

    @patch("open_orchestrator.core.pane_actions.StatusTracker")
    @patch("open_orchestrator.core.pane_actions.WorktreeManager")
    @patch("open_orchestrator.core.pane_actions.TmuxManager")
    def test_teardown_continues_on_tmux_error(
        self,
        mock_tmux_cls: MagicMock,
        mock_wt_cls: MagicMock,
        mock_tracker_cls: MagicMock,
    ) -> None:
        """Teardown continues all steps even if tmux kill fails."""
        from open_orchestrator.core.tmux_manager import TmuxError

        mock_tmux_cls.return_value.session_exists.return_value = True
        mock_tmux_cls.return_value.kill_session.side_effect = TmuxError("kill failed")

        errors = teardown_worktree("auth-jwt", repo_path="/tmp/repo")

        assert len(errors) == 1
        assert "Could not kill tmux" in errors[0]
        # Other steps still ran
        mock_wt_cls.return_value.delete.assert_called_once()
        mock_tracker_cls.return_value.remove_status.assert_called_once()

    @patch("open_orchestrator.core.pane_actions.StatusTracker")
    @patch("open_orchestrator.core.pane_actions.TmuxManager")
    def test_teardown_no_repo_path_skips_worktree_delete(
        self,
        mock_tmux_cls: MagicMock,
        mock_tracker_cls: MagicMock,
    ) -> None:
        """Teardown without repo_path skips git worktree deletion."""
        mock_tmux_cls.return_value.session_exists.return_value = False

        errors = teardown_worktree("auth-jwt", repo_path=None)

        assert errors == []
        mock_tracker_cls.return_value.remove_status.assert_called_once()


# ---------------------------------------------------------------------------
# remove_pane
# ---------------------------------------------------------------------------


class TestRemovePaneFunction:
    """Test remove_pane delegation."""

    @patch("open_orchestrator.core.pane_actions.teardown_worktree")
    def test_remove_pane_delegates(self, mock_teardown: MagicMock) -> None:
        """remove_pane delegates to teardown_worktree."""
        remove_pane("auth-jwt", repo_path="/tmp/repo")
        mock_teardown.assert_called_once_with("auth-jwt", repo_path="/tmp/repo")


# ---------------------------------------------------------------------------
# popup_result_path
# ---------------------------------------------------------------------------


class TestPopupResultPath:
    """Test popup result temp file paths."""

    def test_path_contains_workspace_name(self) -> None:
        path = popup_result_path("my-workspace")
        assert "my-workspace" in path
        assert "owt-popup-" in path

"""Tests for branch-mode session creation, teardown, and lifecycle.

Branch mode (``owt branch`` / ``owt new --in-place``) creates a branch in
the current checkout instead of a separate git worktree. These tests cover
checkout, stash, teardown, merge, and CLI integration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core.agent_launcher import AgentLauncher, LaunchMode, LaunchRequest, SessionType
from open_orchestrator.core.pane_actions import PaneTransaction
from open_orchestrator.models.worktree_info import SessionInfo


class TestSessionType:
    """SessionType enum behaves correctly."""

    def test_session_type_values(self) -> None:
        assert SessionType.WORKTREE.value == "worktree"
        assert SessionType.BRANCH.value == "branch"

    def test_session_type_distinct(self) -> None:
        assert SessionType.WORKTREE != SessionType.BRANCH


class TestSessionInfo:
    """SessionInfo model carries session metadata."""

    def test_session_info_defaults(self) -> None:
        info = SessionInfo(
            name="my-feature",
            branch="feat/my-feature",
            repo_root="/repo",
        )
        assert info.session_type == SessionType.WORKTREE
        assert info.name == "my-feature"
        assert info.worktree_path is None
        assert info.base_branch is None

    def test_session_info_branch_mode(self) -> None:
        info = SessionInfo(
            session_type=SessionType.BRANCH,
            name="fix-bug",
            branch="fix-bug",
            repo_root="/repo",
            base_branch="main",
        )
        assert info.session_type == SessionType.BRANCH
        assert info.worktree_path is None

    def test_session_info_full(self) -> None:
        info = SessionInfo(
            session_type=SessionType.WORKTREE,
            name="auth",
            branch="feat/auth",
            worktree_path="/repo/worktrees/auth",
            repo_root="/repo",
            base_branch="main",
            head_commit="abc1234",
            is_main=False,
        )
        assert info.worktree_path == "/repo/worktrees/auth"
        assert info.head_commit == "abc1234"


class TestLaunchRequestSessionType:
    """LaunchRequest carries session_type field."""

    def test_default_session_type(self) -> None:
        req = LaunchRequest(
            branch="feat/test",
            base_branch="main",
            ai_tool="claude",
            mode=LaunchMode.INTERACTIVE,
        )
        assert req.session_type == SessionType.WORKTREE

    def test_branch_session_type(self) -> None:
        req = LaunchRequest(
            branch="fix-bug",
            base_branch="main",
            ai_tool="pi",
            mode=LaunchMode.INTERACTIVE,
            session_type=SessionType.BRANCH,
        )
        assert req.session_type == SessionType.BRANCH


class TestBranchCheckout:
    """Launcher checkout branch behavior."""

    @patch("git.Repo")
    @patch("open_orchestrator.core.agent_launcher.TmuxManager")
    @patch("open_orchestrator.core.agent_launcher.WorktreeManager")
    def test_prepare_checkout_dispatches_to_branch(
        self,
        mock_wt_cls: MagicMock,
        mock_tmux_cls: MagicMock,
        mock_repo_cls: MagicMock,
    ) -> None:
        """_prepare_checkout calls _checkout_branch for BRANCH sessions."""
        mock_repo = MagicMock()
        mock_repo.is_dirty.return_value = False
        from git.exc import GitCommandError

        mock_repo.git.rev_parse.side_effect = GitCommandError("rev-parse", 1)
        mock_repo_cls.return_value = mock_repo

        launcher = AgentLauncher(
            repo_path="/tmp/repo",
            wt_manager=mock_wt_cls.return_value,
            tmux=mock_tmux_cls.return_value,
        )
        request = LaunchRequest(
            branch="fix-bug",
            base_branch="main",
            ai_tool="claude",
            mode=LaunchMode.INTERACTIVE,
            session_type=SessionType.BRANCH,
        )
        txn = PaneTransaction(repo_path="/tmp/repo")

        name, path, branch = launcher._prepare_checkout(request, txn)

        assert name == "fix-bug"
        assert branch == "fix-bug"
        assert txn.branch_created is True
        assert txn.stash_created is False

    @patch("git.Repo")
    @patch("open_orchestrator.core.agent_launcher.TmuxManager")
    @patch("open_orchestrator.core.agent_launcher.WorktreeManager")
    def test_branch_checkout_stashes_dirty(
        self,
        mock_wt_cls: MagicMock,
        mock_tmux_cls: MagicMock,
        mock_repo_cls: MagicMock,
    ) -> None:
        """_checkout_branch stashes dirty state when repo is dirty."""
        mock_repo = MagicMock()
        mock_repo.is_dirty.return_value = True
        from git.exc import GitCommandError

        mock_repo.git.rev_parse.side_effect = GitCommandError("rev-parse", 1)
        mock_repo_cls.return_value = mock_repo

        launcher = AgentLauncher(
            repo_path="/tmp/repo",
            wt_manager=mock_wt_cls.return_value,
            tmux=mock_tmux_cls.return_value,
        )
        request = LaunchRequest(
            branch="fix-bug",
            base_branch="main",
            ai_tool="claude",
            mode=LaunchMode.INTERACTIVE,
            session_type=SessionType.BRANCH,
        )
        txn = PaneTransaction(repo_path="/tmp/repo")

        launcher._prepare_checkout(request, txn)

        assert txn.stash_created is True
        mock_repo.git.stash.assert_called_once()

    @patch("git.Repo")
    @patch("open_orchestrator.core.agent_launcher.TmuxManager")
    @patch("open_orchestrator.core.agent_launcher.WorktreeManager")
    def test_branch_checkout_rejects_existing_branch(
        self,
        mock_wt_cls: MagicMock,
        mock_tmux_cls: MagicMock,
        mock_repo_cls: MagicMock,
    ) -> None:
        """_checkout_branch raises if branch already exists."""
        mock_repo = MagicMock()
        mock_repo.is_dirty.return_value = False
        # rev_parse succeeds = branch exists
        mock_repo.git.rev_parse.return_value = "abc123"
        mock_repo_cls.return_value = mock_repo

        launcher = AgentLauncher(
            repo_path="/tmp/repo",
            wt_manager=mock_wt_cls.return_value,
            tmux=mock_tmux_cls.return_value,
        )
        request = LaunchRequest(
            branch="existing-branch",
            base_branch="main",
            ai_tool="claude",
            mode=LaunchMode.INTERACTIVE,
            session_type=SessionType.BRANCH,
        )
        txn = PaneTransaction(repo_path="/tmp/repo")

        from open_orchestrator.core.pane_actions import PaneActionError

        with pytest.raises(PaneActionError, match="already exists"):
            launcher._prepare_checkout(request, txn)


class TestTeardownBranch:
    """Teardown handles branch-mode cleanup."""

    @patch("git.Repo")
    def test_cleanup_branch_deletes_branch(self, mock_repo_cls: MagicMock) -> None:
        """_cleanup_branch switches to default and deletes the session branch."""
        from open_orchestrator.core.pane_actions import _cleanup_branch

        mock_repo = MagicMock()
        mock_repo.active_branch.name = "fix-bug"
        mock_repo.git.rev_parse.side_effect = Exception("no main")
        mock_repo_cls.return_value = mock_repo

        errors: list[str] = []
        _cleanup_branch("/tmp/repo", "fix-bug", errors)

        assert errors == []
        mock_repo.git.checkout.assert_called_once_with("master")
        mock_repo.git.branch.assert_called_once_with("-D", "fix-bug")

    @patch("git.Repo")
    def test_pop_auto_stash_finds_marker(self, mock_repo_cls: MagicMock) -> None:
        """_pop_auto_stash pops stash with matching marker."""
        from open_orchestrator.core.pane_actions import _pop_auto_stash

        mock_repo = MagicMock()
        mock_stash_result = MagicMock()
        mock_stash_result.splitlines.return_value = [
            "stash@{0}: On main: owt-auto-stash-fix-bug",
        ]
        mock_repo.git.stash.return_value = mock_stash_result
        mock_repo_cls.return_value = mock_repo

        errors: list[str] = []
        _pop_auto_stash("/tmp/repo", "fix-bug", errors)

        assert errors == []
        mock_repo.git.stash.assert_any_call("pop")

    @patch("git.Repo")
    def test_pop_auto_stash_skips_when_no_match(self, mock_repo_cls: MagicMock) -> None:
        """_pop_auto_stash is a no-op when no stash matches."""
        from open_orchestrator.core.pane_actions import _pop_auto_stash

        mock_repo = MagicMock()
        mock_stash_result = MagicMock()
        mock_stash_result.splitlines.return_value = [
            "stash@{0}: On main: some-other-stash",
        ]
        mock_repo.git.stash.return_value = mock_stash_result
        mock_repo_cls.return_value = mock_repo

        errors: list[str] = []
        _pop_auto_stash("/tmp/repo", "fix-bug", errors)

        assert errors == []

    @patch("git.Repo")
    def test_cleanup_branch_errors_collected(self, mock_repo_cls: MagicMock) -> None:
        """_cleanup_branch appends errors when branch deletion fails."""
        from git.exc import GitCommandError

        from open_orchestrator.core.pane_actions import _cleanup_branch

        mock_repo = MagicMock()
        mock_repo.active_branch.name = "fix-bug"
        mock_repo.git.rev_parse.side_effect = Exception("no main")
        mock_repo.git.checkout.side_effect = GitCommandError("checkout", 1)
        mock_repo_cls.return_value = mock_repo

        errors: list[str] = []
        _cleanup_branch("/tmp/repo", "fix-bug", errors)

        assert len(errors) == 1
        assert "Could not clean up" in errors[0]


class TestPaneTransactionBranchMode:
    """PaneTransaction branch/stash fields."""

    def test_default_branch_fields(self) -> None:
        txn = PaneTransaction()
        assert txn.branch_created is False
        assert txn.stash_created is False
        assert txn.session_type == "worktree"

    def test_rollback_includes_branch_params(self) -> None:
        """PaneTransaction.rollback passes branch/stash params."""
        with patch("open_orchestrator.core.pane_actions.teardown_worktree") as mock_teardown:
            txn = PaneTransaction(
                repo_path="/tmp/repo",
                worktree_name="fix-bug",
                branch_created=True,
                stash_created=True,
                tmux_session_created=True,
                status_initialized=True,
            )
            txn.rollback()

            mock_teardown.assert_called_once_with(
                "fix-bug",
                repo_path="/tmp/repo",
                kill_tmux=True,
                delete_git_worktree=False,
                clean_status=True,
                delete_branch=True,
                pop_stash=True,
                force=True,
                backend_kind="tmux",
                backend_session_id=None,
                backend_meta=None,
            )

    def test_rollback_branch_only(self) -> None:
        """Rollback with only branch_created cleans branch without tmux/worktree."""
        with patch("open_orchestrator.core.pane_actions.teardown_worktree") as mock_teardown:
            txn = PaneTransaction(
                repo_path="/tmp/repo",
                worktree_name="fix-bug",
                branch_created=True,
                stash_created=False,
            )
            txn.rollback()

            mock_teardown.assert_called_once_with(
                "fix-bug",
                repo_path="/tmp/repo",
                kill_tmux=False,
                delete_git_worktree=False,
                clean_status=False,
                delete_branch=True,
                pop_stash=False,
                force=True,
                backend_kind="tmux",
                backend_session_id=None,
                backend_meta=None,
            )


class TestLaunchResultBranchMode:
    """LaunchResult includes session_type and repo_root for branch mode."""

    @patch("git.Repo")
    @patch("open_orchestrator.core.agent_launcher.TmuxManager")
    @patch("open_orchestrator.core.agent_launcher.WorktreeManager")
    @patch("open_orchestrator.core.agent_launcher._init_pane_tracking")
    def test_launch_result_includes_session_type(
        self,
        mock_init_tracking: MagicMock,
        mock_wt_cls: MagicMock,
        mock_tmux_cls: MagicMock,
        mock_repo_cls: MagicMock,
    ) -> None:
        """LaunchResult carries session_type and repo_root for branch mode."""
        mock_repo = MagicMock()
        mock_repo.is_dirty.return_value = False
        from git.exc import GitCommandError

        mock_repo.git.rev_parse.side_effect = GitCommandError("rev-parse", 1)
        mock_repo_cls.return_value = mock_repo

        mock_tmux = MagicMock()
        mock_tmux.create_worktree_session.return_value.session_name = "owt-fix-bug"
        mock_tmux_cls.return_value = mock_tmux

        mock_init_tracking.return_value = MagicMock()

        launcher = AgentLauncher(
            repo_path="/tmp/repo",
            wt_manager=mock_wt_cls.return_value,
            tmux=mock_tmux,
        )
        request = LaunchRequest(
            branch="fix-bug",
            base_branch="main",
            ai_tool="claude",
            mode=LaunchMode.INTERACTIVE,
            session_type=SessionType.BRANCH,
        )

        result = launcher.launch(request)

        assert result.session_type == SessionType.BRANCH
        assert result.repo_root == "/tmp/repo"
        assert result.worktree_name == "fix-bug"


class TestDetectSessionType:
    """Session type detection from CLI layer."""

    def test_detect_branch_unknown_name(self) -> None:
        """_detect_session_type returns True for names not in git worktree list."""
        from open_orchestrator.commands.merge_cmds import _detect_session_type
        from open_orchestrator.core.worktree import WorktreeNotFoundError

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.side_effect = WorktreeNotFoundError("not found")
            mock_wt_cls.return_value = mock_wt

            assert _detect_session_type("branch-session") is True

    def test_detect_worktree_known_name(self) -> None:
        """_detect_session_type returns False for names in git worktree list."""
        from open_orchestrator.commands.merge_cmds import _detect_session_type

        with patch("open_orchestrator.core.worktree.WorktreeManager") as mock_wt_cls:
            mock_wt = MagicMock()
            mock_wt.get.return_value = MagicMock()
            mock_wt_cls.return_value = mock_wt

            assert _detect_session_type("worktree-session") is False


class TestMergeBranchMode:
    """MergeManager merge with branch_mode=True."""

    def test_merge_branch_mode_accepted(self) -> None:
        """MergeManager.merge accepts branch_mode parameter."""
        from open_orchestrator.core.merge import MergeManager

        with patch.object(MergeManager, "__init__", return_value=None):
            merge_mgr = MergeManager.__new__(MergeManager)
            merge_mgr.wt_manager = MagicMock()
            merge_mgr.wt_manager.git_root = "/tmp/repo"

            # Should accept the parameter without error
            import inspect

            sig = inspect.signature(merge_mgr.merge)
            assert "branch_mode" in sig.parameters
            assert sig.parameters["branch_mode"].default is False


class TestCLIIntegration:
    """CLI flags for branch mode."""

    def test_new_worktree_has_in_place_flag(self) -> None:
        """owt new command has --in-place flag."""
        from open_orchestrator.commands.worktree import new_worktree

        assert any(p.name == "branch_mode" for p in new_worktree.params)

    def test_branch_cmd_registered(self) -> None:
        """owt branch command is registered."""
        import click

        from open_orchestrator.commands.worktree import branch_cmd, register

        group = click.Group("test")
        register(group)
        assert group.get_command(None, "branch") is branch_cmd

    def test_branch_cmd_has_required_options(self) -> None:
        """owt branch command has required options."""
        from open_orchestrator.commands.worktree import branch_cmd

        param_names = {p.name for p in branch_cmd.params}
        assert "description" in param_names
        assert "base_branch" in param_names
        assert "ai_tool" in param_names

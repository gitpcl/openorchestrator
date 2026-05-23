"""
Tests for CLI entry point and commands.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.core.worktree import (
    NotAGitRepositoryError,
    WorktreeNotFoundError,
)
from open_orchestrator.models.worktree_info import WorktreeInfo


@pytest.fixture
def mock_worktree_info(temp_directory: Path) -> WorktreeInfo:
    """Create a mock WorktreeInfo for testing."""
    return WorktreeInfo(
        path=temp_directory / "test-worktree",
        branch="feature/test",
        head_commit="abc123f",
        is_main=False,
        is_detached=False,
    )


class TestCLIMain:
    """Test main CLI entry point."""

    def test_main_group_help(self, cli_runner: CliRunner) -> None:
        """Test main CLI group can be invoked with --help."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Open Orchestrator" in result.output
        assert "new" in result.output
        assert "list" in result.output
        assert "send" in result.output

    def test_version_command(self, cli_runner: CliRunner) -> None:
        """Test version command."""
        result = cli_runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "open-orchestrator" in result.output


class TestListCommand:
    """Test 'owt list' command."""

    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.core.tmux_manager.TmuxManager")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_list_worktrees_empty(
        self,
        mock_wt_manager: MagicMock,
        mock_tmux: MagicMock,
        mock_status: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test listing worktrees when there are none."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = []

        result = cli_runner.invoke(main, ["list"])
        assert result.exit_code == 0

    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.core.tmux_manager.TmuxManager")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_list_worktrees_with_results(
        self,
        mock_wt_manager: MagicMock,
        mock_tmux: MagicMock,
        mock_status: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test listing worktrees with results."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = [mock_worktree_info]

        mock_tmux_instance = mock_tmux.return_value
        mock_tmux_instance.get_session_for_worktree.return_value = None

        mock_status_instance = mock_status.return_value
        mock_status_instance.get_status.return_value = None

        result = cli_runner.invoke(main, ["list"])
        assert result.exit_code == 0
        assert "feature/test" in result.output

    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_list_worktrees_not_a_git_repo(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test listing worktrees outside a git repository fails."""
        mock_wt_manager.side_effect = NotAGitRepositoryError("Not a git repository")

        result = cli_runner.invoke(main, ["list"])
        assert result.exit_code != 0


class TestSendCommand:
    """Test 'owt send' command."""

    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.core.backend_factory.select_backend")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_send_command_to_worktree(
        self,
        mock_wt_manager: MagicMock,
        mock_select_backend: MagicMock,
        mock_status: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test sending a command to a worktree's AI agent via the backend."""
        from open_orchestrator.models.backend import BackendKind, BackendSession

        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.return_value = mock_worktree_info

        # Tracker reports a live session for the worktree.
        session = BackendSession(kind=BackendKind.TMUX, id="owt-test-worktree", worktree_name="test-worktree")
        mock_status.return_value.get_backend_session.return_value = session

        mock_backend = MagicMock()
        mock_backend.is_alive.return_value = True
        mock_select_backend.return_value = mock_backend

        result = cli_runner.invoke(main, ["send", "feature/test", "echo", "hello"])
        assert result.exit_code == 0
        mock_backend.send_text.assert_called_once()
        sent_session, sent_text = mock_backend.send_text.call_args.args
        assert sent_session.id == "owt-test-worktree"
        assert sent_text == "echo hello"

    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_send_command_to_nonexistent_worktree(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test sending a command to a nonexistent worktree fails."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.side_effect = WorktreeNotFoundError("Worktree not found")

        result = cli_runner.invoke(main, ["send", "feature/nonexistent", "echo", "hello"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    @patch("open_orchestrator.commands._shared.StatusTracker")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_send_command_without_tmux_session(
        self,
        mock_wt_manager: MagicMock,
        mock_status: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test sending a command to a worktree without a live session fails."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.return_value = mock_worktree_info

        # No backend session recorded → send must fail with a clear error.
        mock_status.return_value.get_backend_session.return_value = None

        result = cli_runner.invoke(main, ["send", "feature/test", "echo", "hello"])
        assert result.exit_code != 0


class TestDeleteCommand:
    """Test 'owt delete' command."""

    @patch("open_orchestrator.core.pane_actions.StatusTracker")
    @patch("open_orchestrator.core.backend_factory.select_backend")
    @patch("open_orchestrator.core.pane_actions.WorktreeManager")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_delete_worktree_success(
        self,
        mock_wt_manager: MagicMock,
        mock_pa_wt_manager: MagicMock,
        mock_select_backend: MagicMock,
        mock_pa_status: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test deleting a worktree successfully."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.return_value = mock_worktree_info
        mock_wt_instance.git_root = Path("/tmp/test-repo")

        mock_backend = MagicMock()
        mock_backend.session_for.return_value = None  # no live session
        mock_select_backend.return_value = mock_backend
        mock_pa_status.return_value.get_status.return_value = None  # no DB row

        result = cli_runner.invoke(main, ["delete", "feature/test", "--force", "--yes"])
        assert result.exit_code == 0
        # Verify --force is passed through to git worktree remove
        mock_pa_wt_manager.return_value.delete.assert_called_once_with("test-worktree", force=True)

    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_delete_nonexistent_worktree(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test deleting a nonexistent worktree fails."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.side_effect = WorktreeNotFoundError("Worktree not found")

        result = cli_runner.invoke(main, ["delete", "feature/nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestSwitchCommand:
    """Test 'owt switch' command."""

    @patch("open_orchestrator.core.tmux_manager.TmuxManager")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_switch_to_worktree_no_session(
        self,
        mock_wt_manager: MagicMock,
        mock_tmux: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test switching to a worktree without tmux session fails."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.return_value = mock_worktree_info

        mock_tmux_instance = mock_tmux.return_value
        mock_tmux_instance.generate_session_name.return_value = "owt-test-worktree"
        mock_tmux_instance.session_exists.return_value = False

        result = cli_runner.invoke(main, ["switch", "feature/test"])
        assert result.exit_code != 0

    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_switch_to_nonexistent_worktree(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test switching to a nonexistent worktree fails."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.side_effect = WorktreeNotFoundError("Worktree not found")

        result = cli_runner.invoke(main, ["switch", "feature/nonexistent"])
        assert result.exit_code != 0


class TestSwitchboardLaunch:
    """Test default UI launches when no subcommand given.

    Sprint 024 made the control plane the default; the legacy card grid
    is still available behind ``--legacy-cards``.
    """

    @patch("open_orchestrator.core.control_plane_view.ControlPlaneApp")
    def test_no_args_launches_control_plane(self, mock_app: MagicMock, cli_runner: CliRunner) -> None:
        """'owt' with no args launches the control plane (Sprint 024)."""
        cli_runner.invoke(main, [])
        mock_app.assert_called_once()

    @patch("open_orchestrator.core.switchboard.launch_switchboard")
    def test_legacy_flag_launches_switchboard(self, mock_switchboard: MagicMock, cli_runner: CliRunner) -> None:
        """'owt --legacy-cards' still launches the legacy switchboard."""
        cli_runner.invoke(main, ["--legacy-cards"])
        mock_switchboard.assert_called_once()

    def test_help_still_works(self, cli_runner: CliRunner) -> None:
        """Test 'owt --help' still works."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Open Orchestrator" in result.output

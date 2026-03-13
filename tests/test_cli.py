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
def cli_runner() -> CliRunner:
    """Create a Click CLI test runner."""
    return CliRunner()


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

    @patch("open_orchestrator.cli.StatusTracker")
    @patch("open_orchestrator.cli.TmuxManager")
    @patch("open_orchestrator.cli.WorktreeManager")
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

    @patch("open_orchestrator.cli.StatusTracker")
    @patch("open_orchestrator.cli.TmuxManager")
    @patch("open_orchestrator.cli.WorktreeManager")
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

    @patch("open_orchestrator.cli.WorktreeManager")
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

    @patch("open_orchestrator.cli.StatusTracker")
    @patch("open_orchestrator.cli.TmuxManager")
    @patch("open_orchestrator.cli.WorktreeManager")
    def test_send_command_to_worktree(
        self,
        mock_wt_manager: MagicMock,
        mock_tmux: MagicMock,
        mock_status: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test sending a command to a worktree's AI agent."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.return_value = mock_worktree_info

        mock_tmux_instance = mock_tmux.return_value
        mock_tmux_instance.generate_session_name.return_value = "owt-test-worktree"
        mock_tmux_instance.session_exists.return_value = True

        result = cli_runner.invoke(main, ["send", "feature/test", "echo", "hello"])
        assert result.exit_code == 0
        mock_tmux_instance.send_keys_to_pane.assert_called_once()

    @patch("open_orchestrator.cli.WorktreeManager")
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

    @patch("open_orchestrator.cli.TmuxManager")
    @patch("open_orchestrator.cli.WorktreeManager")
    def test_send_command_without_tmux_session(
        self,
        mock_wt_manager: MagicMock,
        mock_tmux: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test sending a command to a worktree without a tmux session fails."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.return_value = mock_worktree_info

        mock_tmux_instance = mock_tmux.return_value
        mock_tmux_instance.generate_session_name.return_value = "owt-test-worktree"
        mock_tmux_instance.session_exists.return_value = False

        result = cli_runner.invoke(main, ["send", "feature/test", "echo", "hello"])
        assert result.exit_code != 0


class TestDeleteCommand:
    """Test 'owt delete' command."""

    @patch("open_orchestrator.cli.StatusTracker")
    @patch("open_orchestrator.cli.TmuxManager")
    @patch("open_orchestrator.cli.WorktreeManager")
    def test_delete_worktree_success(
        self,
        mock_wt_manager: MagicMock,
        mock_tmux: MagicMock,
        mock_status: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test deleting a worktree successfully."""
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.get.return_value = mock_worktree_info
        mock_wt_instance.delete.return_value = mock_worktree_info.path

        mock_tmux_instance = mock_tmux.return_value
        mock_tmux_instance.generate_session_name.return_value = "owt-test-worktree"
        mock_tmux_instance.session_exists.return_value = False

        result = cli_runner.invoke(main, ["delete", "feature/test", "--force", "--yes"])
        assert result.exit_code == 0
        mock_wt_instance.delete.assert_called_once()

    @patch("open_orchestrator.cli.WorktreeManager")
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

    @patch("open_orchestrator.cli.TmuxManager")
    @patch("open_orchestrator.cli.WorktreeManager")
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

    @patch("open_orchestrator.cli.WorktreeManager")
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
    """Test switchboard launches when no subcommand given."""

    @patch("open_orchestrator.core.switchboard.launch_switchboard")
    def test_no_args_launches_switchboard(
        self, mock_switchboard: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test that 'owt' with no args launches the switchboard."""
        result = cli_runner.invoke(main, [])
        mock_switchboard.assert_called_once()

    def test_help_still_works(self, cli_runner: CliRunner) -> None:
        """Test 'owt --help' still works."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Open Orchestrator" in result.output

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
    WorktreeAlreadyExistsError,
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
        is_bare=False,
        is_detached=False,
        is_locked=False,
        lock_reason=None,
        prunable=None,
    )


class TestCLIMain:
    """Test main CLI entry point."""

    def test_main_group_exists(self, cli_runner: CliRunner) -> None:
        """Test main CLI group can be invoked."""
        result = cli_runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Open Orchestrator" in result.output
        assert "create" in result.output
        assert "list" in result.output
        assert "send" in result.output
        assert "status" in result.output

    def test_version_option(self, cli_runner: CliRunner) -> None:
        """Test version option displays version."""
        result = cli_runner.invoke(main, ["--version"])
        assert result.exit_code == 0


class TestCreateCommand:
    """Test 'owt create' command."""

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.TmuxManager")
    @patch("open_orchestrator.cli.ProjectDetector")
    @patch("open_orchestrator.cli.EnvironmentSetup")
    @patch("open_orchestrator.cli.StatusTracker")
    def test_create_worktree_basic(
        self,
        mock_status: MagicMock,
        mock_env_setup: MagicMock,
        mock_detector: MagicMock,
        mock_tmux: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test creating a worktree with basic options."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.create.return_value = mock_worktree_info
        mock_wt_instance.repo.working_dir = "/fake/repo"

        mock_detector_instance = mock_detector.return_value
        mock_detector_instance.detect.return_value = None

        # Act
        result = cli_runner.invoke(main, ["create", "feature/test", "--no-tmux", "--no-deps", "--no-env"])

        # Assert
        assert result.exit_code == 0
        mock_wt_instance.create.assert_called_once_with(
            branch="feature/test",
            base_branch=None,
            path=None,
            force=False,
        )
        assert "Worktree created successfully" in result.output

    @patch("open_orchestrator.cli.WorktreeManager")
    def test_create_worktree_with_base_branch(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test creating a worktree with base branch."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.create.return_value = mock_worktree_info
        mock_wt_instance.repo.working_dir = "/fake/repo"

        # Act
        result = cli_runner.invoke(main, ["create", "feature/new", "--base", "develop", "--no-tmux", "--no-deps", "--no-env"])

        # Assert
        assert result.exit_code == 0
        mock_wt_instance.create.assert_called_once_with(
            branch="feature/new",
            base_branch="develop",
            path=None,
            force=False,
        )

    @patch("open_orchestrator.cli.WorktreeManager")
    def test_create_worktree_already_exists(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test creating a worktree that already exists fails."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.create.side_effect = WorktreeAlreadyExistsError("Worktree already exists")

        # Act
        result = cli_runner.invoke(main, ["create", "feature/existing", "--no-tmux"])

        # Assert
        assert result.exit_code != 0
        assert "Worktree already exists" in result.output

    @patch("open_orchestrator.cli.WorktreeManager")
    def test_create_worktree_not_a_git_repo(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test creating a worktree outside a git repository fails."""
        # Arrange
        mock_wt_manager.side_effect = NotAGitRepositoryError("Not a git repository")

        # Act
        result = cli_runner.invoke(main, ["create", "feature/test"])

        # Assert
        assert result.exit_code != 0
        assert "Not a git repository" in result.output

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.TmuxManager")
    def test_create_worktree_with_tmux(
        self,
        mock_tmux: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test creating a worktree with tmux session."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.create.return_value = mock_worktree_info
        mock_wt_instance.repo.working_dir = "/fake/repo"

        mock_tmux_instance = mock_tmux.return_value
        mock_session_info = MagicMock()
        mock_session_info.name = "owt-feature-test"
        mock_tmux_instance.create_worktree_session.return_value = mock_session_info
        mock_tmux_instance.is_inside_tmux.return_value = False

        # Act
        result = cli_runner.invoke(main, ["create", "feature/test", "--tmux", "--no-deps", "--no-env"])

        # Assert
        assert result.exit_code == 0
        mock_tmux_instance.create_worktree_session.assert_called_once()
        assert "tmux session created" in result.output.lower() or "session" in result.output.lower()


class TestListCommand:
    """Test 'owt list' command."""

    @patch("open_orchestrator.cli.WorktreeManager")
    def test_list_worktrees_empty(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test listing worktrees when there are none."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = []

        # Act
        result = cli_runner.invoke(main, ["list"])

        # Assert
        assert result.exit_code == 0
        mock_wt_instance.list_all.assert_called_once()

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.TmuxManager")
    def test_list_worktrees_with_results(
        self,
        mock_tmux: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test listing worktrees with results."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = [mock_worktree_info]

        mock_tmux_instance = mock_tmux.return_value
        mock_tmux_instance.get_session_for_worktree.return_value = None

        # Act
        result = cli_runner.invoke(main, ["list"])

        # Assert
        assert result.exit_code == 0
        assert "feature/test" in result.output
        mock_wt_instance.list_all.assert_called_once()

    @patch("open_orchestrator.cli.WorktreeManager")
    def test_list_worktrees_not_a_git_repo(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test listing worktrees outside a git repository fails."""
        # Arrange
        mock_wt_manager.side_effect = NotAGitRepositoryError("Not a git repository")

        # Act
        result = cli_runner.invoke(main, ["list"])

        # Assert
        assert result.exit_code != 0


class TestSendCommand:
    """Test 'owt send' command."""

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.TmuxManager")
    def test_send_command_to_worktree(
        self,
        mock_tmux: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test sending a command to a worktree's Claude instance."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = [mock_worktree_info]

        mock_tmux_instance = mock_tmux.return_value
        mock_session_info = MagicMock()
        mock_session_info.name = "owt-feature-test"
        mock_tmux_instance.get_session_for_worktree.return_value = mock_session_info

        # Act
        result = cli_runner.invoke(main, ["send", "feature/test", "echo hello"])

        # Assert
        assert result.exit_code == 0
        mock_tmux_instance.send_keys_to_pane.assert_called_once()

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.TmuxManager")
    def test_send_command_to_nonexistent_worktree(
        self,
        mock_tmux: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test sending a command to a nonexistent worktree fails."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = []

        # Act
        result = cli_runner.invoke(main, ["send", "feature/nonexistent", "echo hello"])

        # Assert
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "no worktree" in result.output.lower()

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.TmuxManager")
    def test_send_command_to_worktree_without_tmux_session(
        self,
        mock_tmux: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test sending a command to a worktree without a tmux session fails."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = [mock_worktree_info]

        mock_tmux_instance = mock_tmux.return_value
        mock_tmux_instance.get_session_for_worktree.return_value = None

        # Act
        result = cli_runner.invoke(main, ["send", "feature/test", "echo hello"])

        # Assert
        assert result.exit_code != 0
        assert "no tmux session" in result.output.lower() or "session not found" in result.output.lower()


class TestStatusCommand:
    """Test 'owt status' command."""

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.StatusTracker")
    def test_status_command_empty(
        self,
        mock_status: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test status command with no active Claude instances."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = []

        mock_status_instance = mock_status.return_value
        mock_summary = MagicMock()
        mock_summary.active_claudes = 0
        mock_summary.idle_claudes = 0
        mock_summary.blocked_claudes = 0
        mock_summary.total_claudes = 0
        mock_status_instance.get_summary.return_value = mock_summary

        # Act
        result = cli_runner.invoke(main, ["status"])

        # Assert
        assert result.exit_code == 0
        assert "0" in result.output or "no" in result.output.lower()

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.StatusTracker")
    def test_status_command_with_active_claudes(
        self,
        mock_status: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test status command with active Claude instances."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = [mock_worktree_info]

        mock_status_instance = mock_status.return_value
        mock_summary = MagicMock()
        mock_summary.active_claudes = 1
        mock_summary.idle_claudes = 0
        mock_summary.blocked_claudes = 0
        mock_summary.total_claudes = 1
        mock_status_instance.get_summary.return_value = mock_summary

        # Act
        result = cli_runner.invoke(main, ["status"])

        # Assert
        assert result.exit_code == 0
        assert "1" in result.output or "active" in result.output.lower()


class TestCleanupCommand:
    """Test 'owt cleanup' command."""

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.CleanupService")
    def test_cleanup_dry_run(
        self,
        mock_cleanup: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test cleanup command in dry-run mode."""
        # Arrange
        mock_cleanup_instance = mock_cleanup.return_value
        mock_report = MagicMock()
        mock_report.deleted = []
        mock_report.skipped = []
        mock_report.errors = []
        mock_cleanup_instance.cleanup.return_value = mock_report

        # Act
        result = cli_runner.invoke(main, ["cleanup", "--dry-run"])

        # Assert
        assert result.exit_code == 0
        mock_cleanup_instance.cleanup.assert_called_once_with(dry_run=True, force=False)
        assert "dry run" in result.output.lower() or "would delete" in result.output.lower()

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.CleanupService")
    def test_cleanup_with_force(
        self,
        mock_cleanup: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test cleanup command with force flag."""
        # Arrange
        mock_cleanup_instance = mock_cleanup.return_value
        mock_report = MagicMock()
        mock_report.deleted = []
        mock_report.skipped = []
        mock_report.errors = []
        mock_cleanup_instance.cleanup.return_value = mock_report

        # Act
        result = cli_runner.invoke(main, ["cleanup", "--force"])

        # Assert
        assert result.exit_code == 0
        mock_cleanup_instance.cleanup.assert_called_once_with(dry_run=False, force=True)


class TestDeleteCommand:
    """Test 'owt delete' command."""

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.TmuxManager")
    def test_delete_worktree_success(
        self,
        mock_tmux: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test deleting a worktree successfully."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = [mock_worktree_info]

        mock_tmux_instance = mock_tmux.return_value
        mock_tmux_instance.get_session_for_worktree.return_value = None

        # Act
        result = cli_runner.invoke(main, ["delete", "feature/test", "--force"])

        # Assert
        assert result.exit_code == 0
        mock_wt_instance.delete.assert_called_once()

    @patch("open_orchestrator.cli.WorktreeManager")
    def test_delete_nonexistent_worktree(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test deleting a nonexistent worktree fails."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = []

        # Act
        result = cli_runner.invoke(main, ["delete", "feature/nonexistent"])

        # Assert
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "no worktree" in result.output.lower()


class TestSwitchCommand:
    """Test 'owt switch' command."""

    @patch("open_orchestrator.cli.WorktreeManager")
    @patch("open_orchestrator.cli.TmuxManager")
    def test_switch_to_worktree_with_tmux(
        self,
        mock_tmux: MagicMock,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_info: WorktreeInfo,
    ) -> None:
        """Test switching to a worktree's tmux session."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = [mock_worktree_info]

        mock_tmux_instance = mock_tmux.return_value
        mock_session_info = MagicMock()
        mock_session_info.name = "owt-feature-test"
        mock_tmux_instance.get_session_for_worktree.return_value = mock_session_info
        mock_tmux_instance.is_inside_tmux.return_value = True

        # Act
        result = cli_runner.invoke(main, ["switch", "feature/test", "--tmux"])

        # Assert
        assert result.exit_code == 0
        mock_tmux_instance.attach_session.assert_called_once()

    @patch("open_orchestrator.cli.WorktreeManager")
    def test_switch_to_nonexistent_worktree(
        self,
        mock_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test switching to a nonexistent worktree fails."""
        # Arrange
        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.list_all.return_value = []

        # Act
        result = cli_runner.invoke(main, ["switch", "feature/nonexistent"])

        # Assert
        assert result.exit_code != 0


class TestPlanModeFlag:
    """Test --plan-mode flag integration in create command."""

    @patch("open_orchestrator.cli.sync_claude_md")
    @patch("open_orchestrator.cli.StatusTracker")
    @patch("open_orchestrator.cli.EnvironmentSetup")
    @patch("open_orchestrator.cli.ProjectDetector")
    @patch("open_orchestrator.cli.TmuxManager")
    @patch("open_orchestrator.cli.WorktreeManager")
    def test_create_with_plan_mode_flag(
        self,
        mock_wt_manager: MagicMock,
        mock_tmux: MagicMock,
        mock_detector: MagicMock,
        mock_env_setup: MagicMock,
        mock_status: MagicMock,
        mock_sync_claude_md: MagicMock,
        cli_runner: CliRunner,
        temp_directory: Path,
    ) -> None:
        """Test --plan-mode flag is passed to TmuxManager.create_worktree_session."""
        # Arrange
        mock_worktree_info = WorktreeInfo(
            path=temp_directory / "test-worktree",
            branch="feature/test",
            head_commit="abc123f",
            is_bare=False,
            is_detached=False,
            is_locked=False,
            lock_reason=None,
            prunable=None,
        )

        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.create.return_value = mock_worktree_info
        mock_wt_instance.repo.working_dir = "/fake/repo"

        mock_detector_instance = mock_detector.return_value
        mock_detector_instance.detect.return_value = None

        mock_tmux_instance = mock_tmux.return_value
        mock_session = MagicMock()
        mock_session.session_name = "owt-feature-test"
        mock_session.pane_count = 2
        mock_tmux_instance.create_worktree_session.return_value = mock_session

        mock_sync_claude_md.return_value = []

        # Act
        result = cli_runner.invoke(main, ["create", "feature/test", "--plan-mode", "--claude", "--no-deps", "--no-env"])

        # Assert
        assert result.exit_code == 0
        mock_tmux_instance.create_worktree_session.assert_called_once()
        call_kwargs = mock_tmux_instance.create_worktree_session.call_args[1]
        assert call_kwargs["plan_mode"] is True
        assert "plan mode" in result.output

    @patch("open_orchestrator.cli.sync_claude_md")
    @patch("open_orchestrator.cli.StatusTracker")
    @patch("open_orchestrator.cli.EnvironmentSetup")
    @patch("open_orchestrator.cli.ProjectDetector")
    @patch("open_orchestrator.cli.TmuxManager")
    @patch("open_orchestrator.cli.WorktreeManager")
    def test_create_without_plan_mode_flag(
        self,
        mock_wt_manager: MagicMock,
        mock_tmux: MagicMock,
        mock_detector: MagicMock,
        mock_env_setup: MagicMock,
        mock_status: MagicMock,
        mock_sync_claude_md: MagicMock,
        cli_runner: CliRunner,
        temp_directory: Path,
    ) -> None:
        """Test plan_mode defaults to False when --plan-mode flag not provided."""
        # Arrange
        mock_worktree_info = WorktreeInfo(
            path=temp_directory / "test-worktree",
            branch="feature/test",
            head_commit="abc123f",
            is_bare=False,
            is_detached=False,
            is_locked=False,
            lock_reason=None,
            prunable=None,
        )

        mock_wt_instance = mock_wt_manager.return_value
        mock_wt_instance.create.return_value = mock_worktree_info
        mock_wt_instance.repo.working_dir = "/fake/repo"

        mock_detector_instance = mock_detector.return_value
        mock_detector_instance.detect.return_value = None

        mock_tmux_instance = mock_tmux.return_value
        mock_session = MagicMock()
        mock_session.session_name = "owt-feature-test"
        mock_session.pane_count = 2
        mock_tmux_instance.create_worktree_session.return_value = mock_session

        mock_sync_claude_md.return_value = []

        # Act
        result = cli_runner.invoke(main, ["create", "feature/test", "--claude", "--no-deps", "--no-env"])

        # Assert
        assert result.exit_code == 0
        mock_tmux_instance.create_worktree_session.assert_called_once()
        call_kwargs = mock_tmux_instance.create_worktree_session.call_args[1]
        assert call_kwargs["plan_mode"] is False
        assert "plan mode" not in result.output


class TestShellCompletion:
    """Test shell completion generation commands."""

    def test_completion_bash_command(self, cli_runner: CliRunner) -> None:
        """Test 'owt completion bash' outputs bash completion script."""
        # Act
        result = cli_runner.invoke(main, ["completion", "bash"])

        # Assert
        assert result.exit_code == 0
        assert "_OWT_COMPLETE=bash_source owt" in result.output
        assert "eval" in result.output

    def test_completion_zsh_command(self, cli_runner: CliRunner) -> None:
        """Test 'owt completion zsh' outputs zsh completion script."""
        # Act
        result = cli_runner.invoke(main, ["completion", "zsh"])

        # Assert
        assert result.exit_code == 0
        assert "_OWT_COMPLETE=zsh_source owt" in result.output
        assert "eval" in result.output

    def test_completion_fish_command(self, cli_runner: CliRunner) -> None:
        """Test 'owt completion fish' outputs fish completion script."""
        # Act
        result = cli_runner.invoke(main, ["completion", "fish"])

        # Assert
        assert result.exit_code == 0
        assert "_OWT_COMPLETE=fish_source owt" in result.output
        assert "source" in result.output

    @patch.dict("os.environ", {"SHELL": "/bin/bash"})
    def test_completion_install_auto_detect_bash(self, cli_runner: CliRunner) -> None:
        """Test 'owt completion install' auto-detects bash shell."""
        # Act
        result = cli_runner.invoke(main, ["completion", "install"])

        # Assert
        assert result.exit_code == 0
        assert "bash" in result.output
        assert "~/.bashrc" in result.output

    @patch.dict("os.environ", {"SHELL": "/bin/zsh"})
    def test_completion_install_auto_detect_zsh(self, cli_runner: CliRunner) -> None:
        """Test 'owt completion install' auto-detects zsh shell."""
        # Act
        result = cli_runner.invoke(main, ["completion", "install"])

        # Assert
        assert result.exit_code == 0
        assert "zsh" in result.output
        assert "~/.zshrc" in result.output

    @patch.dict("os.environ", {"SHELL": "/usr/bin/fish"})
    def test_completion_install_auto_detect_fish(self, cli_runner: CliRunner) -> None:
        """Test 'owt completion install' auto-detects fish shell."""
        # Act
        result = cli_runner.invoke(main, ["completion", "install"])

        # Assert
        assert result.exit_code == 0
        assert "fish" in result.output
        assert "~/.config/fish/completions" in result.output

    def test_completion_install_explicit_shell(self, cli_runner: CliRunner) -> None:
        """Test 'owt completion install --shell' with explicit shell choice."""
        # Act
        result = cli_runner.invoke(main, ["completion", "install", "--shell", "zsh"])

        # Assert
        assert result.exit_code == 0
        assert "zsh" in result.output
        assert "~/.zshrc" in result.output

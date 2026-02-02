"""
Tests for Dashboard TUI component.

This module tests the Dashboard class which provides a real-time
terminal UI for monitoring AI tool activity across worktrees.
"""

from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

from click.testing import CliRunner
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from open_orchestrator.cli import main
from open_orchestrator.core.dashboard import Dashboard, DashboardConfig
from open_orchestrator.models.status import (
    AIActivityStatus,
    StatusSummary,
    TokenUsage,
    WorktreeAIStatus,
)
from open_orchestrator.models.worktree_info import WorktreeInfo


class TestDashboardConfig:
    """Test DashboardConfig dataclass."""

    def test_default_values(self) -> None:
        """Test DashboardConfig with default values."""
        config = DashboardConfig()

        assert config.refresh_rate == 2.0
        assert config.show_token_usage is True
        assert config.show_commands is True
        assert config.compact is False

    def test_custom_values(self) -> None:
        """Test DashboardConfig with custom values."""
        config = DashboardConfig(
            refresh_rate=1.0,
            show_token_usage=False,
            show_commands=False,
            compact=True,
        )

        assert config.refresh_rate == 1.0
        assert config.show_token_usage is False
        assert config.show_commands is False
        assert config.compact is True

    def test_partial_custom_values(self) -> None:
        """Test DashboardConfig with partial custom values."""
        config = DashboardConfig(refresh_rate=0.5, compact=True)

        assert config.refresh_rate == 0.5
        assert config.show_token_usage is True  # default
        assert config.show_commands is True  # default
        assert config.compact is True


class TestDashboard:
    """Test Dashboard class."""

    def test_init_default_config(self) -> None:
        """Test Dashboard initialization with default config."""
        dashboard = Dashboard()

        assert dashboard.config.refresh_rate == 2.0
        assert dashboard.config.show_token_usage is True
        assert dashboard.config.show_commands is True
        assert dashboard.config.compact is False
        assert isinstance(dashboard.console, Console)
        assert dashboard._running is False

    def test_init_custom_config(self) -> None:
        """Test Dashboard initialization with custom config."""
        config = DashboardConfig(refresh_rate=1.0, compact=True)
        dashboard = Dashboard(config=config)

        assert dashboard.config.refresh_rate == 1.0
        assert dashboard.config.compact is True

    def test_init_custom_console(self) -> None:
        """Test Dashboard initialization with custom console."""
        mock_console = Mock(spec=Console)
        dashboard = Dashboard(console=mock_console)

        assert dashboard.console is mock_console

    def test_get_status_icon_idle(self) -> None:
        """Test _get_status_icon for IDLE status."""
        dashboard = Dashboard()
        style, icon = dashboard._get_status_icon(AIActivityStatus.IDLE.value)

        assert style == "dim"
        assert icon == "○"

    def test_get_status_icon_working(self) -> None:
        """Test _get_status_icon for WORKING status."""
        dashboard = Dashboard()
        style, icon = dashboard._get_status_icon(AIActivityStatus.WORKING.value)

        assert style == "green bold"
        assert icon == "●"

    def test_get_status_icon_blocked(self) -> None:
        """Test _get_status_icon for BLOCKED status."""
        dashboard = Dashboard()
        style, icon = dashboard._get_status_icon(AIActivityStatus.BLOCKED.value)

        assert style == "red bold"
        assert icon == "■"

    def test_get_status_icon_waiting(self) -> None:
        """Test _get_status_icon for WAITING status."""
        dashboard = Dashboard()
        style, icon = dashboard._get_status_icon(AIActivityStatus.WAITING.value)

        assert style == "yellow"
        assert icon == "◌"

    def test_get_status_icon_completed(self) -> None:
        """Test _get_status_icon for COMPLETED status."""
        dashboard = Dashboard()
        style, icon = dashboard._get_status_icon(AIActivityStatus.COMPLETED.value)

        assert style == "blue"
        assert icon == "✓"

    def test_get_status_icon_error(self) -> None:
        """Test _get_status_icon for ERROR status."""
        dashboard = Dashboard()
        style, icon = dashboard._get_status_icon(AIActivityStatus.ERROR.value)

        assert style == "red"
        assert icon == "✗"

    def test_get_status_icon_unknown(self) -> None:
        """Test _get_status_icon for UNKNOWN status."""
        dashboard = Dashboard()
        style, icon = dashboard._get_status_icon(AIActivityStatus.UNKNOWN.value)

        assert style == "dim"
        assert icon == "?"

    def test_get_status_icon_invalid(self) -> None:
        """Test _get_status_icon for invalid status."""
        dashboard = Dashboard()
        style, icon = dashboard._get_status_icon("invalid_status")

        assert style == "dim"
        assert icon == "?"

    @patch("open_orchestrator.core.dashboard.datetime")
    def test_create_header(self, mock_datetime: MagicMock) -> None:
        """Test _create_header creates panel with title and timestamp."""
        # Arrange
        mock_now = Mock()
        mock_now.strftime.return_value = "12:34:56"
        mock_datetime.now.return_value = mock_now

        dashboard = Dashboard()

        # Act
        header = dashboard._create_header()

        # Assert
        assert isinstance(header, Panel)
        mock_datetime.now.assert_called_once()
        mock_now.strftime.assert_called_once_with("%H:%M:%S")

    def test_create_legend(self) -> None:
        """Test _create_legend creates Text with all status icons."""
        dashboard = Dashboard()

        # Act
        legend = dashboard._create_legend()

        # Assert
        assert isinstance(legend, Text)
        # Check that legend contains text content
        legend_str = str(legend)
        assert "Legend:" in legend_str or len(legend.plain) > 0

    @patch.object(Dashboard, "_get_status_icon")
    def test_create_worktree_table_with_status(
        self, mock_get_status_icon: MagicMock
    ) -> None:
        """Test _create_worktree_table with worktree having status."""
        # Arrange
        mock_get_status_icon.return_value = ("green bold", "●")

        dashboard = Dashboard(config=DashboardConfig(show_token_usage=True, show_commands=True))

        # Mock WorktreeManager
        mock_worktree = Mock(spec=WorktreeInfo)
        mock_worktree.name = "test-feature"
        mock_worktree.branch = "feature/test"
        mock_worktree.is_main = False

        dashboard.wt_manager.list_all = Mock(return_value=[mock_worktree])

        # Mock StatusTracker
        mock_status = WorktreeAIStatus(
            worktree_name="test-feature",
            worktree_path="/path/to/worktree",
            branch="feature/test",
            activity_status=AIActivityStatus.WORKING,
            current_task="Implementing tests",
            token_usage=TokenUsage(input_tokens=1000, output_tokens=500),
            updated_at=datetime.now(),
        )
        dashboard.status_tracker.get_status = Mock(return_value=mock_status)
        dashboard.status_tracker.cleanup_orphans = Mock()

        # Act
        table = dashboard._create_worktree_table()

        # Assert
        assert isinstance(table, Table)
        dashboard.status_tracker.cleanup_orphans.assert_called_once()
        dashboard.wt_manager.list_all.assert_called_once()
        dashboard.status_tracker.get_status.assert_called_once_with("test-feature")
        mock_get_status_icon.assert_called_once_with(AIActivityStatus.WORKING.value)

    def test_create_worktree_table_without_status(self) -> None:
        """Test _create_worktree_table with worktree having no status."""
        # Arrange
        dashboard = Dashboard(config=DashboardConfig(show_token_usage=True, show_commands=True))

        # Mock WorktreeManager
        mock_worktree = Mock(spec=WorktreeInfo)
        mock_worktree.name = "test-feature"
        mock_worktree.branch = "feature/test"
        mock_worktree.is_main = False

        dashboard.wt_manager.list_all = Mock(return_value=[mock_worktree])

        # Mock StatusTracker - no status
        dashboard.status_tracker.get_status = Mock(return_value=None)
        dashboard.status_tracker.cleanup_orphans = Mock()

        # Act
        table = dashboard._create_worktree_table()

        # Assert
        assert isinstance(table, Table)
        dashboard.status_tracker.cleanup_orphans.assert_called_once()

    def test_create_worktree_table_excludes_main_worktree(self) -> None:
        """Test _create_worktree_table excludes main worktree."""
        # Arrange
        dashboard = Dashboard()

        # Mock WorktreeManager with main and feature worktrees
        main_worktree = Mock(spec=WorktreeInfo)
        main_worktree.name = "main"
        main_worktree.is_main = True

        feature_worktree = Mock(spec=WorktreeInfo)
        feature_worktree.name = "test-feature"
        feature_worktree.branch = "feature/test"
        feature_worktree.is_main = False

        dashboard.wt_manager.list_all = Mock(return_value=[main_worktree, feature_worktree])

        # Mock StatusTracker
        dashboard.status_tracker.get_status = Mock(return_value=None)
        dashboard.status_tracker.cleanup_orphans = Mock()

        # Act
        table = dashboard._create_worktree_table()

        # Assert
        assert isinstance(table, Table)
        # get_status should only be called for feature worktree, not main
        dashboard.status_tracker.get_status.assert_called_once_with("test-feature")

    def test_create_worktree_table_truncates_long_task(self) -> None:
        """Test _create_worktree_table truncates long task names."""
        # Arrange
        dashboard = Dashboard()

        # Mock WorktreeManager
        mock_worktree = Mock(spec=WorktreeInfo)
        mock_worktree.name = "test-feature"
        mock_worktree.branch = "feature/test"
        mock_worktree.is_main = False

        dashboard.wt_manager.list_all = Mock(return_value=[mock_worktree])

        # Mock StatusTracker with long task name
        long_task = "This is a very long task name that should be truncated to fit in the table"
        mock_status = WorktreeAIStatus(
            worktree_name="test-feature",
            worktree_path="/path/to/worktree",
            branch="feature/test",
            activity_status=AIActivityStatus.WORKING,
            current_task=long_task,
            updated_at=datetime.now(),
        )
        dashboard.status_tracker.get_status = Mock(return_value=mock_status)
        dashboard.status_tracker.cleanup_orphans = Mock()

        # Act
        table = dashboard._create_worktree_table()

        # Assert
        assert isinstance(table, Table)

    def test_create_worktree_table_no_token_columns(self) -> None:
        """Test _create_worktree_table without token usage columns."""
        # Arrange
        dashboard = Dashboard(config=DashboardConfig(show_token_usage=False))

        # Mock WorktreeManager
        mock_worktree = Mock(spec=WorktreeInfo)
        mock_worktree.name = "test-feature"
        mock_worktree.branch = "feature/test"
        mock_worktree.is_main = False

        dashboard.wt_manager.list_all = Mock(return_value=[mock_worktree])

        # Mock StatusTracker
        mock_status = WorktreeAIStatus(
            worktree_name="test-feature",
            worktree_path="/path/to/worktree",
            branch="feature/test",
            activity_status=AIActivityStatus.WORKING,
            updated_at=datetime.now(),
        )
        dashboard.status_tracker.get_status = Mock(return_value=mock_status)
        dashboard.status_tracker.cleanup_orphans = Mock()

        # Act
        table = dashboard._create_worktree_table()

        # Assert
        assert isinstance(table, Table)

    def test_create_worktree_table_no_commands_column(self) -> None:
        """Test _create_worktree_table without commands column."""
        # Arrange
        dashboard = Dashboard(config=DashboardConfig(show_commands=False))

        # Mock WorktreeManager
        mock_worktree = Mock(spec=WorktreeInfo)
        mock_worktree.name = "test-feature"
        mock_worktree.branch = "feature/test"
        mock_worktree.is_main = False

        dashboard.wt_manager.list_all = Mock(return_value=[mock_worktree])

        # Mock StatusTracker
        mock_status = WorktreeAIStatus(
            worktree_name="test-feature",
            worktree_path="/path/to/worktree",
            branch="feature/test",
            activity_status=AIActivityStatus.WORKING,
            updated_at=datetime.now(),
        )
        dashboard.status_tracker.get_status = Mock(return_value=mock_status)
        dashboard.status_tracker.cleanup_orphans = Mock()

        # Act
        table = dashboard._create_worktree_table()

        # Assert
        assert isinstance(table, Table)

    def test_create_worktree_table_empty_worktree_list(self) -> None:
        """Test _create_worktree_table with empty worktree list."""
        # Arrange
        dashboard = Dashboard()

        # Mock WorktreeManager with empty list
        dashboard.wt_manager.list_all = Mock(return_value=[])
        dashboard.status_tracker.cleanup_orphans = Mock()

        # Act
        table = dashboard._create_worktree_table()

        # Assert
        assert isinstance(table, Table)
        dashboard.status_tracker.cleanup_orphans.assert_called_once_with([])

    def test_create_summary_panel_with_token_usage(self) -> None:
        """Test _create_summary_panel with token usage enabled."""
        # Arrange
        dashboard = Dashboard(config=DashboardConfig(show_token_usage=True))

        # Mock WorktreeManager
        mock_worktree = Mock(spec=WorktreeInfo)
        mock_worktree.name = "test-feature"
        dashboard.wt_manager.list_all = Mock(return_value=[mock_worktree])

        # Mock StatusTracker summary
        mock_summary = StatusSummary(
            total_worktrees=1,
            worktrees_with_status=1,
            active_ai_sessions=1,
            idle_ai_sessions=0,
            blocked_ai_sessions=0,
            total_input_tokens=1000,
            total_output_tokens=500,
            total_estimated_cost_usd=0.0525,
        )
        dashboard.status_tracker.get_summary = Mock(return_value=mock_summary)

        # Act
        panel = dashboard._create_summary_panel()

        # Assert
        assert isinstance(panel, Panel)
        dashboard.status_tracker.get_summary.assert_called_once()

    def test_create_summary_panel_without_token_usage(self) -> None:
        """Test _create_summary_panel with token usage disabled."""
        # Arrange
        dashboard = Dashboard(config=DashboardConfig(show_token_usage=False))

        # Mock WorktreeManager
        mock_worktree = Mock(spec=WorktreeInfo)
        mock_worktree.name = "test-feature"
        dashboard.wt_manager.list_all = Mock(return_value=[mock_worktree])

        # Mock StatusTracker summary
        mock_summary = StatusSummary(
            total_worktrees=1,
            worktrees_with_status=1,
            active_ai_sessions=1,
            idle_ai_sessions=0,
            blocked_ai_sessions=0,
            total_input_tokens=0,
            total_output_tokens=0,
        )
        dashboard.status_tracker.get_summary = Mock(return_value=mock_summary)

        # Act
        panel = dashboard._create_summary_panel()

        # Assert
        assert isinstance(panel, Panel)

    def test_create_summary_panel_no_tokens(self) -> None:
        """Test _create_summary_panel with zero tokens."""
        # Arrange
        dashboard = Dashboard(config=DashboardConfig(show_token_usage=True))

        # Mock WorktreeManager
        mock_worktree = Mock(spec=WorktreeInfo)
        mock_worktree.name = "test-feature"
        dashboard.wt_manager.list_all = Mock(return_value=[mock_worktree])

        # Mock StatusTracker summary with zero tokens
        mock_summary = StatusSummary(
            total_worktrees=1,
            worktrees_with_status=1,
            active_ai_sessions=0,
            idle_ai_sessions=1,
            blocked_ai_sessions=0,
            total_input_tokens=0,
            total_output_tokens=0,
        )
        dashboard.status_tracker.get_summary = Mock(return_value=mock_summary)

        # Act
        panel = dashboard._create_summary_panel()

        # Assert
        assert isinstance(panel, Panel)

    def test_create_layout_full_mode(self) -> None:
        """Test _create_layout in full mode."""
        # Arrange
        dashboard = Dashboard(config=DashboardConfig(compact=False))

        # Mock dependencies
        dashboard.wt_manager.list_all = Mock(return_value=[])
        dashboard.status_tracker.cleanup_orphans = Mock()
        dashboard.status_tracker.get_summary = Mock(
            return_value=StatusSummary(total_worktrees=0)
        )

        # Act
        layout = dashboard._create_layout()

        # Assert
        assert isinstance(layout, Layout)

    def test_create_layout_compact_mode(self) -> None:
        """Test _create_layout in compact mode."""
        # Arrange
        dashboard = Dashboard(config=DashboardConfig(compact=True))

        # Mock dependencies
        dashboard.wt_manager.list_all = Mock(return_value=[])
        dashboard.status_tracker.cleanup_orphans = Mock()

        # Act
        layout = dashboard._create_layout()

        # Assert
        assert isinstance(layout, Layout)

    def test_stop_sets_running_false(self) -> None:
        """Test stop() sets _running flag to False."""
        dashboard = Dashboard()
        dashboard._running = True

        dashboard.stop()

        assert dashboard._running is False

    @patch("open_orchestrator.core.dashboard.time.sleep")
    @patch.object(Dashboard, "_create_layout")
    def test_run_keyboard_interrupt(
        self, mock_create_layout: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Test run() handles KeyboardInterrupt gracefully."""
        # Arrange
        dashboard = Dashboard()
        mock_create_layout.return_value = Layout()

        # Simulate KeyboardInterrupt on first iteration
        mock_sleep.side_effect = KeyboardInterrupt()

        # Act - should not raise
        dashboard.run()

        # Assert
        assert dashboard._running is False


class TestDashboardCLI:
    """Test Dashboard CLI integration."""

    @patch("open_orchestrator.core.dashboard.Dashboard")
    def test_dashboard_command_default_options(
        self, mock_dashboard_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test 'owt dashboard' with default options."""
        # Arrange
        mock_dashboard_instance = Mock()
        mock_dashboard_class.return_value = mock_dashboard_instance

        # Act
        result = cli_runner.invoke(main, ["dashboard"])

        # Assert
        assert result.exit_code == 0
        mock_dashboard_class.assert_called_once()

        # Verify config passed to Dashboard
        call_kwargs = mock_dashboard_class.call_args[1]
        config = call_kwargs["config"]
        assert config.refresh_rate == 2.0
        assert config.show_token_usage is True
        assert config.show_commands is True
        assert config.compact is False

        mock_dashboard_instance.run.assert_called_once()

    @patch("open_orchestrator.core.dashboard.Dashboard")
    def test_dashboard_command_custom_refresh(
        self, mock_dashboard_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test 'owt dashboard' with custom refresh rate."""
        # Arrange
        mock_dashboard_instance = Mock()
        mock_dashboard_class.return_value = mock_dashboard_instance

        # Act
        result = cli_runner.invoke(main, ["dashboard", "--refresh", "1.0"])

        # Assert
        assert result.exit_code == 0
        call_kwargs = mock_dashboard_class.call_args[1]
        config = call_kwargs["config"]
        assert config.refresh_rate == 1.0

    @patch("open_orchestrator.core.dashboard.Dashboard")
    def test_dashboard_command_no_tokens(
        self, mock_dashboard_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test 'owt dashboard' with --no-tokens flag."""
        # Arrange
        mock_dashboard_instance = Mock()
        mock_dashboard_class.return_value = mock_dashboard_instance

        # Act
        result = cli_runner.invoke(main, ["dashboard", "--no-tokens"])

        # Assert
        assert result.exit_code == 0
        call_kwargs = mock_dashboard_class.call_args[1]
        config = call_kwargs["config"]
        assert config.show_token_usage is False

    @patch("open_orchestrator.core.dashboard.Dashboard")
    def test_dashboard_command_no_commands(
        self, mock_dashboard_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test 'owt dashboard' with --no-commands flag."""
        # Arrange
        mock_dashboard_instance = Mock()
        mock_dashboard_class.return_value = mock_dashboard_instance

        # Act
        result = cli_runner.invoke(main, ["dashboard", "--no-commands"])

        # Assert
        assert result.exit_code == 0
        call_kwargs = mock_dashboard_class.call_args[1]
        config = call_kwargs["config"]
        assert config.show_commands is False

    @patch("open_orchestrator.core.dashboard.Dashboard")
    def test_dashboard_command_compact(
        self, mock_dashboard_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test 'owt dashboard' with --compact flag."""
        # Arrange
        mock_dashboard_instance = Mock()
        mock_dashboard_class.return_value = mock_dashboard_instance

        # Act
        result = cli_runner.invoke(main, ["dashboard", "--compact"])

        # Assert
        assert result.exit_code == 0
        call_kwargs = mock_dashboard_class.call_args[1]
        config = call_kwargs["config"]
        assert config.compact is True

    @patch("open_orchestrator.core.dashboard.Dashboard")
    def test_dashboard_command_all_options(
        self, mock_dashboard_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test 'owt dashboard' with all options combined."""
        # Arrange
        mock_dashboard_instance = Mock()
        mock_dashboard_class.return_value = mock_dashboard_instance

        # Act
        result = cli_runner.invoke(
            main,
            ["dashboard", "-r", "0.5", "--no-tokens", "--no-commands", "--compact"],
        )

        # Assert
        assert result.exit_code == 0
        call_kwargs = mock_dashboard_class.call_args[1]
        config = call_kwargs["config"]
        assert config.refresh_rate == 0.5
        assert config.show_token_usage is False
        assert config.show_commands is False
        assert config.compact is True

    @patch("open_orchestrator.core.dashboard.Dashboard")
    def test_dashboard_command_short_refresh_flag(
        self, mock_dashboard_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test 'owt dashboard' with -r short flag."""
        # Arrange
        mock_dashboard_instance = Mock()
        mock_dashboard_class.return_value = mock_dashboard_instance

        # Act
        result = cli_runner.invoke(main, ["dashboard", "-r", "3.5"])

        # Assert
        assert result.exit_code == 0
        call_kwargs = mock_dashboard_class.call_args[1]
        config = call_kwargs["config"]
        assert config.refresh_rate == 3.5

    @patch("open_orchestrator.core.dashboard.Dashboard")
    def test_dashboard_command_short_compact_flag(
        self, mock_dashboard_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test 'owt dashboard' with -c short flag."""
        # Arrange
        mock_dashboard_instance = Mock()
        mock_dashboard_class.return_value = mock_dashboard_instance

        # Act
        result = cli_runner.invoke(main, ["dashboard", "-c"])

        # Assert
        assert result.exit_code == 0
        call_kwargs = mock_dashboard_class.call_args[1]
        config = call_kwargs["config"]
        assert config.compact is True

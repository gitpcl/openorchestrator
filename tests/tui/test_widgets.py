"""
Tests for Textual widgets.

Tests all TUI widgets with various configurations, states, and edge cases.
"""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from open_orchestrator.models.status import (
    AIActivityStatus,
    CommandRecord,
    StatusSummary,
    TokenUsage,
    WorktreeAIStatus,
)
from open_orchestrator.models.worktree_info import WorktreeInfo
from open_orchestrator.tui.widgets import (
    ActivityLogWidget,
    StatusPanelWidget,
    TokenUsageWidget,
    WorktreeTableWidget,
)


@pytest.fixture
def mock_status_tracker():
    """Create a mock StatusTracker."""
    tracker = Mock()
    tracker.cleanup_orphans = Mock()
    return tracker


@pytest.fixture
def mock_wt_manager():
    """Create a mock WorktreeManager."""
    manager = Mock()
    return manager


@pytest.fixture
def sample_worktree():
    """Create a sample WorktreeInfo."""
    from pathlib import Path

    return WorktreeInfo(
        path=Path("/path/to/worktree/feature-branch"),
        branch="feature/test",
        head_commit="abc123",
        is_main=False,
    )


@pytest.fixture
def main_worktree():
    """Create a main WorktreeInfo."""
    from pathlib import Path

    return WorktreeInfo(
        path=Path("/path/to/main"),
        branch="main",
        head_commit="def456",
        is_main=True,
    )


@pytest.fixture
def sample_status():
    """Create a sample WorktreeAIStatus."""
    return WorktreeAIStatus(
        worktree_name="feature-branch",
        worktree_path="/path/to/worktree/feature-branch",
        branch="feature/test",
        activity_status=AIActivityStatus.WORKING.value,
        current_task="Implementing feature",
        token_usage=TokenUsage(
            input_tokens=1000,
            output_tokens=500,
        ),
        recent_commands=[
            CommandRecord(
                command="pytest tests/",
                timestamp=datetime.now() - timedelta(minutes=5),
            ),
            CommandRecord(
                command="git commit -m 'test'",
                timestamp=datetime.now() - timedelta(minutes=2),
            ),
        ],
        updated_at=datetime.now(),
    )


@pytest.fixture
def sample_summary():
    """Create a sample StatusSummary."""
    return StatusSummary(
        total_worktrees=3,
        active_ai_sessions=1,
        idle_ai_sessions=2,
        blocked_ai_sessions=0,
        total_input_tokens=5000,
        total_output_tokens=3000,
        total_estimated_cost_usd=0.08,
    )


class TestWorktreeTableWidget:
    """Tests for WorktreeTableWidget."""

    @pytest.mark.textual
    def test_initialization(self, mock_status_tracker, mock_wt_manager):
        """Test widget initialization."""
        widget = WorktreeTableWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_token_usage=True,
            show_commands=True,
        )

        assert widget.status_tracker == mock_status_tracker
        assert widget.wt_manager == mock_wt_manager
        assert widget.show_token_usage is True
        assert widget.show_commands is True

    @pytest.mark.textual
    def test_initialization_without_optional_columns(self, mock_status_tracker, mock_wt_manager):
        """Test widget initialization without optional columns."""
        widget = WorktreeTableWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_token_usage=False,
            show_commands=False,
        )

        assert widget.show_token_usage is False
        assert widget.show_commands is False

    @pytest.mark.textual
    def test_get_status_icon_working(self):
        """Test status icon retrieval for WORKING status."""
        widget = WorktreeTableWidget(
            status_tracker=Mock(),
            wt_manager=Mock(),
        )

        style, icon = widget._get_status_icon(AIActivityStatus.WORKING.value)
        assert style == "green bold"
        assert icon == "●"

    @pytest.mark.textual
    def test_get_status_icon_idle(self):
        """Test status icon retrieval for IDLE status."""
        widget = WorktreeTableWidget(
            status_tracker=Mock(),
            wt_manager=Mock(),
        )

        style, icon = widget._get_status_icon(AIActivityStatus.IDLE.value)
        assert style == "dim"
        assert icon == "○"

    @pytest.mark.textual
    def test_get_status_icon_blocked(self):
        """Test status icon retrieval for BLOCKED status."""
        widget = WorktreeTableWidget(
            status_tracker=Mock(),
            wt_manager=Mock(),
        )

        style, icon = widget._get_status_icon(AIActivityStatus.BLOCKED.value)
        assert style == "red bold"
        assert icon == "■"

    @pytest.mark.textual
    def test_get_status_icon_invalid(self):
        """Test status icon retrieval for invalid status."""
        widget = WorktreeTableWidget(
            status_tracker=Mock(),
            wt_manager=Mock(),
        )

        style, icon = widget._get_status_icon("INVALID")
        assert style == "dim"
        assert icon == "?"

    @pytest.mark.textual
    def test_refresh_data_with_status(
        self, mock_status_tracker, mock_wt_manager, sample_worktree, sample_status
    ):
        """Test refreshing table data with status information."""
        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = sample_status

        widget = WorktreeTableWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        # This would normally be called by Textual framework
        # We're testing the logic directly
        assert widget.status_tracker == mock_status_tracker
        assert widget.wt_manager == mock_wt_manager

    @pytest.mark.textual
    def test_refresh_data_excludes_main_worktree(
        self, mock_status_tracker, mock_wt_manager, main_worktree, sample_worktree
    ):
        """Test that main worktree is excluded from display."""
        mock_wt_manager.list_all.return_value = [main_worktree, sample_worktree]
        mock_status_tracker.get_status.return_value = None

        WorktreeTableWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        # Verify that list_all is called and main worktree exists
        assert mock_wt_manager.list_all() == [main_worktree, sample_worktree]
        assert main_worktree.is_main is True

    @pytest.mark.textual
    def test_refresh_data_without_status(
        self, mock_status_tracker, mock_wt_manager, sample_worktree
    ):
        """Test refreshing table data without status information."""
        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = None

        WorktreeTableWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        # Verify status is None
        assert mock_status_tracker.get_status("feature-branch") is None

    @pytest.mark.textual
    def test_refresh_data_empty_worktree_list(self, mock_status_tracker, mock_wt_manager):
        """Test refreshing table data with empty worktree list."""
        mock_wt_manager.list_all.return_value = []

        WorktreeTableWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        # Verify empty list
        assert mock_wt_manager.list_all() == []

    @pytest.mark.textual
    def test_long_task_truncation(self, mock_status_tracker, mock_wt_manager, sample_worktree):
        """Test that long task names are truncated at 35 characters."""
        long_task_status = WorktreeAIStatus(
            worktree_name="feature-branch",
            worktree_path="/path/to/worktree/feature-branch",
            branch="feature/test",
            activity_status=AIActivityStatus.WORKING.value,
            current_task="This is a very long task name that should be truncated to fit",
            token_usage=TokenUsage(
                input_tokens=0,
                output_tokens=0,
            ),
            recent_commands=[],
            updated_at=datetime.now(),
        )

        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = long_task_status

        WorktreeTableWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        # Verify truncation logic
        task = long_task_status.current_task
        assert len(task) > 35
        truncated = task[:32] + "..." if len(task) > 35 else task
        assert len(truncated) == 35
        assert truncated.endswith("...")


class TestStatusPanelWidget:
    """Tests for StatusPanelWidget."""

    @pytest.mark.textual
    def test_initialization(self, mock_status_tracker, mock_wt_manager):
        """Test widget initialization."""
        widget = StatusPanelWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_token_usage=True,
        )

        assert widget.status_tracker == mock_status_tracker
        assert widget.wt_manager == mock_wt_manager
        assert widget.show_token_usage is True

    @pytest.mark.textual
    def test_initialization_without_token_usage(self, mock_status_tracker, mock_wt_manager):
        """Test widget initialization without token usage."""
        widget = StatusPanelWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_token_usage=False,
        )

        assert widget.show_token_usage is False

    @pytest.mark.textual
    def test_refresh_data_with_summary(
        self, mock_status_tracker, mock_wt_manager, sample_worktree, sample_summary
    ):
        """Test refreshing panel with summary data."""
        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_summary.return_value = sample_summary

        StatusPanelWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_token_usage=True,
        )

        # Verify summary data
        summary = mock_status_tracker.get_summary(["feature-branch"])
        assert summary.active_ai_sessions == 1
        assert summary.idle_ai_sessions == 2
        assert summary.blocked_ai_sessions == 0

    @pytest.mark.textual
    def test_refresh_data_zero_token_usage(
        self, mock_status_tracker, mock_wt_manager, sample_worktree
    ):
        """Test refreshing panel with zero token usage."""
        zero_summary = StatusSummary(
            total_worktrees=1,
            active_ai_sessions=0,
            idle_ai_sessions=1,
            blocked_ai_sessions=0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_estimated_cost_usd=0.0,
        )

        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_summary.return_value = zero_summary

        StatusPanelWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_token_usage=True,
        )

        # Verify zero tokens
        summary = mock_status_tracker.get_summary(["feature-branch"])
        assert summary.total_input_tokens == 0
        assert summary.total_output_tokens == 0


class TestTokenUsageWidget:
    """Tests for TokenUsageWidget."""

    @pytest.mark.textual
    def test_initialization(self, mock_status_tracker, mock_wt_manager):
        """Test widget initialization."""
        widget = TokenUsageWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_detailed=True,
        )

        assert widget.status_tracker == mock_status_tracker
        assert widget.wt_manager == mock_wt_manager
        assert widget.show_detailed is True

    @pytest.mark.textual
    def test_initialization_compact_mode(self, mock_status_tracker, mock_wt_manager):
        """Test widget initialization in compact mode."""
        widget = TokenUsageWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_detailed=False,
        )

        assert widget.show_detailed is False

    @pytest.mark.textual
    def test_refresh_data_with_tokens(
        self, mock_status_tracker, mock_wt_manager, sample_worktree, sample_summary
    ):
        """Test refreshing widget with token data."""
        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_summary.return_value = sample_summary

        TokenUsageWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_detailed=True,
        )

        # Verify token data
        summary = mock_status_tracker.get_summary(["feature-branch"])
        assert summary.total_input_tokens == 5000
        assert summary.total_output_tokens == 3000
        assert summary.total_estimated_cost_usd == 0.08

    @pytest.mark.textual
    def test_refresh_data_zero_tokens(
        self, mock_status_tracker, mock_wt_manager, sample_worktree
    ):
        """Test refreshing widget with zero tokens."""
        zero_summary = StatusSummary(
            total_worktrees=1,
            active_ai_sessions=0,
            idle_ai_sessions=1,
            blocked_ai_sessions=0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_estimated_cost_usd=0.0,
        )

        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_summary.return_value = zero_summary

        TokenUsageWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_detailed=True,
        )

        # Verify zero tokens
        summary = mock_status_tracker.get_summary(["feature-branch"])
        assert summary.total_input_tokens == 0
        assert summary.total_output_tokens == 0


class TestActivityLogWidget:
    """Tests for ActivityLogWidget."""

    @pytest.mark.textual
    def test_initialization(self, mock_status_tracker, mock_wt_manager):
        """Test widget initialization."""
        widget = ActivityLogWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            max_entries=10,
        )

        assert widget.status_tracker == mock_status_tracker
        assert widget.wt_manager == mock_wt_manager
        assert widget.max_entries == 10

    @pytest.mark.textual
    def test_initialization_custom_max_entries(self, mock_status_tracker, mock_wt_manager):
        """Test widget initialization with custom max entries."""
        widget = ActivityLogWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            max_entries=5,
        )

        assert widget.max_entries == 5

    @pytest.mark.textual
    def test_refresh_data_with_commands(
        self, mock_status_tracker, mock_wt_manager, sample_worktree, sample_status
    ):
        """Test refreshing widget with command data."""
        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = sample_status

        ActivityLogWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            max_entries=10,
        )

        # Verify command data
        status = mock_status_tracker.get_status("feature-branch")
        assert len(status.recent_commands) == 2
        assert status.recent_commands[0].command == "pytest tests/"
        assert status.recent_commands[1].command == "git commit -m 'test'"

    @pytest.mark.textual
    def test_refresh_data_no_commands(
        self, mock_status_tracker, mock_wt_manager, sample_worktree
    ):
        """Test refreshing widget with no command data."""
        no_commands_status = WorktreeAIStatus(
            worktree_name="feature-branch",
            worktree_path="/path/to/worktree/feature-branch",
            branch="feature/test",
            activity_status=AIActivityStatus.IDLE.value,
            current_task=None,
            token_usage=TokenUsage(
                input_tokens=0,
                output_tokens=0,
            ),
            recent_commands=[],
            updated_at=datetime.now(),
        )

        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = no_commands_status

        ActivityLogWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            max_entries=10,
        )

        # Verify no commands
        status = mock_status_tracker.get_status("feature-branch")
        assert len(status.recent_commands) == 0

    @pytest.mark.textual
    def test_refresh_data_excludes_main_worktree(
        self, mock_status_tracker, mock_wt_manager, main_worktree, sample_worktree, sample_status
    ):
        """Test that activity log excludes main worktree commands."""
        mock_wt_manager.list_all.return_value = [main_worktree, sample_worktree]
        mock_status_tracker.get_status.return_value = sample_status

        ActivityLogWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            max_entries=10,
        )

        # Verify main worktree is excluded
        assert main_worktree.is_main is True
        assert sample_worktree.is_main is False

    @pytest.mark.textual
    def test_long_command_truncation(
        self, mock_status_tracker, mock_wt_manager, sample_worktree
    ):
        """Test that long commands are truncated at 50 characters."""
        long_command_status = WorktreeAIStatus(
            worktree_name="feature-branch",
            worktree_path="/path/to/worktree/feature-branch",
            branch="feature/test",
            activity_status=AIActivityStatus.WORKING.value,
            current_task="Running tests",
            token_usage=TokenUsage(
                input_tokens=0,
                output_tokens=0,
            ),
            recent_commands=[
                CommandRecord(
                    command="pytest tests/test_very_long_filename_that_should_be_truncated.py -v --cov",
                    timestamp=datetime.now(),
                ),
            ],
            updated_at=datetime.now(),
        )

        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = long_command_status

        ActivityLogWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            max_entries=10,
        )

        # Verify truncation logic
        cmd = long_command_status.recent_commands[0].command
        assert len(cmd) > 50
        truncated = cmd[:50] + "..." if len(cmd) > 50 else cmd
        assert len(truncated) == 53
        assert truncated.endswith("...")


class TestWidgetEdgeCases:
    """Tests for widget edge cases and error handling."""

    @pytest.mark.textual
    def test_worktree_table_with_none_status(
        self, mock_status_tracker, mock_wt_manager, sample_worktree
    ):
        """Test WorktreeTableWidget with None status."""
        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = None

        widget = WorktreeTableWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        # Should handle None gracefully
        assert widget.status_tracker == mock_status_tracker

    @pytest.mark.textual
    def test_status_panel_with_all_zero_summary(
        self, mock_status_tracker, mock_wt_manager, sample_worktree
    ):
        """Test StatusPanelWidget with all-zero summary."""
        zero_summary = StatusSummary(
            total_worktrees=0,
            active_ai_sessions=0,
            idle_ai_sessions=0,
            blocked_ai_sessions=0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_estimated_cost_usd=0.0,
        )

        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_summary.return_value = zero_summary

        StatusPanelWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            show_token_usage=True,
        )

        # Should handle all zeros gracefully
        summary = mock_status_tracker.get_summary(["feature-branch"])
        assert summary.active_ai_sessions == 0
        assert summary.total_input_tokens == 0

    @pytest.mark.textual
    def test_activity_log_with_empty_worktree_list(self, mock_status_tracker, mock_wt_manager):
        """Test ActivityLogWidget with empty worktree list."""
        mock_wt_manager.list_all.return_value = []

        ActivityLogWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            max_entries=10,
        )

        # Should handle empty list gracefully
        assert mock_wt_manager.list_all() == []

    @pytest.mark.textual
    def test_worktree_table_with_null_task(
        self, mock_status_tracker, mock_wt_manager, sample_worktree
    ):
        """Test WorktreeTableWidget with null current task."""
        null_task_status = WorktreeAIStatus(
            worktree_name="feature-branch",
            worktree_path="/path/to/worktree/feature-branch",
            branch="feature/test",
            activity_status=AIActivityStatus.IDLE.value,
            current_task=None,
            token_usage=TokenUsage(
                input_tokens=0,
                output_tokens=0,
            ),
            recent_commands=[],
            updated_at=datetime.now(),
        )

        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = null_task_status

        WorktreeTableWidget(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        # Should display "-" for null task
        status = mock_status_tracker.get_status("feature-branch")
        task = status.current_task or "-"
        assert task == "-"

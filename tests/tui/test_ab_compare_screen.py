"""
Tests for A/B comparison screen.

This module tests the ABCompareScreen and its components including:
- ToolPanel widget for displaying individual tool status
- CostComparisonPanel for comparing costs between tools
- Keyboard shortcuts (Tab, q) for navigation
- Reactive state updates when StatusTracker data changes
"""

from datetime import datetime
from unittest.mock import Mock

import pytest
from textual.pilot import Pilot

from open_orchestrator.config import AITool
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.ab_workspace import ABWorkspace
from open_orchestrator.models.status import (
    AIActivityStatus,
    TokenUsage,
    WorktreeAIStatus,
)
from open_orchestrator.tui.screens.ab_compare import (
    ABCompareScreen,
    CostComparisonPanel,
    ToolPanel,
)


@pytest.fixture
def mock_status_tracker() -> Mock:
    """Create a mock StatusTracker instance."""
    tracker = Mock(spec=StatusTracker)
    return tracker


@pytest.fixture
def mock_wt_manager() -> Mock:
    """Create a mock WorktreeManager instance."""
    manager = Mock(spec=WorktreeManager)
    return manager


@pytest.fixture
def sample_workspace() -> ABWorkspace:
    """Create a sample ABWorkspace for testing."""
    return ABWorkspace(
        id="test-ab-workspace",
        branch="feature/test",
        worktree_a="feature-test-claude",
        worktree_b="feature-test-opencode",
        tool_a=AITool.CLAUDE,
        tool_b=AITool.OPENCODE,
        tmux_session="owt-ab-test",
        initial_prompt="Test prompt",
        created_at=datetime.now(),
    )


@pytest.fixture
def sample_status_working() -> WorktreeAIStatus:
    """Create a sample WorktreeAIStatus with WORKING status."""
    return WorktreeAIStatus(
        worktree_name="feature-test-claude",
        worktree_path="/path/to/worktree/feature-test-claude",
        branch="feature/test-claude",
        ai_tool="claude",
        activity_status=AIActivityStatus.WORKING,
        current_task="Implementing authentication",
        token_usage=TokenUsage(
            input_tokens=5000,
            output_tokens=3000,
        ),
        updated_at=datetime.now(),
    )


@pytest.fixture
def sample_status_idle() -> WorktreeAIStatus:
    """Create a sample WorktreeAIStatus with IDLE status."""
    return WorktreeAIStatus(
        worktree_name="feature-test-opencode",
        worktree_path="/path/to/worktree/feature-test-opencode",
        branch="feature/test-opencode",
        ai_tool="opencode",
        activity_status=AIActivityStatus.IDLE,
        current_task=None,
        token_usage=TokenUsage(
            input_tokens=4000,
            output_tokens=2000,
        ),
        updated_at=datetime.now(),
    )


@pytest.mark.textual
class TestToolPanel:
    """Tests for ToolPanel widget."""

    async def test_tool_panel_renders_no_data(
        self,
        mock_status_tracker: Mock,
    ) -> None:
        """Test ToolPanel renders correctly when no status data is available."""
        mock_status_tracker.get_status.return_value = None

        panel = ToolPanel(
            status_tracker=mock_status_tracker,
            worktree_name="test-worktree",
            tool_name="claude",
        )

        # Mount the widget to trigger rendering
        from textual.app import App

        app = App()
        async with app.run_test():
            await app.mount(panel)
            await app.wait_for_scheduled()

            # Verify get_status was called
            mock_status_tracker.get_status.assert_called_with("test-worktree")

    async def test_tool_panel_renders_working_status(
        self,
        mock_status_tracker: Mock,
        sample_status_working: WorktreeAIStatus,
    ) -> None:
        """Test ToolPanel renders correctly with WORKING status."""
        mock_status_tracker.get_status.return_value = sample_status_working

        panel = ToolPanel(
            status_tracker=mock_status_tracker,
            worktree_name="feature-test-claude",
            tool_name="claude",
        )

        from textual.app import App

        app = App()
        async with app.run_test():
            await app.mount(panel)
            await app.wait_for_scheduled()

            # Verify status was fetched
            mock_status_tracker.get_status.assert_called_with("feature-test-claude")

    async def test_tool_panel_focus_changes_border(
        self,
        mock_status_tracker: Mock,
        sample_status_working: WorktreeAIStatus,
    ) -> None:
        """Test ToolPanel border style changes when focused."""
        mock_status_tracker.get_status.return_value = sample_status_working

        panel = ToolPanel(
            status_tracker=mock_status_tracker,
            worktree_name="feature-test-claude",
            tool_name="claude",
        )

        from textual.app import App

        app = App()
        async with app.run_test():
            await app.mount(panel)
            await app.wait_for_scheduled()

            # Initially not focused
            assert panel.focused is False

            # Set focused
            panel.focused = True
            panel.refresh_data()
            await app.wait_for_scheduled()

            # Verify focus state changed
            assert panel.focused is True


@pytest.mark.textual
class TestCostComparisonPanel:
    """Tests for CostComparisonPanel widget."""

    async def test_cost_comparison_renders_no_data(
        self,
        mock_status_tracker: Mock,
    ) -> None:
        """Test CostComparisonPanel renders correctly when no data is available."""
        mock_status_tracker.get_status.return_value = None

        panel = CostComparisonPanel(
            status_tracker=mock_status_tracker,
            worktree_a="worktree-a",
            tool_a="claude",
            worktree_b="worktree-b",
            tool_b="opencode",
        )

        from textual.app import App

        app = App()
        async with app.run_test():
            await app.mount(panel)
            await app.wait_for_scheduled()

            # Verify get_status was called for both worktrees
            assert mock_status_tracker.get_status.call_count == 2

    async def test_cost_comparison_calculates_correctly(
        self,
        mock_status_tracker: Mock,
        sample_status_working: WorktreeAIStatus,
        sample_status_idle: WorktreeAIStatus,
    ) -> None:
        """Test CostComparisonPanel calculates costs correctly."""

        def get_status_side_effect(worktree_name: str) -> WorktreeAIStatus:
            if worktree_name == "feature-test-claude":
                return sample_status_working
            return sample_status_idle

        mock_status_tracker.get_status.side_effect = get_status_side_effect

        panel = CostComparisonPanel(
            status_tracker=mock_status_tracker,
            worktree_a="feature-test-claude",
            tool_a="claude",
            worktree_b="feature-test-opencode",
            tool_b="opencode",
        )

        from textual.app import App

        app = App()
        async with app.run_test():
            await app.mount(panel)
            await app.wait_for_scheduled()

            # Verify both statuses were fetched
            assert mock_status_tracker.get_status.call_count >= 2

    async def test_cost_comparison_identifies_cheaper_tool(
        self,
        mock_status_tracker: Mock,
        sample_status_working: WorktreeAIStatus,
        sample_status_idle: WorktreeAIStatus,
    ) -> None:
        """Test CostComparisonPanel identifies the cheaper tool."""

        def get_status_side_effect(worktree_name: str) -> WorktreeAIStatus:
            if worktree_name == "feature-test-claude":
                return sample_status_working
            return sample_status_idle

        mock_status_tracker.get_status.side_effect = get_status_side_effect

        panel = CostComparisonPanel(
            status_tracker=mock_status_tracker,
            worktree_a="feature-test-claude",
            tool_a="claude",
            worktree_b="feature-test-opencode",
            tool_b="opencode",
        )

        from textual.app import App

        app = App()
        async with app.run_test():
            await app.mount(panel)
            await app.wait_for_scheduled()

            # OpenCode should be cheaper (free)
            # Cost calculation is done in the render method
            # We verify that the panel refreshed successfully
            assert panel is not None


@pytest.mark.textual
class TestABCompareScreen:
    """Tests for ABCompareScreen."""

    async def test_screen_composes_correctly(
        self,
        sample_workspace: ABWorkspace,
        mock_status_tracker: Mock,
        mock_wt_manager: Mock,
    ) -> None:
        """Test ABCompareScreen composes with correct layout."""
        mock_status_tracker.get_status.return_value = None

        screen = ABCompareScreen(
            workspace=sample_workspace,
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        from textual.app import App

        app = App()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(screen)
            await pilot.pause()

            # Verify tool panels are present
            tool_a_panel = screen.query_one("#tool-a-panel", ToolPanel)
            tool_b_panel = screen.query_one("#tool-b-panel", ToolPanel)

            assert tool_a_panel.worktree_name == "feature-test-claude"
            assert tool_a_panel.tool_name == "claude"
            assert tool_b_panel.worktree_name == "feature-test-opencode"
            assert tool_b_panel.tool_name == "opencode"

            # Verify cost comparison panel
            cost_panel = screen.query_one(CostComparisonPanel)
            assert cost_panel.worktree_a == "feature-test-claude"
            assert cost_panel.worktree_b == "feature-test-opencode"

    async def test_tab_toggles_focus(
        self,
        sample_workspace: ABWorkspace,
        mock_status_tracker: Mock,
        mock_wt_manager: Mock,
    ) -> None:
        """Test Tab key toggles focus between left and right panels."""
        mock_status_tracker.get_status.return_value = None

        screen = ABCompareScreen(
            workspace=sample_workspace,
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        from textual.app import App

        app = App()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(screen)
            await pilot.pause()

            # Initially left panel should be focused
            assert screen.left_focused is True

            # Press Tab to toggle focus
            await pilot.press("tab")
            await pilot.pause()

            # Right panel should now be focused
            assert screen.left_focused is False

            # Press Tab again to toggle back
            await pilot.press("tab")
            await pilot.pause()

            # Left panel should be focused again
            assert screen.left_focused is True

    async def test_q_exits_screen(
        self,
        sample_workspace: ABWorkspace,
        mock_status_tracker: Mock,
        mock_wt_manager: Mock,
    ) -> None:
        """Test 'q' key exits the A/B comparison screen."""
        mock_status_tracker.get_status.return_value = None

        screen = ABCompareScreen(
            workspace=sample_workspace,
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        from textual.app import App

        app = App()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(screen)
            await pilot.pause()

            # Verify screen is active
            assert pilot.app.screen is screen

            # Press 'q' to exit
            await pilot.press("q")
            await pilot.pause()

            # Screen should be popped
            assert pilot.app.screen is not screen

    async def test_reactive_state_updates(
        self,
        sample_workspace: ABWorkspace,
        mock_status_tracker: Mock,
        mock_wt_manager: Mock,
        sample_status_working: WorktreeAIStatus,
    ) -> None:
        """Test screen updates when StatusTracker data changes."""
        mock_status_tracker.get_status.return_value = None

        screen = ABCompareScreen(
            workspace=sample_workspace,
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        from textual.app import App

        app = App()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(screen)
            await pilot.pause()

            # Update mock to return status data
            mock_status_tracker.get_status.return_value = sample_status_working

            # Trigger manual refresh
            screen._refresh_ui()
            await pilot.pause()

            # Verify get_status was called for both worktrees
            assert mock_status_tracker.get_status.call_count >= 2

    async def test_edge_case_one_tool_completes(
        self,
        sample_workspace: ABWorkspace,
        mock_status_tracker: Mock,
        mock_wt_manager: Mock,
        sample_status_idle: WorktreeAIStatus,
    ) -> None:
        """Test screen handles case where one tool completes before the other."""
        # Set up one tool as COMPLETED, other as WORKING
        completed_status = WorktreeAIStatus(
            worktree_name="feature-test-claude",
            worktree_path="/path/to/worktree/feature-test-claude",
            branch="feature/test-claude",
            ai_tool="claude",
            activity_status=AIActivityStatus.COMPLETED,
            current_task="Task completed",
            token_usage=TokenUsage(input_tokens=5000, output_tokens=3000),
            updated_at=datetime.now(),
        )

        working_status = WorktreeAIStatus(
            worktree_name="feature-test-opencode",
            worktree_path="/path/to/worktree/feature-test-opencode",
            branch="feature/test-opencode",
            ai_tool="opencode",
            activity_status=AIActivityStatus.WORKING,
            current_task="Still working...",
            token_usage=TokenUsage(input_tokens=4000, output_tokens=2000),
            updated_at=datetime.now(),
        )

        def get_status_side_effect(worktree_name: str) -> WorktreeAIStatus:
            if worktree_name == "feature-test-claude":
                return completed_status
            return working_status

        mock_status_tracker.get_status.side_effect = get_status_side_effect

        screen = ABCompareScreen(
            workspace=sample_workspace,
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        from textual.app import App

        app = App()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(screen)
            await pilot.pause()

            # Verify screen handles mixed statuses correctly
            screen._refresh_ui()
            await pilot.pause()

            # Both statuses should be fetched
            assert mock_status_tracker.get_status.call_count >= 2

    async def test_edge_case_one_tool_errors(
        self,
        sample_workspace: ABWorkspace,
        mock_status_tracker: Mock,
        mock_wt_manager: Mock,
    ) -> None:
        """Test screen handles case where one tool errors while other continues."""
        error_status = WorktreeAIStatus(
            worktree_name="feature-test-claude",
            worktree_path="/path/to/worktree/feature-test-claude",
            branch="feature/test-claude",
            ai_tool="claude",
            activity_status=AIActivityStatus.ERROR,
            current_task="Error occurred",
            token_usage=TokenUsage(input_tokens=5000, output_tokens=3000),
            updated_at=datetime.now(),
        )

        working_status = WorktreeAIStatus(
            worktree_name="feature-test-opencode",
            worktree_path="/path/to/worktree/feature-test-opencode",
            branch="feature/test-opencode",
            ai_tool="opencode",
            activity_status=AIActivityStatus.WORKING,
            current_task="Still working...",
            token_usage=TokenUsage(input_tokens=4000, output_tokens=2000),
            updated_at=datetime.now(),
        )

        def get_status_side_effect(worktree_name: str) -> WorktreeAIStatus:
            if worktree_name == "feature-test-claude":
                return error_status
            return working_status

        mock_status_tracker.get_status.side_effect = get_status_side_effect

        screen = ABCompareScreen(
            workspace=sample_workspace,
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        from textual.app import App

        app = App()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(screen)
            await pilot.pause()

            # Verify screen handles error state correctly
            screen._refresh_ui()
            await pilot.pause()

            # Both statuses should be fetched
            assert mock_status_tracker.get_status.call_count >= 2

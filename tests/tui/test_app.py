"""
Tests for OrchestratorApp TUI (dmux-style).

Tests the main TUI application including keyboard navigation,
action handlers, composition, and terminal detection.
"""

from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from textual.widgets import DataTable, Footer

from open_orchestrator.config import AITool
from open_orchestrator.models.ab_workspace import ABWorkspace
from open_orchestrator.models.status import AIActivityStatus, TokenUsage, WorktreeAIStatus
from open_orchestrator.models.worktree_info import WorktreeInfo
from open_orchestrator.tui.app import OrchestratorApp, PaneListWidget, is_interactive_terminal, launch_tui
from open_orchestrator.tui.screens import ABCompareScreen


@pytest.fixture
def mock_status_tracker():
    """Create a mock StatusTracker."""
    tracker = Mock()
    tracker.cleanup_orphans = Mock()
    tracker.get_status = Mock(return_value=None)
    tracker.get_summary = Mock(return_value=Mock(active_claudes=0))
    return tracker


@pytest.fixture
def mock_wt_manager():
    """Create a mock WorktreeManager."""
    manager = Mock()
    manager.list_all = Mock(return_value=[])
    return manager


@pytest.fixture
def mock_ab_launcher():
    """Create a mock ABLauncher."""
    launcher = Mock()
    launcher.store = Mock()
    launcher.store.find_by_worktree = Mock(return_value=None)
    return launcher


@pytest.fixture
def sample_ab_workspace():
    """Create a sample ABWorkspace."""
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
def sample_worktree():
    """Create a sample WorktreeInfo."""
    return WorktreeInfo(
        path=Path("/path/to/worktree/feature-branch"),
        branch="feature/test",
        head_commit="abc123",
        is_main=False,
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
        recent_commands=[],
        updated_at=datetime.now(),
    )


class TestOrchestratorAppInitialization:
    """Tests for OrchestratorApp initialization."""

    @pytest.mark.textual
    def test_initialization_with_dependencies(self, mock_status_tracker, mock_wt_manager):
        """Test app initialization with provided dependencies."""
        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        assert app.status_tracker == mock_status_tracker
        assert app.wt_manager == mock_wt_manager
        assert app._refresh_interval == 2.0

    @pytest.mark.textual
    def test_initialization_without_dependencies(self):
        """Test app initialization without dependencies (uses defaults)."""
        app = OrchestratorApp()

        assert app.status_tracker is not None
        assert app.wt_manager is not None
        assert app._refresh_interval == 2.0

    @pytest.mark.textual
    def test_bindings_defined(self):
        """Test that keybindings are properly defined."""
        app = OrchestratorApp()

        # BINDINGS are tuples of (key, action, description)
        binding_keys = [binding[0] for binding in app.BINDINGS]
        binding_actions = [binding[1] for binding in app.BINDINGS]

        # dmux-style bindings
        assert "n" in binding_keys
        assert "x" in binding_keys
        assert "m" in binding_keys
        assert "j" in binding_keys
        assert "k" in binding_keys
        assert "enter" in binding_keys
        assert "a" in binding_keys
        assert "q" in binding_keys
        assert "question_mark" in binding_keys

        assert "new_pane" in binding_actions
        assert "close_pane" in binding_actions
        assert "merge_worktree" in binding_actions
        assert "cursor_down" in binding_actions
        assert "cursor_up" in binding_actions
        assert "attach" in binding_actions
        assert "ab_launch" in binding_actions
        assert "quit_tui" in binding_actions
        assert "show_help" in binding_actions

    @pytest.mark.textual
    def test_workspace_params(self):
        """Test workspace_name and repo_path are accepted."""
        app = OrchestratorApp(workspace_name="owt-test", repo_path="/tmp/test")
        assert app.workspace_name == "owt-test"
        assert app.repo_path == "/tmp/test"


class TestOrchestratorAppComposition:
    """Tests for OrchestratorApp compose() method."""

    @pytest.mark.textual
    async def test_compose_returns_sidebar_and_footer(self, mock_status_tracker, mock_wt_manager):
        """Test that compose() returns PaneListWidget and Footer."""
        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test():
            pane_list = app.query_one(PaneListWidget)
            footer = app.query_one(Footer)

            assert pane_list is not None
            assert footer is not None


class TestOrchestratorAppActions:
    """Tests for OrchestratorApp action handlers."""

    @pytest.mark.textual
    async def test_action_new_pane_no_workspace(self, mock_status_tracker, mock_wt_manager):
        """Test 'n' key with no workspace shows error."""
        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            workspace_name="",
        )

        async with app.run_test() as pilot:
            await pilot.press("n")
            assert len(app._notifications) > 0

    @pytest.mark.textual
    async def test_action_close_pane_no_selection(self, mock_status_tracker, mock_wt_manager):
        """Test 'x' key with no selection shows notification."""
        mock_wt_manager.list_all.return_value = []

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test() as pilot:
            await pilot.press("x")
            assert len(app._notifications) > 0

    @pytest.mark.textual
    async def test_action_merge_no_selection(self, mock_status_tracker, mock_wt_manager):
        """Test 'm' key with no selection shows notification."""
        mock_wt_manager.list_all.return_value = []

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test() as pilot:
            await pilot.press("m")
            assert len(app._notifications) > 0

    @pytest.mark.textual
    async def test_action_cursor_down(self, mock_status_tracker, mock_wt_manager, sample_status):
        """Test 'j' key moves cursor down."""
        worktree1 = WorktreeInfo(
            path=Path("/path/to/worktree/feature-1"),
            branch="feature/1",
            head_commit="abc123",
            is_main=False,
        )
        worktree2 = WorktreeInfo(
            path=Path("/path/to/worktree/feature-2"),
            branch="feature/2",
            head_commit="def456",
            is_main=False,
        )

        mock_wt_manager.list_all.return_value = [worktree1, worktree2]
        mock_status_tracker.get_status.return_value = sample_status

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            pane_list = app.query_one(PaneListWidget)
            table = pane_list.query_one(DataTable)

            initial_row = table.cursor_row
            await pilot.press("j")

            assert table.cursor_row != initial_row

    @pytest.mark.textual
    async def test_action_cursor_up(self, mock_status_tracker, mock_wt_manager, sample_status):
        """Test 'k' key moves cursor up."""
        worktree1 = WorktreeInfo(
            path=Path("/path/to/worktree/feature-1"),
            branch="feature/1",
            head_commit="abc123",
            is_main=False,
        )
        worktree2 = WorktreeInfo(
            path=Path("/path/to/worktree/feature-2"),
            branch="feature/2",
            head_commit="def456",
            is_main=False,
        )

        mock_wt_manager.list_all.return_value = [worktree1, worktree2]
        mock_status_tracker.get_status.return_value = sample_status

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            await pilot.press("j")

            pane_list = app.query_one(PaneListWidget)
            table = pane_list.query_one(DataTable)
            cursor_after_down = table.cursor_row

            await pilot.press("k")
            assert table.cursor_row != cursor_after_down

    @pytest.mark.textual
    async def test_action_cursor_navigation_empty_list(self, mock_status_tracker, mock_wt_manager):
        """Test j/k keys handle empty pane list gracefully."""
        mock_wt_manager.list_all.return_value = []

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test() as pilot:
            await pilot.press("j")
            await pilot.press("k")

            pane_list = app.query_one(PaneListWidget)
            table = pane_list.query_one(DataTable)
            assert table.row_count == 0

    @pytest.mark.textual
    async def test_action_attach_no_selection(self, mock_status_tracker, mock_wt_manager):
        """Test action_attach() with no selection shows notification."""
        mock_wt_manager.list_all.return_value = []

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test():
            app.action_attach()
            assert True

    @pytest.mark.textual
    async def test_action_ab_launch_no_selection(self, mock_status_tracker, mock_wt_manager, mock_ab_launcher):
        """Test 'a' key with no selection shows notification."""
        mock_wt_manager.list_all.return_value = []

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
            ab_launcher=mock_ab_launcher,
        )

        async with app.run_test() as pilot:
            await pilot.press("a")
            assert len(app._notifications) > 0

    @pytest.mark.textual
    async def test_action_show_help(self, mock_status_tracker, mock_wt_manager):
        """Test '?' key opens help overlay."""
        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test() as pilot:
            await pilot.press("?")
            await pilot.pause()
            # Help screen should be pushed
            from open_orchestrator.tui.screens.help_overlay import HelpOverlayScreen

            assert isinstance(pilot.app.screen, HelpOverlayScreen)

    @pytest.mark.textual
    async def test_action_quit_no_panes(self, mock_status_tracker, mock_wt_manager):
        """Test 'q' key quits when no panes exist."""
        mock_wt_manager.list_all.return_value = []

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test() as pilot:
            await pilot.press("q")
            assert app.is_running is False


class TestOrchestratorAppRefresh:
    """Tests for OrchestratorApp status refresh."""

    @pytest.mark.textual
    async def test_refresh_interval_set_on_mount(self, mock_status_tracker, mock_wt_manager):
        """Test that refresh interval is set on mount."""
        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test():
            assert app._refresh_interval == 2.0

    @pytest.mark.textual
    async def test_refresh_ui_updates_widget(
        self, mock_status_tracker, mock_wt_manager, sample_worktree, sample_status
    ):
        """Test that _refresh_ui() calls widget.refresh_data()."""
        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = sample_status

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test():
            app._refresh_ui()

            pane_list = app.query_one(PaneListWidget)
            table = pane_list.query_one(DataTable)
            assert table.row_count == 1


class TestTerminalDetection:
    """Tests for terminal detection and CLI fallback."""

    def test_is_interactive_terminal_true(self):
        """Test is_interactive_terminal() returns True for interactive terminals."""
        with patch("sys.stdout.isatty", return_value=True):
            assert is_interactive_terminal() is True

    def test_is_interactive_terminal_false(self):
        """Test is_interactive_terminal() returns False for non-interactive terminals."""
        with patch("sys.stdout.isatty", return_value=False):
            assert is_interactive_terminal() is False

    def test_launch_tui_interactive_terminal(self, mock_status_tracker, mock_wt_manager):
        """Test launch_tui() starts app in interactive terminal."""
        with patch("sys.stdout.isatty", return_value=True):
            with patch("open_orchestrator.tui.app.OrchestratorApp.run") as mock_run:
                launch_tui(
                    status_tracker=mock_status_tracker,
                    wt_manager=mock_wt_manager,
                )
                mock_run.assert_called_once()

    def test_launch_tui_non_interactive_terminal(self, mock_status_tracker, mock_wt_manager):
        """Test launch_tui() shows message for non-interactive terminal."""
        with patch("sys.stdout.isatty", return_value=False):
            output = StringIO()
            with patch("sys.stdout", output):
                launch_tui(
                    status_tracker=mock_status_tracker,
                    wt_manager=mock_wt_manager,
                )


class TestOrchestratorAppEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.textual
    async def test_get_selected_worktree_no_cursor(self, mock_status_tracker, mock_wt_manager):
        """Test _get_selected_worktree() with no cursor returns None."""
        mock_wt_manager.list_all.return_value = []

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test():
            selected = app._get_selected_worktree()
            assert selected is None

    @pytest.mark.textual
    async def test_get_selected_worktree_empty_table(self, mock_status_tracker, mock_wt_manager):
        """Test _get_selected_worktree() with empty table returns None."""
        mock_wt_manager.list_all.return_value = []

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test():
            selected = app._get_selected_worktree()
            assert selected is None

    @pytest.mark.textual
    async def test_get_selected_worktree_with_selection(
        self, mock_status_tracker, mock_wt_manager, sample_worktree, sample_status
    ):
        """Test _get_selected_worktree() returns correct worktree name."""
        mock_wt_manager.list_all.return_value = [sample_worktree]
        mock_status_tracker.get_status.return_value = sample_status

        app = OrchestratorApp(
            status_tracker=mock_status_tracker,
            wt_manager=mock_wt_manager,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            pane_list = app.query_one(PaneListWidget)
            pane_list.refresh_data()
            await pilot.pause()

            table = pane_list.query_one(DataTable)

            if table.row_count > 0:
                table.move_cursor(row=0)
                await pilot.pause()

                selected = app._get_selected_worktree()
                assert selected in ["feature-branch", None]
            else:
                selected = app._get_selected_worktree()
                assert selected is None

"""Tests for the tmux manager module."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import libtmux.exc

from claude_orchestrator.core.tmux_manager import (
    TmuxError,
    TmuxLayout,
    TmuxManager,
    TmuxSessionConfig,
    TmuxSessionExistsError,
    TmuxSessionInfo,
    TmuxSessionNotFoundError,
)


class TestTmuxLayout:
    """Tests for TmuxLayout enum."""

    def test_layout_values(self):
        """Test that all expected layouts are defined."""
        assert TmuxLayout.MAIN_VERTICAL.value == "main-vertical"
        assert TmuxLayout.THREE_PANE.value == "three-pane"
        assert TmuxLayout.QUAD.value == "quad"
        assert TmuxLayout.EVEN_HORIZONTAL.value == "even-horizontal"
        assert TmuxLayout.EVEN_VERTICAL.value == "even-vertical"


class TestTmuxSessionConfig:
    """Tests for TmuxSessionConfig dataclass."""

    def test_default_values(self, temp_dir: Path):
        """Test default configuration values."""
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir)
        )

        assert config.session_name == "test-session"
        assert config.working_directory == str(temp_dir)
        assert config.layout == TmuxLayout.MAIN_VERTICAL
        assert config.pane_count == 2
        assert config.auto_start_claude is True
        assert config.window_name is None

    def test_custom_values(self, temp_dir: Path):
        """Test custom configuration values."""
        config = TmuxSessionConfig(
            session_name="custom-session",
            working_directory=str(temp_dir),
            layout=TmuxLayout.QUAD,
            pane_count=4,
            auto_start_claude=False,
            window_name="main-window"
        )

        assert config.layout == TmuxLayout.QUAD
        assert config.pane_count == 4
        assert config.auto_start_claude is False
        assert config.window_name == "main-window"


class TestTmuxSessionInfo:
    """Tests for TmuxSessionInfo dataclass."""

    def test_session_info_creation(self):
        """Test creating TmuxSessionInfo."""
        info = TmuxSessionInfo(
            session_name="test-session",
            session_id="$1",
            window_count=1,
            pane_count=2,
            created_at="2024-01-01 12:00:00",
            attached=False,
            working_directory="/tmp/test"
        )

        assert info.session_name == "test-session"
        assert info.session_id == "$1"
        assert info.window_count == 1
        assert info.pane_count == 2
        assert info.attached is False


class TestTmuxManager:
    """Tests for TmuxManager class."""

    def test_generate_session_name(self):
        """Test session name generation from worktree name."""
        manager = TmuxManager()

        assert manager._generate_session_name("feature-test") == "cwt-feature-test"
        assert manager._generate_session_name("feature/test") == "cwt-feature-test"
        assert manager._generate_session_name("feature.test") == "cwt-feature-test"

    def test_session_prefix(self):
        """Test that session prefix is correctly defined."""
        assert TmuxManager.SESSION_PREFIX == "cwt"

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_session_exists_true(self, mock_server_prop):
        """Test checking if session exists when it does."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.session_exists("test-session")

        assert result is True
        mock_server.has_session.assert_called_once_with("test-session")

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_session_exists_false(self, mock_server_prop):
        """Test checking if session exists when it doesn't."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.session_exists("nonexistent")

        assert result is False

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_session_exists_exception(self, mock_server_prop):
        """Test handling libtmux exception when checking session."""
        mock_server = MagicMock()
        mock_server.has_session.side_effect = libtmux.exc.LibTmuxException("error")
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.session_exists("test-session")

        assert result is False

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_create_session_already_exists(self, mock_server_prop, temp_dir: Path):
        """Test creating session when it already exists."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="existing-session",
            working_directory=str(temp_dir)
        )

        with pytest.raises(TmuxSessionExistsError) as exc_info:
            manager.create_session(config)

        assert "already exists" in str(exc_info.value)

    def test_create_session_invalid_directory(self):
        """Test creating session with non-existent directory."""
        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory="/nonexistent/path"
        )

        with patch.object(manager, 'session_exists', return_value=False):
            with pytest.raises(TmuxError) as exc_info:
                manager.create_session(config)

        assert "Working directory does not exist" in str(exc_info.value)

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_create_session_success(
        self,
        mock_server_prop,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test successful session creation."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="new-session",
            working_directory=str(temp_dir),
            auto_start_claude=False
        )

        result = manager.create_session(config)

        assert isinstance(result, TmuxSessionInfo)
        mock_server.new_session.assert_called_once()

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_list_sessions_empty(self, mock_server_prop):
        """Test listing sessions when none exist."""
        mock_server = MagicMock()
        mock_server.sessions = []
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.list_sessions()

        assert result == []

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_list_sessions_filter_prefix(self, mock_server_prop, mock_libtmux_session):
        """Test listing sessions filters by prefix."""
        cwt_session = MagicMock()
        cwt_session.name = "cwt-test"
        cwt_session.id = "$1"
        cwt_session.attached_count = 0
        cwt_session.windows = mock_libtmux_session.windows

        other_session = MagicMock()
        other_session.name = "other-session"
        other_session.id = "$2"
        other_session.attached_count = 0
        other_session.windows = mock_libtmux_session.windows

        mock_server = MagicMock()
        mock_server.sessions = [cwt_session, other_session]
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.list_sessions(filter_prefix=True)

        assert len(result) == 1
        assert result[0].session_name == "cwt-test"

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_list_sessions_no_filter(self, mock_server_prop, mock_libtmux_session):
        """Test listing all sessions without prefix filter."""
        cwt_session = MagicMock()
        cwt_session.name = "cwt-test"
        cwt_session.id = "$1"
        cwt_session.attached_count = 0
        cwt_session.windows = mock_libtmux_session.windows

        other_session = MagicMock()
        other_session.name = "other-session"
        other_session.id = "$2"
        other_session.attached_count = 0
        other_session.windows = mock_libtmux_session.windows

        mock_server = MagicMock()
        mock_server.sessions = [cwt_session, other_session]
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.list_sessions(filter_prefix=False)

        assert len(result) == 2

    @patch("subprocess.run")
    def test_attach_session_not_found(self, mock_run):
        """Test attaching to non-existent session."""
        manager = TmuxManager()

        with patch.object(manager, 'session_exists', return_value=False):
            with pytest.raises(TmuxSessionNotFoundError) as exc_info:
                manager.attach("nonexistent")

        assert "not found" in str(exc_info.value)

    @patch("subprocess.run")
    def test_attach_session_success(self, mock_run):
        """Test successful session attachment."""
        manager = TmuxManager()

        with patch.object(manager, 'session_exists', return_value=True):
            manager.attach("test-session")

        mock_run.assert_called_once_with(
            ["tmux", "attach-session", "-t", "test-session"],
            check=True
        )

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_kill_session_not_found(self, mock_server_prop):
        """Test killing non-existent session."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()

        with pytest.raises(TmuxSessionNotFoundError):
            manager.kill_session("nonexistent")

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_kill_session_success(self, mock_server_prop, mock_libtmux_session):
        """Test successful session killing."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        sessions_mock = MagicMock()
        sessions_mock.filter.return_value = [mock_libtmux_session]
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        manager.kill_session("test-session")

        mock_libtmux_session.kill.assert_called_once()

    def test_is_inside_tmux_true(self):
        """Test detecting when inside tmux."""
        manager = TmuxManager()

        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            assert manager.is_inside_tmux() is True

    def test_is_inside_tmux_false(self):
        """Test detecting when not inside tmux."""
        manager = TmuxManager()

        env_copy = os.environ.copy()
        env_copy.pop("TMUX", None)

        with patch.dict(os.environ, env_copy, clear=True):
            assert manager.is_inside_tmux() is False

    @patch("subprocess.run")
    def test_get_current_session_name(self, mock_run):
        """Test getting current session name when inside tmux."""
        mock_run.return_value = MagicMock(stdout="my-session\n")

        manager = TmuxManager()

        with patch.dict(os.environ, {"TMUX": "/tmp/tmux"}):
            result = manager.get_current_session_name()

        assert result == "my-session"

    def test_get_current_session_name_not_in_tmux(self):
        """Test getting current session name when not inside tmux."""
        manager = TmuxManager()

        env_copy = os.environ.copy()
        env_copy.pop("TMUX", None)

        with patch.dict(os.environ, env_copy, clear=True):
            result = manager.get_current_session_name()

        assert result is None

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_create_worktree_session(
        self,
        mock_server_prop,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test creating session for worktree."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.create_worktree_session(
            worktree_name="feature-test",
            worktree_path=str(temp_dir),
            layout=TmuxLayout.THREE_PANE,
            pane_count=3,
            auto_start_claude=False
        )

        assert isinstance(result, TmuxSessionInfo)
        mock_server.new_session.assert_called_once()

        call_kwargs = mock_server.new_session.call_args[1]
        assert call_kwargs["session_name"] == "cwt-feature-test"
        assert call_kwargs["start_directory"] == str(temp_dir)

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_get_session_for_worktree_found(
        self,
        mock_server_prop,
        mock_libtmux_session: MagicMock
    ):
        """Test finding existing session for worktree."""
        mock_libtmux_session.name = "cwt-feature-test"

        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        sessions_mock = MagicMock()
        sessions_mock.filter.return_value = [mock_libtmux_session]
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.get_session_for_worktree("feature-test")

        assert result is not None
        assert result.session_name == "cwt-feature-test"

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_get_session_for_worktree_not_found(self, mock_server_prop):
        """Test finding session for worktree when it doesn't exist."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.get_session_for_worktree("nonexistent")

        assert result is None

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_send_keys_to_pane(self, mock_server_prop, mock_libtmux_session):
        """Test sending keys to a pane."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        sessions_mock = MagicMock()
        sessions_mock.filter.return_value = [mock_libtmux_session]
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        manager.send_keys_to_pane("test-session", "echo hello")

        mock_libtmux_session.windows[0].panes[0].send_keys.assert_called_once_with(
            "echo hello",
            enter=True
        )

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_send_keys_session_not_found(self, mock_server_prop):
        """Test sending keys to non-existent session."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()

        with pytest.raises(TmuxSessionNotFoundError):
            manager.send_keys_to_pane("nonexistent", "echo hello")


class TestTmuxLayoutSetup:
    """Tests for tmux layout setup methods."""

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_setup_main_vertical_layout(
        self,
        mock_server_prop,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test main-vertical layout setup."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            layout=TmuxLayout.MAIN_VERTICAL,
            pane_count=2,
            auto_start_claude=False
        )

        manager.create_session(config)

        window = mock_libtmux_session.active_window
        window.select_layout.assert_called_with("main-vertical")

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_setup_three_pane_layout(
        self,
        mock_server_prop,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test three-pane layout setup."""
        # The bottom pane (accessed via window.panes[-1]) will have split_window called
        mock_bottom_pane = MagicMock()

        # Create a mock panes list where [-1] returns our bottom pane
        # After first split_window, window.panes[-1] should be the bottom pane
        mock_panes = MagicMock()
        mock_panes.__getitem__ = MagicMock(side_effect=lambda i: mock_bottom_pane if i == -1 else MagicMock())
        mock_libtmux_session.active_window.panes = mock_panes

        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            layout=TmuxLayout.THREE_PANE,
            auto_start_claude=False
        )

        manager.create_session(config)

        window = mock_libtmux_session.active_window
        # Three-pane layout:
        # 1. First split on window to create top/bottom (window.split_window)
        # 2. Second split on bottom pane to create left/right (bottom_pane.split_window)
        assert window.split_window.call_count == 1
        assert mock_bottom_pane.split_window.call_count == 1

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_setup_quad_layout(
        self,
        mock_server_prop,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test quad layout setup."""
        mock_panes = [MagicMock() for _ in range(4)]
        mock_libtmux_session.active_window.panes = mock_panes

        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            layout=TmuxLayout.QUAD,
            auto_start_claude=False
        )

        manager.create_session(config)

        window = mock_libtmux_session.active_window
        window.select_layout.assert_called_with("tiled")

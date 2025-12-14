"""Tests for the tmux manager module."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import libtmux.exc

from open_orchestrator.config import AITool
from open_orchestrator.core.tmux_manager import (
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
        assert config.auto_start_ai is True
        assert config.window_name is None

    def test_custom_values(self, temp_dir: Path):
        """Test custom configuration values."""
        config = TmuxSessionConfig(
            session_name="custom-session",
            working_directory=str(temp_dir),
            layout=TmuxLayout.QUAD,
            pane_count=4,
            auto_start_ai=False,
            window_name="main-window"
        )

        assert config.layout == TmuxLayout.QUAD
        assert config.pane_count == 4
        assert config.auto_start_ai is False
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

        assert manager._generate_session_name("feature-test") == "owt-feature-test"
        assert manager._generate_session_name("feature/test") == "owt-feature-test"
        assert manager._generate_session_name("feature.test") == "owt-feature-test"

    def test_session_prefix(self):
        """Test that session prefix is correctly defined."""
        assert TmuxManager.SESSION_PREFIX == "owt"

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
            auto_start_ai=False
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
        owt_session = MagicMock()
        owt_session.name = "owt-test"
        owt_session.id = "$1"
        owt_session.attached_count = 0
        owt_session.windows = mock_libtmux_session.windows

        other_session = MagicMock()
        other_session.name = "other-session"
        other_session.id = "$2"
        other_session.attached_count = 0
        other_session.windows = mock_libtmux_session.windows

        mock_server = MagicMock()
        mock_server.sessions = [owt_session, other_session]
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.list_sessions(filter_prefix=True)

        assert len(result) == 1
        assert result[0].session_name == "owt-test"

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_list_sessions_no_filter(self, mock_server_prop, mock_libtmux_session):
        """Test listing all sessions without prefix filter."""
        owt_session = MagicMock()
        owt_session.name = "owt-test"
        owt_session.id = "$1"
        owt_session.attached_count = 0
        owt_session.windows = mock_libtmux_session.windows

        other_session = MagicMock()
        other_session.name = "other-session"
        other_session.id = "$2"
        other_session.attached_count = 0
        other_session.windows = mock_libtmux_session.windows

        mock_server = MagicMock()
        mock_server.sessions = [owt_session, other_session]
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
            auto_start_ai=False
        )

        assert isinstance(result, TmuxSessionInfo)
        mock_server.new_session.assert_called_once()

        call_kwargs = mock_server.new_session.call_args[1]
        assert call_kwargs["session_name"] == "owt-feature-test"
        assert call_kwargs["start_directory"] == str(temp_dir)

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_get_session_for_worktree_found(
        self,
        mock_server_prop,
        mock_libtmux_session: MagicMock
    ):
        """Test finding existing session for worktree."""
        mock_libtmux_session.name = "owt-feature-test"

        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        sessions_mock = MagicMock()
        sessions_mock.filter.return_value = [mock_libtmux_session]
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.get_session_for_worktree("feature-test")

        assert result is not None
        assert result.session_name == "owt-feature-test"

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
            auto_start_ai=False
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
            auto_start_ai=False
        )

        manager.create_session(config)

        window = mock_libtmux_session.active_window
        # Three-pane layout:
        # 1. First split on window to create top/bottom (window.split)
        # 2. Second split on bottom pane to create left/right (bottom_pane.split)
        assert window.split.call_count == 1
        assert mock_bottom_pane.split.call_count == 1

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
            auto_start_ai=False
        )

        manager.create_session(config)

        window = mock_libtmux_session.active_window
        window.select_layout.assert_called_with("tiled")


class TestAIToolSupport:
    """Tests for multi-AI tool support."""

    def test_ai_tool_enum_values(self):
        """Test that all expected AI tools are defined."""
        assert AITool.CLAUDE.value == "claude"
        assert AITool.OPENCODE.value == "opencode"
        assert AITool.DROID.value == "droid"

    def test_ai_tool_get_command(self):
        """Test command retrieval for each tool."""
        assert AITool.get_command(AITool.CLAUDE) == "claude"
        assert AITool.get_command(AITool.OPENCODE) == "opencode"
        assert AITool.get_command(AITool.DROID) == "droid"

    def test_session_config_default_ai_tool(self, temp_dir: Path):
        """Test default AI tool is Claude."""
        config = TmuxSessionConfig(
            session_name="test",
            working_directory=str(temp_dir)
        )
        assert config.ai_tool == AITool.CLAUDE

    def test_session_config_custom_ai_tool(self, temp_dir: Path):
        """Test setting custom AI tool."""
        config = TmuxSessionConfig(
            session_name="test",
            working_directory=str(temp_dir),
            ai_tool=AITool.OPENCODE
        )
        assert config.ai_tool == AITool.OPENCODE

    def test_session_config_droid_tool(self, temp_dir: Path):
        """Test setting Droid as AI tool."""
        config = TmuxSessionConfig(
            session_name="test",
            working_directory=str(temp_dir),
            ai_tool=AITool.DROID
        )
        assert config.ai_tool == AITool.DROID

    @patch.object(AITool, 'get_executable_path', return_value='opencode')
    @patch.object(AITool, 'is_installed', return_value=True)
    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_start_ai_tool_opencode(
        self,
        mock_server_prop,
        mock_is_installed,
        mock_get_path,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test starting OpenCode instead of Claude."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.OPENCODE,
            auto_start_ai=True
        )

        manager.create_session(config)

        # Verify opencode command was sent
        mock_libtmux_session.active_window.active_pane.send_keys.assert_called_with(
            "opencode",
            enter=True
        )

    @patch.object(AITool, 'get_executable_path', return_value='droid')
    @patch.object(AITool, 'is_installed', return_value=True)
    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_start_ai_tool_droid(
        self,
        mock_server_prop,
        mock_is_installed,
        mock_get_path,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test starting Droid instead of Claude."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.DROID,
            auto_start_ai=True
        )

        manager.create_session(config)

        # Verify droid command was sent
        mock_libtmux_session.active_window.active_pane.send_keys.assert_called_with(
            "droid",
            enter=True
        )

    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_no_ai_tool_when_disabled(
        self,
        mock_server_prop,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test no AI tool is started when auto_start_ai is False."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.OPENCODE,
            auto_start_ai=False
        )

        manager.create_session(config)

        # Verify no command was sent to the pane
        mock_libtmux_session.active_window.active_pane.send_keys.assert_not_called()

    @patch.object(AITool, 'get_executable_path', return_value='opencode')
    @patch.object(AITool, 'is_installed', return_value=True)
    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_create_worktree_session_with_ai_tool(
        self,
        mock_server_prop,
        mock_is_installed,
        mock_get_path,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test creating worktree session with custom AI tool."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.create_worktree_session(
            worktree_name="feature-test",
            worktree_path=str(temp_dir),
            ai_tool=AITool.OPENCODE,
            auto_start_ai=True
        )

        assert isinstance(result, TmuxSessionInfo)
        mock_libtmux_session.active_window.active_pane.send_keys.assert_called_with(
            "opencode",
            enter=True
        )

    @patch.object(AITool, 'get_known_paths', return_value=[])
    @patch('shutil.which', return_value=None)
    def test_ai_tool_not_installed(self, mock_which, mock_known_paths):
        """Test is_installed returns False when tool is not found."""
        assert AITool.is_installed(AITool.OPENCODE) is False
        mock_which.assert_called_with("opencode")

    @patch('shutil.which', return_value='/usr/local/bin/claude')
    def test_ai_tool_installed(self, mock_which):
        """Test is_installed returns True when tool is found in PATH."""
        assert AITool.is_installed(AITool.CLAUDE) is True
        mock_which.assert_called_with("claude")

    def test_ai_tool_get_install_hint(self):
        """Test install hints are provided for each tool."""
        claude_hint = AITool.get_install_hint(AITool.CLAUDE)
        assert "npm install" in claude_hint or "Install" in claude_hint

        opencode_hint = AITool.get_install_hint(AITool.OPENCODE)
        assert "go install" in opencode_hint or "Install" in opencode_hint

        droid_hint = AITool.get_install_hint(AITool.DROID)
        assert "factory.ai" in droid_hint or "Install" in droid_hint


class TestDroidAutoLevel:
    """Tests for Droid auto level support."""

    def test_droid_auto_level_values(self):
        """Test DroidAutoLevel enum values."""
        from open_orchestrator.config import DroidAutoLevel

        assert DroidAutoLevel.LOW.value == "low"
        assert DroidAutoLevel.MEDIUM.value == "medium"
        assert DroidAutoLevel.HIGH.value == "high"

    def test_droid_command_with_auto_level(self):
        """Test Droid command includes auto level flag."""
        from open_orchestrator.config import DroidAutoLevel

        cmd_low = AITool.get_command(AITool.DROID, droid_auto=DroidAutoLevel.LOW)
        assert "--auto low" in cmd_low

        cmd_medium = AITool.get_command(AITool.DROID, droid_auto=DroidAutoLevel.MEDIUM)
        assert "--auto medium" in cmd_medium

        cmd_high = AITool.get_command(AITool.DROID, droid_auto=DroidAutoLevel.HIGH)
        assert "--auto high" in cmd_high

    def test_droid_command_with_skip_permissions(self):
        """Test Droid command includes skip-permissions flag."""
        cmd = AITool.get_command(AITool.DROID, droid_skip_permissions=True)
        assert "--skip-permissions-unsafe" in cmd

    def test_droid_command_with_all_options(self):
        """Test Droid command with all options combined."""
        from open_orchestrator.config import DroidAutoLevel

        cmd = AITool.get_command(
            AITool.DROID,
            droid_auto=DroidAutoLevel.HIGH,
            droid_skip_permissions=True
        )
        assert "droid" in cmd
        assert "--auto high" in cmd
        assert "--skip-permissions-unsafe" in cmd

    def test_session_config_droid_options(self, temp_dir: Path):
        """Test session config with Droid-specific options."""
        from open_orchestrator.config import DroidAutoLevel

        config = TmuxSessionConfig(
            session_name="test",
            working_directory=str(temp_dir),
            ai_tool=AITool.DROID,
            droid_auto=DroidAutoLevel.MEDIUM,
            droid_skip_permissions=True
        )

        assert config.ai_tool == AITool.DROID
        assert config.droid_auto == DroidAutoLevel.MEDIUM
        assert config.droid_skip_permissions is True

    @patch.object(AITool, 'is_installed', return_value=True)
    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_create_session_with_droid_auto(
        self,
        mock_server_prop,
        mock_is_installed,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test creating session with Droid auto level."""
        from open_orchestrator.config import DroidAutoLevel

        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.DROID,
            droid_auto=DroidAutoLevel.HIGH,
            auto_start_ai=True
        )

        manager.create_session(config)

        # Verify droid command with auto level was sent
        call_args = mock_libtmux_session.active_window.active_pane.send_keys.call_args
        sent_command = call_args[0][0]
        assert "droid" in sent_command
        assert "--auto high" in sent_command


class TestOpenCodeConfig:
    """Tests for OpenCode configuration support."""

    def test_opencode_command_with_config(self):
        """Test OpenCode command includes config path."""
        cmd = AITool.get_command(AITool.OPENCODE, opencode_config="/path/to/config.json")
        assert "OPENCODE_CONFIG=/path/to/config.json" in cmd
        assert "opencode" in cmd

    def test_opencode_command_without_config(self):
        """Test OpenCode command without config path."""
        cmd = AITool.get_command(AITool.OPENCODE)
        assert cmd == "opencode"
        assert "OPENCODE_CONFIG" not in cmd

    def test_session_config_opencode_options(self, temp_dir: Path):
        """Test session config with OpenCode-specific options."""
        config = TmuxSessionConfig(
            session_name="test",
            working_directory=str(temp_dir),
            ai_tool=AITool.OPENCODE,
            opencode_config="/custom/config.json"
        )

        assert config.ai_tool == AITool.OPENCODE
        assert config.opencode_config == "/custom/config.json"

    @patch.object(AITool, 'is_installed', return_value=True)
    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_create_session_with_opencode_config(
        self,
        mock_server_prop,
        mock_is_installed,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test creating session with OpenCode config path."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.OPENCODE,
            opencode_config="/my/config.json",
            auto_start_ai=True
        )

        manager.create_session(config)

        # Verify opencode command with config was sent
        call_args = mock_libtmux_session.active_window.active_pane.send_keys.call_args
        sent_command = call_args[0][0]
        assert "OPENCODE_CONFIG=/my/config.json" in sent_command
        assert "opencode" in sent_command


class TestToolInstallationCheck:
    """Tests for AI tool installation checking."""

    @patch.object(AITool, 'is_installed', return_value=False)
    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_create_session_fails_when_tool_not_installed(
        self,
        mock_server_prop,
        mock_is_installed,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test session creation fails when AI tool is not installed."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.OPENCODE,
            auto_start_ai=True
        )

        with pytest.raises(TmuxError) as exc_info:
            manager.create_session(config)

        assert "not installed" in str(exc_info.value)

    @patch.object(AITool, 'is_installed', return_value=False)
    @patch.object(TmuxManager, 'server', new_callable=PropertyMock)
    def test_create_session_skips_check_when_disabled(
        self,
        mock_server_prop,
        mock_is_installed,
        temp_dir: Path,
        mock_libtmux_session: MagicMock
    ):
        """Test session creation skips tool check when auto_start_ai is False."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.OPENCODE,
            auto_start_ai=False  # Tool not started, so no check needed
        )

        # Should not raise an error
        result = manager.create_session(config)
        assert isinstance(result, TmuxSessionInfo)

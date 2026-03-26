"""Tests for the tmux manager module."""

import os
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import libtmux.exc
import pytest

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
        assert TmuxLayout.SINGLE.value == "single"
        assert TmuxLayout.MAIN_VERTICAL.value == "main-vertical"


class TestTmuxSessionConfig:
    """Tests for TmuxSessionConfig dataclass."""

    def test_default_values(self, temp_dir: Path):
        """Test default configuration values."""
        config = TmuxSessionConfig(session_name="test-session", working_directory=str(temp_dir))

        assert config.session_name == "test-session"
        assert config.working_directory == str(temp_dir)
        assert config.layout == TmuxLayout.SINGLE
        assert config.pane_count == 1
        assert config.auto_start_ai is True
        assert config.window_name is None

    def test_custom_values(self, temp_dir: Path):
        """Test custom configuration values."""
        config = TmuxSessionConfig(
            session_name="custom-session",
            working_directory=str(temp_dir),
            layout=TmuxLayout.MAIN_VERTICAL,
            pane_count=2,
            auto_start_ai=False,
            window_name="main-window",
        )

        assert config.layout == TmuxLayout.MAIN_VERTICAL
        assert config.pane_count == 2
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
            working_directory="/tmp/test",
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

        assert manager.generate_session_name("feature-test") == "owt-feature-test"
        assert manager.generate_session_name("feature/test") == "owt-feature-test"
        assert manager.generate_session_name("feature.test") == "owt-feature-test"

    def test_session_prefix(self):
        """Test that session prefix is correctly defined."""
        assert TmuxManager.SESSION_PREFIX == "owt"

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_session_exists_true(self, mock_server_prop):
        """Test checking if session exists when it does."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.session_exists("test-session")

        assert result is True
        mock_server.has_session.assert_called_once_with("test-session")

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_session_exists_false(self, mock_server_prop):
        """Test checking if session exists when it doesn't."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.session_exists("nonexistent")

        assert result is False

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_session_exists_exception(self, mock_server_prop):
        """Test handling libtmux exception when checking session."""
        mock_server = MagicMock()
        mock_server.has_session.side_effect = libtmux.exc.LibTmuxException("error")
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.session_exists("test-session")

        assert result is False

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_already_exists(self, mock_server_prop, temp_dir: Path):
        """Test creating session when it already exists."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(session_name="existing-session", working_directory=str(temp_dir))

        with pytest.raises(TmuxSessionExistsError) as exc_info:
            manager.create_session(config)

        assert "already exists" in str(exc_info.value)

    def test_create_session_invalid_directory(self):
        """Test creating session with non-existent directory."""
        manager = TmuxManager()
        config = TmuxSessionConfig(session_name="test-session", working_directory="/nonexistent/path")

        with patch.object(manager, "session_exists", return_value=False):
            with pytest.raises(TmuxError) as exc_info:
                manager.create_session(config)

        assert "Working directory does not exist" in str(exc_info.value)

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_success(self, mock_server_prop, temp_dir: Path, mock_libtmux_session: MagicMock):
        """Test successful session creation."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(session_name="new-session", working_directory=str(temp_dir), auto_start_ai=False)

        result = manager.create_session(config)

        assert isinstance(result, TmuxSessionInfo)
        mock_server.new_session.assert_called_once()

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_list_sessions_empty(self, mock_server_prop):
        """Test listing sessions when none exist."""
        mock_server = MagicMock()
        mock_server.sessions = []
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.list_sessions()

        assert result == []

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
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

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
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

        with patch.object(manager, "session_exists", return_value=False):
            with pytest.raises(TmuxSessionNotFoundError) as exc_info:
                manager.attach("nonexistent")

        assert "not found" in str(exc_info.value)

    @patch("subprocess.run")
    def test_attach_session_success(self, mock_run):
        """Test successful session attachment."""
        manager = TmuxManager()

        with patch.object(manager, "session_exists", return_value=True):
            manager.attach("test-session")

        mock_run.assert_called_once_with(["tmux", "attach-session", "-t", "test-session"], check=True)

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_kill_session_not_found(self, mock_server_prop):
        """Test killing non-existent session."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()

        with pytest.raises(TmuxSessionNotFoundError):
            manager.kill_session("nonexistent")

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
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

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_worktree_session(self, mock_server_prop, temp_dir: Path, mock_libtmux_session: MagicMock):
        """Test creating session for worktree."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.create_worktree_session(
            worktree_name="feature-test",
            worktree_path=str(temp_dir),
            auto_start_ai=False,
        )

        assert isinstance(result, TmuxSessionInfo)
        mock_server.new_session.assert_called_once()

        call_kwargs = mock_server.new_session.call_args[1]
        assert call_kwargs["session_name"] == "owt-feature-test"
        assert call_kwargs["start_directory"] == str(temp_dir)

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_get_session_for_worktree_found(self, mock_server_prop, mock_libtmux_session: MagicMock):
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

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_get_session_for_worktree_not_found(self, mock_server_prop):
        """Test finding session for worktree when it doesn't exist."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.get_session_for_worktree("nonexistent")

        assert result is None

    @patch("open_orchestrator.core.tmux_manager.subprocess")
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_send_keys_to_pane(self, mock_server_prop, mock_subprocess, mock_libtmux_session):
        """Test sending keys to a pane via tmux buffer."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        manager.send_keys_to_pane("test-session", "echo hello")

        # Should use set-buffer + paste-buffer + send-keys Enter
        assert mock_subprocess.run.call_count == 3
        calls = mock_subprocess.run.call_args_list
        assert calls[0][0][0] == ["tmux", "set-buffer", "-b", "owt-send", "--", "echo hello"]
        assert calls[1][0][0] == ["tmux", "paste-buffer", "-b", "owt-send", "-d", "-p", "-t", "test-session:0.0"]
        assert calls[2][0][0] == ["tmux", "send-keys", "-t", "test-session:0.0", "Enter"]

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
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

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_setup_main_vertical_layout(self, mock_server_prop, temp_dir: Path, mock_libtmux_session: MagicMock):
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
            auto_start_ai=False,
        )

        manager.create_session(config)

        window = mock_libtmux_session.active_window
        window.select_layout.assert_called_with("main-vertical")

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_setup_single_layout(self, mock_server_prop, temp_dir: Path, mock_libtmux_session: MagicMock):
        """Test single layout (no splits)."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session", working_directory=str(temp_dir), layout=TmuxLayout.SINGLE, auto_start_ai=False
        )

        manager.create_session(config)

        window = mock_libtmux_session.active_window
        # Single layout should not call split
        window.split.assert_not_called()


class TestAIToolSupport:
    """Tests for multi-AI tool support."""

    def test_ai_tool_enum_values(self):
        """Test that all expected AI tools are defined."""
        assert AITool.CLAUDE.value == "claude"
        assert AITool.OPENCODE.value == "opencode"
        assert AITool.DROID.value == "droid"

    def test_ai_tool_get_command(self):
        """Test command retrieval for each tool."""
        assert AITool.get_command(AITool.CLAUDE) == "claude --dangerously-skip-permissions"
        assert AITool.get_command(AITool.OPENCODE) == "opencode"
        assert AITool.get_command(AITool.DROID) == "droid --skip-permissions-unsafe"

    def test_ai_tool_get_command_with_prompt(self):
        """Test that prompt adds -p flag without inline prompt text."""
        cmd = AITool.get_command(AITool.CLAUDE, prompt="Do something")
        assert cmd == "claude --dangerously-skip-permissions -p"
        # Prompt text must NOT appear in command (piped via stdin instead)
        assert "Do something" not in cmd

    def test_ai_tool_get_command_plan_mode_with_prompt(self):
        """Test plan mode flag ordering with prompt."""
        cmd = AITool.get_command(AITool.CLAUDE, plan_mode=True, prompt="Plan this")
        assert cmd == "claude --permission-mode plan -p"

    def test_session_config_default_ai_tool(self, temp_dir: Path):
        """Test default AI tool is Claude."""
        config = TmuxSessionConfig(session_name="test", working_directory=str(temp_dir))
        assert config.ai_tool == AITool.CLAUDE

    def test_session_config_custom_ai_tool(self, temp_dir: Path):
        """Test setting custom AI tool."""
        config = TmuxSessionConfig(session_name="test", working_directory=str(temp_dir), ai_tool=AITool.OPENCODE)
        assert config.ai_tool == AITool.OPENCODE

    def test_session_config_droid_tool(self, temp_dir: Path):
        """Test setting Droid as AI tool."""
        config = TmuxSessionConfig(session_name="test", working_directory=str(temp_dir), ai_tool=AITool.DROID)
        assert config.ai_tool == AITool.DROID

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "get_executable_path", return_value="opencode")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_start_ai_tool_opencode(
        self, mock_server_prop, mock_is_installed, mock_get_path,
        mock_wait, mock_send_cmd, temp_dir: Path, mock_libtmux_session: MagicMock,
    ):
        """Test starting OpenCode instead of Claude."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session", working_directory=str(temp_dir), ai_tool=AITool.OPENCODE, auto_start_ai=True
        )

        manager.create_session(config)

        # Verify opencode command was sent via reliable delivery
        mock_send_cmd.assert_called_once()
        assert mock_send_cmd.call_args[0][1] == "opencode"

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "get_executable_path", return_value="droid")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_start_ai_tool_droid(
        self, mock_server_prop, mock_is_installed, mock_get_path,
        mock_wait, mock_send_cmd, temp_dir: Path, mock_libtmux_session: MagicMock,
    ):
        """Test starting Droid instead of Claude."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session", working_directory=str(temp_dir), ai_tool=AITool.DROID, auto_start_ai=True
        )

        manager.create_session(config)

        # Verify droid command was sent via reliable delivery
        mock_send_cmd.assert_called_once()
        assert mock_send_cmd.call_args[0][1] == "droid --skip-permissions-unsafe"

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_no_ai_tool_when_disabled(
        self, mock_server_prop, mock_wait, mock_send_cmd,
        temp_dir: Path, mock_libtmux_session: MagicMock,
    ):
        """Test no AI tool is started when auto_start_ai is False."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session", working_directory=str(temp_dir), ai_tool=AITool.OPENCODE, auto_start_ai=False
        )

        manager.create_session(config)

        # Verify no command was sent to the pane
        mock_send_cmd.assert_not_called()

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "get_executable_path", return_value="opencode")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_worktree_session_with_ai_tool(
        self, mock_server_prop, mock_is_installed, mock_get_path,
        mock_wait, mock_send_cmd, temp_dir: Path, mock_libtmux_session: MagicMock,
    ):
        """Test creating worktree session with custom AI tool."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.create_worktree_session(
            worktree_name="feature-test", worktree_path=str(temp_dir), ai_tool=AITool.OPENCODE, auto_start_ai=True
        )

        assert isinstance(result, TmuxSessionInfo)
        mock_send_cmd.assert_called_once()
        assert mock_send_cmd.call_args[0][1] == "opencode"

    @patch.object(AITool, "get_known_paths", return_value=[])
    @patch("shutil.which", return_value=None)
    def test_ai_tool_not_installed(self, mock_which, mock_known_paths):
        """Test is_installed returns False when tool is not found."""
        assert AITool.is_installed(AITool.OPENCODE) is False
        mock_which.assert_called_with("opencode")

    @patch("shutil.which", return_value="/usr/local/bin/claude")
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

        cmd = AITool.get_command(AITool.DROID, droid_auto=DroidAutoLevel.HIGH, droid_skip_permissions=True)
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
            droid_skip_permissions=True,
        )

        assert config.ai_tool == AITool.DROID
        assert config.droid_auto == DroidAutoLevel.MEDIUM
        assert config.droid_skip_permissions is True

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_with_droid_auto(
        self, mock_server_prop, mock_is_installed, mock_wait, mock_send_cmd,
        temp_dir: Path, mock_libtmux_session: MagicMock,
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
            auto_start_ai=True,
        )

        manager.create_session(config)

        # Verify droid command with auto level was sent
        sent_command = mock_send_cmd.call_args[0][1]
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
            session_name="test", working_directory=str(temp_dir), ai_tool=AITool.OPENCODE, opencode_config="/custom/config.json"
        )

        assert config.ai_tool == AITool.OPENCODE
        assert config.opencode_config == "/custom/config.json"

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_with_opencode_config(
        self, mock_server_prop, mock_is_installed, mock_wait, mock_send_cmd,
        temp_dir: Path, mock_libtmux_session: MagicMock,
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
            auto_start_ai=True,
        )

        manager.create_session(config)

        # Verify opencode command with config was sent
        sent_command = mock_send_cmd.call_args[0][1]
        assert "OPENCODE_CONFIG=/my/config.json" in sent_command
        assert "opencode" in sent_command


class TestAutoExit:
    """Tests for auto_exit flag support."""

    def test_session_config_auto_exit_default_false(self, temp_dir: Path):
        """Test that auto_exit defaults to False."""
        config = TmuxSessionConfig(session_name="test", working_directory=str(temp_dir))
        assert config.auto_exit is False

    def test_session_config_auto_exit_true(self, temp_dir: Path):
        """Test setting auto_exit to True."""
        config = TmuxSessionConfig(
            session_name="test", working_directory=str(temp_dir), auto_exit=True
        )
        assert config.auto_exit is True

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "get_executable_path", return_value="claude")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_auto_exit_appends_exit_to_command(
        self, mock_server_prop, mock_is_installed, mock_get_path,
        mock_wait, mock_send_cmd, temp_dir: Path, mock_libtmux_session: MagicMock,
    ):
        """Test that auto_exit appends '; exit' to the AI tool command."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.CLAUDE,
            auto_start_ai=True,
            auto_exit=True,
        )

        manager.create_session(config)

        sent_command = mock_send_cmd.call_args[0][1]
        assert sent_command.endswith("; exit")
        assert "claude" in sent_command

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "get_executable_path", return_value="claude")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_no_auto_exit_no_suffix(
        self, mock_server_prop, mock_is_installed, mock_get_path,
        mock_wait, mock_send_cmd, temp_dir: Path, mock_libtmux_session: MagicMock,
    ):
        """Test that without auto_exit, no '; exit' is appended."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.CLAUDE,
            auto_start_ai=True,
            auto_exit=False,
        )

        manager.create_session(config)

        sent_command = mock_send_cmd.call_args[0][1]
        assert not sent_command.endswith("; exit")


    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "get_executable_path", return_value="claude")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_prompt_with_auto_exit_uses_stdin_redirect(
        self, mock_server_prop, mock_is_installed, mock_get_path,
        mock_wait, mock_send_cmd, temp_dir: Path, mock_libtmux_session: MagicMock,
    ):
        """Test that prompt + auto_exit writes a temp file and pipes via stdin."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.CLAUDE,
            auto_start_ai=True,
            auto_exit=True,
            prompt="Implement a feature",
        )

        manager.create_session(config)

        sent_command = mock_send_cmd.call_args[0][1]
        # Should pipe temp file via cat, not inline prompt
        assert "cat " in sent_command
        assert "| " in sent_command
        assert "owt-prompt-" in sent_command
        assert "-p" in sent_command
        assert "OWT_AUTOMATED=1" in sent_command
        assert sent_command.endswith("; exit")
        assert "rm -f" in sent_command
        # Inline prompt text must NOT appear in command
        assert "Implement a feature" not in sent_command

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "get_executable_path", return_value="claude")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_prompt_without_auto_exit_still_uses_stdin_redirect(
        self, mock_server_prop, mock_is_installed, mock_get_path,
        mock_wait, mock_send_cmd, temp_dir: Path, mock_libtmux_session: MagicMock,
    ):
        """Test that prompt without auto_exit still uses temp file piping."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            ai_tool=AITool.CLAUDE,
            auto_start_ai=True,
            auto_exit=False,
            prompt="Implement a feature",
        )

        manager.create_session(config)

        sent_command = mock_send_cmd.call_args[0][1]
        assert "cat " in sent_command
        assert "| " in sent_command
        assert "owt-prompt-" in sent_command
        assert not sent_command.endswith("; exit")
        assert "rm -f" in sent_command

    def test_write_prompt_file_creates_temp_file(self):
        """Test that _write_prompt_file writes prompt to a readable temp file."""
        prompt = "Build a REST API with authentication"
        path = TmuxManager._write_prompt_file(prompt)
        try:
            assert os.path.exists(path)
            assert "owt-prompt-" in os.path.basename(path)
            with open(path) as f:
                assert f.read() == prompt
        finally:
            os.unlink(path)


class TestIsAiRunningInSession:
    """Tests for is_ai_running_in_session method."""

    @patch("subprocess.run")
    def test_returns_false_when_session_gone(self, mock_run):
        """Test returns False when tmux session doesn't exist."""
        manager = TmuxManager()
        with patch.object(manager, "session_exists", return_value=False):
            assert manager.is_ai_running_in_session("owt-test") is False

    @patch("subprocess.run")
    def test_returns_false_when_pane_runs_shell(self, mock_run):
        """Test returns False when pane is running a shell (AI exited)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="zsh\n")
        manager = TmuxManager()
        with patch.object(manager, "session_exists", return_value=True):
            assert manager.is_ai_running_in_session("owt-test") is False

    @patch("subprocess.run")
    def test_returns_true_when_pane_runs_ai(self, mock_run):
        """Test returns True when pane is running an AI tool."""
        mock_run.return_value = MagicMock(returncode=0, stdout="node\n")
        manager = TmuxManager()
        with patch.object(manager, "session_exists", return_value=True):
            assert manager.is_ai_running_in_session("owt-test") is True

    @patch("subprocess.run")
    def test_returns_false_on_subprocess_error(self, mock_run):
        """Test returns False when tmux command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        manager = TmuxManager()
        with patch.object(manager, "session_exists", return_value=True):
            assert manager.is_ai_running_in_session("owt-test") is False

    @patch("subprocess.run")
    def test_returns_false_on_timeout(self, mock_run):
        """Test returns False on subprocess timeout."""
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd="tmux", timeout=5)
        manager = TmuxManager()
        with patch.object(manager, "session_exists", return_value=True):
            assert manager.is_ai_running_in_session("owt-test") is False

    @patch("subprocess.run")
    def test_multiple_shells_returns_false(self, mock_run):
        """Test returns False when all panes run shells."""
        mock_run.return_value = MagicMock(returncode=0, stdout="bash\nzsh\n")
        manager = TmuxManager()
        with patch.object(manager, "session_exists", return_value=True):
            assert manager.is_ai_running_in_session("owt-test") is False


class TestToolInstallationCheck:
    """Tests for AI tool installation checking."""

    @patch.object(AITool, "is_installed", return_value=False)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_fails_when_tool_not_installed(
        self, mock_server_prop, mock_is_installed, temp_dir: Path, mock_libtmux_session: MagicMock
    ):
        """Test session creation fails when AI tool is not installed."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session", working_directory=str(temp_dir), ai_tool=AITool.OPENCODE, auto_start_ai=True
        )

        with pytest.raises(TmuxError) as exc_info:
            manager.create_session(config)

        assert "not installed" in str(exc_info.value)

    @patch.object(AITool, "is_installed", return_value=False)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_skips_check_when_disabled(
        self, mock_server_prop, mock_is_installed, temp_dir: Path, mock_libtmux_session: MagicMock
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
            auto_start_ai=False,  # Tool not started, so no check needed
        )

        # Should not raise an error
        result = manager.create_session(config)
        assert isinstance(result, TmuxSessionInfo)


class TestTmuxManagerContextManager:
    """Tests for TmuxManager context manager and lifecycle methods."""

    def test_enter_returns_self(self):
        """Test __enter__ returns the manager instance."""
        manager = TmuxManager()
        result = manager.__enter__()
        assert result is manager

    def test_exit_calls_close(self):
        """Test __exit__ delegates to close()."""
        manager = TmuxManager()
        with patch.object(manager, "close") as mock_close:
            manager.__exit__(None, None, None)
        mock_close.assert_called_once()

    def test_context_manager_protocol(self):
        """Test using TmuxManager as a context manager."""
        with TmuxManager() as manager:
            assert isinstance(manager, TmuxManager)

    def test_close_sets_server_to_none(self):
        """Test close() nullifies the cached server reference."""
        manager = TmuxManager()
        manager._server = MagicMock()  # simulate an initialised server
        manager.close()
        assert manager._server is None

    def test_close_is_idempotent_when_no_server(self):
        """Test close() is safe to call when _server is already None."""
        manager = TmuxManager()
        assert manager._server is None
        manager.close()  # should not raise
        assert manager._server is None

    def test_server_property_creates_libtmux_server(self):
        """Test the server property lazily creates a libtmux.Server instance."""

        manager = TmuxManager()
        assert manager._server is None

        with patch("libtmux.Server") as mock_server_cls:
            fake_server = MagicMock()
            mock_server_cls.return_value = fake_server
            result = manager.server
            assert result is fake_server
            mock_server_cls.assert_called_once()

    def test_server_property_caches_instance(self):
        """Test the server property returns the same instance on repeated access."""
        manager = TmuxManager()
        with patch("libtmux.Server") as mock_server_cls:
            fake_server = MagicMock()
            mock_server_cls.return_value = fake_server

            first = manager.server
            second = manager.server

            assert first is second
            mock_server_cls.assert_called_once()  # constructed only once


class TestPaneTarget:
    """Tests for TmuxManager._pane_target static method."""

    def test_pane_target_builds_correct_string(self):
        """Test _pane_target returns session:window.pane format."""
        pane = MagicMock()
        pane.session.name = "owt-my-feature"
        pane.window.index = "0"
        pane.pane_index = "1"

        result = TmuxManager._pane_target(pane)
        assert result == "owt-my-feature:0.1"

    def test_pane_target_with_different_indices(self):
        """Test _pane_target with various window and pane indices."""
        pane = MagicMock()
        pane.session.name = "owt-test"
        pane.window.index = "3"
        pane.pane_index = "2"

        result = TmuxManager._pane_target(pane)
        assert result == "owt-test:3.2"

    def test_pane_target_is_callable_as_static_method(self):
        """Test _pane_target can be called on the class without an instance."""
        pane = MagicMock()
        pane.session.name = "session"
        pane.window.index = "0"
        pane.pane_index = "0"

        result = TmuxManager._pane_target(pane)
        assert "session:0.0" == result


class TestWaitForShellReady:
    """Tests for TmuxManager._wait_for_shell_ready method."""

    def _make_pane(self, session_name: str = "owt-test", window_index: str = "0", pane_index: str = "0") -> MagicMock:
        pane = MagicMock()
        pane.session.name = session_name
        pane.window.index = window_index
        pane.pane_index = pane_index
        return pane

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_returns_immediately_when_shell_ready(self, mock_run, mock_sleep):
        """Test method returns once a known shell name appears."""
        mock_run.return_value = MagicMock(stdout="zsh\n", returncode=0)
        manager = TmuxManager()
        pane = self._make_pane()

        manager._wait_for_shell_ready(pane, timeout=3.0)

        mock_run.assert_called_once()
        # No sleeping needed since the shell was ready on the first poll
        mock_sleep.assert_not_called()

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_recognises_all_known_shells(self, mock_run, mock_sleep):
        """Test every shell name in the whitelist is recognised as ready."""
        manager = TmuxManager()
        pane = self._make_pane()

        for shell in ("bash", "zsh", "fish", "sh", "dash", "login"):
            mock_run.return_value = MagicMock(stdout=f"{shell}\n", returncode=0)
            mock_run.reset_mock()
            mock_sleep.reset_mock()
            manager._wait_for_shell_ready(pane, timeout=3.0)
            mock_run.assert_called_once()

    @patch("time.monotonic")
    @patch("time.sleep")
    @patch("subprocess.run")
    def test_retries_until_shell_appears(self, mock_run, mock_sleep, mock_time):
        """Test the method retries when pane_current_command is not a shell yet."""
        # Simulate time: first call returns 0.0, each subsequent adds 0.1s.
        time_values = [0.0, 0.1, 0.2]
        mock_time.side_effect = time_values

        # First poll: command is "python" (not a shell); second: "zsh" (ready).
        mock_run.side_effect = [
            MagicMock(stdout="python\n", returncode=0),
            MagicMock(stdout="zsh\n", returncode=0),
        ]
        manager = TmuxManager()
        pane = self._make_pane()

        manager._wait_for_shell_ready(pane, timeout=3.0)

        assert mock_run.call_count == 2

    @patch("time.monotonic")
    @patch("time.sleep")
    @patch("subprocess.run")
    def test_logs_warning_on_timeout(self, mock_run, mock_sleep, mock_time):
        """Test a warning is logged when timeout expires before shell is ready."""
        # Time exceeds timeout immediately after first check
        mock_time.side_effect = [0.0, 5.0, 5.1]
        mock_run.return_value = MagicMock(stdout="python\n", returncode=0)

        manager = TmuxManager()
        pane = self._make_pane()

        import logging
        with patch.object(logging.getLogger("open_orchestrator.core.tmux_manager"), "warning") as mock_warn:
            manager._wait_for_shell_ready(pane, timeout=3.0)
            mock_warn.assert_called_once()
            assert "timeout" in mock_warn.call_args[0][0].lower() or "timeout" in str(mock_warn.call_args).lower()

    @patch("time.monotonic")
    @patch("time.sleep")
    @patch("subprocess.run")
    def test_handles_subprocess_timeout_exception(self, mock_run, mock_sleep, mock_time):
        """Test OSError / TimeoutExpired from subprocess is swallowed gracefully."""
        import subprocess as sp

        mock_time.side_effect = [0.0, 5.0, 5.1]
        mock_run.side_effect = sp.TimeoutExpired(cmd="tmux", timeout=2)

        manager = TmuxManager()
        pane = self._make_pane()

        # Should not raise, just log a warning
        manager._wait_for_shell_ready(pane, timeout=3.0)

    @patch("time.monotonic")
    @patch("time.sleep")
    @patch("subprocess.run")
    def test_handles_os_error_exception(self, mock_run, mock_sleep, mock_time):
        """Test OSError from subprocess is swallowed gracefully."""
        mock_time.side_effect = [0.0, 5.0, 5.1]
        mock_run.side_effect = OSError("tmux not found")

        manager = TmuxManager()
        pane = self._make_pane()

        manager._wait_for_shell_ready(pane, timeout=3.0)

    @patch("time.monotonic")
    @patch("time.sleep")
    @patch("subprocess.run")
    def test_empty_command_output_is_not_treated_as_ready(self, mock_run, mock_sleep, mock_time):
        """Test empty stdout is not considered a ready shell."""
        # start=0.0; first loop-condition check=0.1 (< 3.0 → enters body);
        # after sleep, second loop-condition check=5.0 (>= 3.0 → exits)
        mock_time.side_effect = [0.0, 0.1, 5.0]
        mock_run.return_value = MagicMock(stdout="", returncode=0)

        manager = TmuxManager()
        pane = self._make_pane()

        # Should timeout and log warning rather than returning early
        manager._wait_for_shell_ready(pane, timeout=3.0)
        # Loop body ran once: subprocess.run was called but empty output
        # did not trigger an early return
        mock_run.assert_called_once()


class TestSendCommandToPane:
    """Tests for TmuxManager._send_command_to_pane class method."""

    def _make_pane(self) -> MagicMock:
        pane = MagicMock()
        pane.session.name = "owt-test"
        pane.window.index = "0"
        pane.pane_index = "0"
        return pane

    @patch("open_orchestrator.core.tmux_manager.subprocess")
    def test_sends_command_via_set_paste_enter(self, mock_subprocess):
        """Test three subprocess calls: set-buffer, paste-buffer, send-keys Enter."""
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        pane = self._make_pane()

        TmuxManager._send_command_to_pane(pane, "echo hello")

        assert mock_subprocess.run.call_count == 3
        calls = mock_subprocess.run.call_args_list
        # set-buffer
        assert calls[0][0][0] == ["tmux", "set-buffer", "-b", "owt-init", "--", "echo hello"]
        # paste-buffer
        assert calls[1][0][0] == ["tmux", "paste-buffer", "-b", "owt-init", "-d", "-p", "-t", "owt-test:0.0"]
        # Enter
        assert calls[2][0][0] == ["tmux", "send-keys", "-t", "owt-test:0.0", "Enter"]

    @patch("open_orchestrator.core.tmux_manager.subprocess")
    def test_uses_owt_init_buffer_name(self, mock_subprocess):
        """Test that the named buffer 'owt-init' is always used."""
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        pane = self._make_pane()

        TmuxManager._send_command_to_pane(pane, "some_command --flag")

        set_buf_args = mock_subprocess.run.call_args_list[0][0][0]
        assert "owt-init" in set_buf_args
        assert "some_command --flag" in set_buf_args

    @patch("open_orchestrator.core.tmux_manager.subprocess")
    def test_passes_check_true_to_all_calls(self, mock_subprocess):
        """Test check=True is set on each subprocess call (raises on non-zero exit)."""
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        pane = self._make_pane()

        TmuxManager._send_command_to_pane(pane, "test")

        for call in mock_subprocess.run.call_args_list:
            kwargs = call[1]
            assert kwargs.get("check") is True

    @patch("open_orchestrator.core.tmux_manager.subprocess")
    def test_callable_as_classmethod(self, mock_subprocess):
        """Test _send_command_to_pane is accessible on an instance too."""
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        pane = self._make_pane()
        manager = TmuxManager()

        # Call through instance — should work identically
        manager._send_command_to_pane(pane, "pwd")
        assert mock_subprocess.run.call_count == 3


class TestCreateSessionBranchPaths:
    """Tests for create_session branch paths not yet covered."""

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_libtmux_exception_with_empty_message(self, mock_server_prop, temp_dir):
        """Test LibTmuxException with empty message provides a helpful fallback."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.side_effect = libtmux.exc.LibTmuxException("")
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            auto_start_ai=False,
        )

        with pytest.raises(TmuxError) as exc_info:
            manager.create_session(config)

        assert "tmux server may not be running" in str(exc_info.value)

    @patch.object(TmuxManager, "_send_command_to_pane")
    @patch.object(TmuxManager, "_wait_for_shell_ready")
    @patch.object(AITool, "is_installed", return_value=True)
    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_raises_when_active_pane_is_none(
        self, mock_server_prop, mock_is_installed, mock_wait, mock_send_cmd,
        temp_dir, mock_libtmux_session: MagicMock,
    ):
        """Test TmuxError raised when active pane is None and auto_start_ai is True."""
        mock_libtmux_session.active_window.active_pane = None

        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            auto_start_ai=True,
        )

        with pytest.raises(TmuxError) as exc_info:
            manager.create_session(config)

        assert "No active pane" in str(exc_info.value)

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_sets_mouse_mode(self, mock_server_prop, temp_dir, mock_libtmux_session: MagicMock):
        """Test mouse mode is enabled on the session when mouse_mode=True."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            auto_start_ai=False,
            mouse_mode=True,
        )

        manager.create_session(config)

        mock_libtmux_session.set_option.assert_any_call("mouse", "on")

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_sets_prefix_key(self, mock_server_prop, temp_dir, mock_libtmux_session: MagicMock):
        """Test custom prefix key is set when prefix_key is specified."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            auto_start_ai=False,
            prefix_key="C-a",
        )

        manager.create_session(config)

        mock_libtmux_session.set_option.assert_any_call("prefix", "C-a")

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_skips_prefix_key_when_none(self, mock_server_prop, temp_dir, mock_libtmux_session: MagicMock):
        """Test prefix key is NOT set when prefix_key is None (default)."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            auto_start_ai=False,
            prefix_key=None,
        )

        manager.create_session(config)

        set_option_calls = [call[0] for call in mock_libtmux_session.set_option.call_args_list]
        assert ("prefix", None) not in set_option_calls

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_create_session_sets_pane_border_options(self, mock_server_prop, temp_dir, mock_libtmux_session: MagicMock):
        """Test pane border status and format window options are applied."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server.new_session.return_value = mock_libtmux_session
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        config = TmuxSessionConfig(
            session_name="test-session",
            working_directory=str(temp_dir),
            auto_start_ai=False,
        )

        manager.create_session(config)

        window = mock_libtmux_session.active_window
        window.set_window_option.assert_any_call("pane-border-status", "top")
        window.set_window_option.assert_any_call("pane-border-format", " #{pane_title} ")


class TestGetSessionInfo:
    """Tests for _get_session_info edge cases."""

    def test_get_session_info_without_created_attribute(self):
        """Test _get_session_info falls back to 'unknown' when session lacks created attr."""
        session = MagicMock(spec=["name", "id", "windows", "attached_count"])
        session.name = "owt-test"
        session.id = "$5"
        session.attached_count = 0

        window = MagicMock()
        pane = MagicMock()
        pane.pane_current_path = "/tmp/test"
        window.panes = [pane]
        session.windows = [window]

        manager = TmuxManager()
        info = manager._get_session_info(session)

        assert info.created_at == "unknown"

    def test_get_session_info_with_empty_windows(self):
        """Test _get_session_info handles session with no windows."""
        session = MagicMock()
        session.name = "owt-empty"
        session.id = "$6"
        session.attached_count = 0
        session.windows = []

        manager = TmuxManager()
        info = manager._get_session_info(session)

        assert info.window_count == 0
        assert info.pane_count == 0
        assert info.working_directory is None


class TestSwitchClient:
    """Tests for switch_client method."""

    @patch("subprocess.run")
    def test_switch_client_success(self, mock_run):
        """Test successful client switch."""
        manager = TmuxManager()

        with patch.object(manager, "session_exists", return_value=True):
            manager.switch_client("test-session")

        mock_run.assert_called_once_with(
            ["tmux", "switch-client", "-t", "test-session"], check=True
        )

    @patch("subprocess.run")
    def test_switch_client_session_not_found(self, mock_run):
        """Test switch_client raises TmuxSessionNotFoundError when session missing."""
        manager = TmuxManager()

        with patch.object(manager, "session_exists", return_value=False):
            with pytest.raises(TmuxSessionNotFoundError) as exc_info:
                manager.switch_client("nonexistent")

        assert "not found" in str(exc_info.value)
        mock_run.assert_not_called()


class TestListSessionsExceptionPath:
    """Tests for list_sessions LibTmuxException path."""

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_list_sessions_returns_empty_on_libtmux_exception(self, mock_server_prop):
        """Test list_sessions returns [] when LibTmuxException accessing sessions."""
        mock_server = MagicMock()
        type(mock_server).sessions = PropertyMock(side_effect=libtmux.exc.LibTmuxException("no server"))
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.list_sessions()

        assert result == []


class TestKillSessionExceptionPath:
    """Tests for kill_session exception handling."""

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_kill_session_raises_tmux_error_on_libtmux_exception(self, mock_server_prop):
        """Test kill_session raises TmuxError when LibTmuxException during kill."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        mock_session = MagicMock()
        mock_session.kill.side_effect = libtmux.exc.LibTmuxException("kill failed")
        sessions_mock = MagicMock()
        sessions_mock.filter.return_value = [mock_session]
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()

        with pytest.raises(TmuxError) as exc_info:
            manager.kill_session("test-session")

        assert "Failed to kill session" in str(exc_info.value)

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_kill_session_raises_tmux_error_on_index_error(self, mock_server_prop):
        """Test kill_session raises TmuxError when sessions list is empty (IndexError)."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        sessions_mock = MagicMock()
        sessions_mock.filter.return_value = []  # empty → IndexError on [0]
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()

        with pytest.raises(TmuxError) as exc_info:
            manager.kill_session("test-session")

        assert "Failed to kill session" in str(exc_info.value)


class TestGetSessionForWorktreeExceptionPath:
    """Tests for get_session_for_worktree exception handling."""

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_returns_none_when_index_error(self, mock_server_prop):
        """Test returns None when filter returns empty list (IndexError)."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        sessions_mock = MagicMock()
        sessions_mock.filter.return_value = []  # triggers IndexError on [0]
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.get_session_for_worktree("feature-test")

        assert result is None

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_returns_none_when_libtmux_exception(self, mock_server_prop):
        """Test returns None when LibTmuxException during session lookup."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        sessions_mock = MagicMock()
        sessions_mock.filter.side_effect = libtmux.exc.LibTmuxException("error")
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.get_session_for_worktree("feature-test")

        assert result is None


class TestGetCurrentSessionNameExceptionPath:
    """Tests for get_current_session_name CalledProcessError path."""

    @patch("subprocess.run")
    def test_returns_none_on_called_process_error(self, mock_run):
        """Test returns None when tmux command exits with non-zero status."""
        import subprocess as sp
        mock_run.side_effect = sp.CalledProcessError(returncode=1, cmd="tmux")

        manager = TmuxManager()

        with patch.dict(os.environ, {"TMUX": "/tmp/tmux"}):
            result = manager.get_current_session_name()

        assert result is None


class TestSendKeysToPaneExceptionPath:
    """Tests for send_keys_to_pane CalledProcessError path."""

    @patch("open_orchestrator.core.tmux_manager.subprocess")
    def test_raises_tmux_error_on_called_process_error(self, mock_subprocess):
        """Test send_keys_to_pane raises TmuxError when subprocess fails."""
        import subprocess as sp

        mock_subprocess.run.side_effect = sp.CalledProcessError(returncode=1, cmd="tmux")
        mock_subprocess.CalledProcessError = sp.CalledProcessError

        manager = TmuxManager()

        with patch.object(manager, "session_exists", return_value=True):
            with pytest.raises(TmuxError) as exc_info:
                manager.send_keys_to_pane("test-session", "echo hello")

        assert "Failed to send keys" in str(exc_info.value)


class TestGetPaneCount:
    """Tests for get_pane_count method."""

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_returns_pane_count_for_active_window(self, mock_server_prop, mock_libtmux_session: MagicMock):
        """Test returns number of panes in the active window."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        # Session has 2 panes in its active window
        mock_libtmux_session.active_window.panes = [MagicMock(), MagicMock()]
        sessions_mock = MagicMock()
        sessions_mock.filter.return_value = [mock_libtmux_session]
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()
        result = manager.get_pane_count("owt-test")

        assert result == 2

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_raises_session_not_found_when_missing(self, mock_server_prop):
        """Test raises TmuxSessionNotFoundError when session doesn't exist."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = False
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()

        with pytest.raises(TmuxSessionNotFoundError):
            manager.get_pane_count("nonexistent")

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_raises_tmux_error_on_libtmux_exception(self, mock_server_prop):
        """Test raises TmuxError when LibTmuxException occurs during lookup."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        sessions_mock = MagicMock()
        sessions_mock.filter.side_effect = libtmux.exc.LibTmuxException("error")
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()

        with pytest.raises(TmuxError) as exc_info:
            manager.get_pane_count("owt-test")

        assert "Failed to get pane count" in str(exc_info.value)

    @patch.object(TmuxManager, "server", new_callable=PropertyMock)
    def test_raises_tmux_error_on_index_error(self, mock_server_prop):
        """Test raises TmuxError when sessions filter returns empty list (IndexError)."""
        mock_server = MagicMock()
        mock_server.has_session.return_value = True

        sessions_mock = MagicMock()
        sessions_mock.filter.return_value = []  # [0] raises IndexError
        mock_server.sessions = sessions_mock
        mock_server_prop.return_value = mock_server

        manager = TmuxManager()

        with pytest.raises(TmuxError) as exc_info:
            manager.get_pane_count("owt-test")

        assert "Failed to get pane count" in str(exc_info.value)


class TestGetTmuxVersion:
    """Tests for get_tmux_version static method."""

    @patch("subprocess.run")
    def test_parses_standard_version_string(self, mock_run):
        """Test version parsing with typical 'tmux 3.3a' output."""
        mock_run.return_value = MagicMock(stdout="tmux 3.3a\n")

        major, minor = TmuxManager.get_tmux_version()

        assert major == 3
        assert minor == 3

    @patch("subprocess.run")
    def test_parses_next_prefix(self, mock_run):
        """Test version parsing strips 'next-' prefix."""
        mock_run.return_value = MagicMock(stdout="tmux next-3.4\n")

        major, minor = TmuxManager.get_tmux_version()

        assert major == 3
        assert minor == 4

    @patch("subprocess.run")
    def test_returns_zero_on_called_process_error(self, mock_run):
        """Test returns (0, 0) when tmux -V fails."""
        import subprocess as sp
        mock_run.side_effect = sp.CalledProcessError(returncode=1, cmd="tmux")

        assert TmuxManager.get_tmux_version() == (0, 0)

    @patch("subprocess.run")
    def test_returns_zero_on_unparseable_version(self, mock_run):
        """Test returns (0, 0) when version string cannot be parsed."""
        mock_run.return_value = MagicMock(stdout="tmux unknown-version\n")

        assert TmuxManager.get_tmux_version() == (0, 0)

    @patch("subprocess.run")
    def test_parses_major_only_version_string(self, mock_run):
        """Test a version with only a major number returns (major, 0)."""
        # Matches the single-digit regex path at line 529-531
        mock_run.return_value = MagicMock(stdout="tmux 3\n")

        major, minor = TmuxManager.get_tmux_version()

        assert major == 3
        assert minor == 0


class TestRunTmuxCmd:
    """Tests for _run_tmux_cmd static method."""

    @patch("subprocess.run")
    def test_returns_true_on_success(self, mock_run):
        """Test returns True when tmux command exits with code 0."""
        mock_run.return_value = MagicMock(returncode=0)

        result = TmuxManager._run_tmux_cmd("new-session", "-d")

        assert result is True
        mock_run.assert_called_once_with(
            ["tmux", "new-session", "-d"],
            check=False, capture_output=True, text=True,
        )

    @patch("subprocess.run")
    def test_returns_false_on_failure(self, mock_run):
        """Test returns False when tmux command exits with non-zero code."""
        mock_run.return_value = MagicMock(returncode=1)

        result = TmuxManager._run_tmux_cmd("kill-session", "-t", "owt-test")

        assert result is False


class TestRunTmuxBatch:
    """Tests for _run_tmux_batch static method."""

    @patch("subprocess.run")
    def test_returns_true_with_no_commands(self, mock_run):
        """Test _run_tmux_batch returns True immediately when given no commands."""
        result = TmuxManager._run_tmux_batch()

        assert result is True
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_chains_multiple_commands_with_semicolons(self, mock_run):
        """Test _run_tmux_batch joins multiple commands with ';' separators."""
        mock_run.return_value = MagicMock(returncode=0)

        result = TmuxManager._run_tmux_batch(
            ("set-option", "-t", "owt-test", "mouse", "on"),
            ("set-option", "-t", "owt-test", "status", "on"),
        )

        assert result is True
        combined_cmd = mock_run.call_args[0][0]
        assert "tmux" in combined_cmd
        assert ";" in combined_cmd

    @patch("subprocess.run")
    def test_returns_false_when_command_fails(self, mock_run):
        """Test _run_tmux_batch returns False when subprocess exits non-zero."""
        mock_run.return_value = MagicMock(returncode=1)

        result = TmuxManager._run_tmux_batch(
            ("set-option", "-t", "owt-test", "mouse", "on"),
        )

        assert result is False


class TestInstallStatusBar:
    """Tests for install_status_bar method."""

    @patch.object(TmuxManager, "_run_tmux_batch")
    def test_install_status_bar_calls_run_tmux_batch(self, mock_batch):
        """Test install_status_bar calls _run_tmux_batch with all expected options."""
        mock_batch.return_value = True

        manager = TmuxManager()
        manager.install_status_bar("owt-test")

        mock_batch.assert_called_once()
        # Unpack the positional args passed as individual tuples
        call_args = mock_batch.call_args[0]
        # Flatten all tuples into one list to check for expected option keys
        all_args = [item for tup in call_args for item in tup]
        assert "status-right" in all_args
        assert "status-interval" in all_args
        assert "pane-border-format" in all_args
        assert "pane-active-border-style" in all_args

    @patch.object(TmuxManager, "_run_tmux_batch")
    def test_install_status_bar_targets_correct_session(self, mock_batch):
        """Test that all set-option calls target the supplied session name."""
        mock_batch.return_value = True

        manager = TmuxManager()
        manager.install_status_bar("owt-my-feature")

        call_args = mock_batch.call_args[0]
        # Every tuple should contain the session name as the -t argument
        for tup in call_args:
            tup_list = list(tup)
            if "-t" in tup_list:
                idx = tup_list.index("-t")
                assert tup_list[idx + 1] == "owt-my-feature"

    @patch("subprocess.run")
    def test_install_status_bar_via_run_tmux_batch(self, mock_run):
        """Test install_status_bar results in subprocess.run being called."""
        mock_run.return_value = MagicMock(returncode=0)

        manager = TmuxManager()
        manager.install_status_bar("owt-test-session")

        # _run_tmux_batch issues a single subprocess.run call
        assert mock_run.call_count >= 1
        combined_cmd = mock_run.call_args[0][0]
        assert "tmux" in combined_cmd

    @patch.object(TmuxManager, "_run_tmux_batch")
    def test_install_status_bar_includes_owt_branding_in_status_right(self, mock_batch):
        """Test status-right value contains OWT branding text."""
        mock_batch.return_value = True

        manager = TmuxManager()
        manager.install_status_bar("owt-test")

        call_args = mock_batch.call_args[0]
        # Find the status-right tuple and verify it contains [owt]
        status_right_value = None
        for tup in call_args:
            tup_list = list(tup)
            if "status-right" in tup_list:
                idx = tup_list.index("status-right")
                status_right_value = tup_list[idx + 1]
                break

        assert status_right_value is not None
        assert "[owt]" in status_right_value


class TestSetupMainVerticalEdgeCases:
    """Tests for _setup_main_vertical edge cases."""

    def test_setup_main_vertical_no_pane_select_when_empty(self):
        """Test _setup_main_vertical does not call select() when panes list is empty."""
        window = MagicMock()
        window.panes = []  # empty panes — covers the 'if panes' False branch

        manager = TmuxManager()
        manager._setup_main_vertical(window, pane_count=1, working_dir="/tmp")

        # No panes to select
        for pane in window.panes:
            pane.select.assert_not_called()

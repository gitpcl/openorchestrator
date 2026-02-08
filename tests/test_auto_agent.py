"""Tests for autonomous agent functionality."""

import time
from pathlib import Path
from unittest.mock import Mock, patch

import pexpect
import pytest

from open_orchestrator.config import AITool
from open_orchestrator.core.auto_agent import (
    AutoAgent,
    AutoAgentError,
    AutoAgentMonitor,
    AutoAgentPromptPatterns,
    AutoAgentTimeoutError,
)
from open_orchestrator.models.status import AIActivityStatus


@pytest.fixture
def mock_worktree_path(tmp_path: Path) -> Path:
    """Create a mock worktree directory."""
    worktree = tmp_path / "test-worktree"
    worktree.mkdir()
    return worktree


@pytest.fixture
def mock_log_file(tmp_path: Path) -> Path:
    """Create a mock log file path."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return log_dir / "test.log"


class TestAutoAgentPromptPatterns:
    """Test prompt pattern matching."""

    def test_workspace_trust_pattern(self):
        """Test workspace trust pattern matches common prompts."""
        assert AutoAgentPromptPatterns.WORKSPACE_TRUST.search("Do you trust this folder?")
        assert AutoAgentPromptPatterns.WORKSPACE_TRUST.search("workspace trust required")
        assert AutoAgentPromptPatterns.WORKSPACE_TRUST.search("Trust this folder")

    def test_ready_state_pattern(self):
        """Test ready state pattern matches common states."""
        assert AutoAgentPromptPatterns.READY_STATE.search("How can I help you?")
        assert AutoAgentPromptPatterns.READY_STATE.search("What can I do for you?")
        assert AutoAgentPromptPatterns.READY_STATE.search("Ready for input")

    def test_error_pattern(self):
        """Test error pattern matches common errors."""
        assert AutoAgentPromptPatterns.ERROR_PATTERN.search("Error: File not found")
        assert AutoAgentPromptPatterns.ERROR_PATTERN.search("Failed to execute")
        assert AutoAgentPromptPatterns.ERROR_PATTERN.search("Exception occurred")

    def test_completion_pattern(self):
        """Test completion pattern matches success states."""
        assert AutoAgentPromptPatterns.COMPLETION_MARKERS.search("Task complete")
        assert AutoAgentPromptPatterns.COMPLETION_MARKERS.search("Done")
        assert AutoAgentPromptPatterns.COMPLETION_MARKERS.search("Completed successfully")


class TestAutoAgent:
    """Test autonomous agent functionality."""

    def test_init(self, mock_worktree_path: Path, mock_log_file: Path):
        """Test agent initialization."""
        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
            log_file=mock_log_file,
        )

        assert agent.worktree_path == mock_worktree_path
        assert agent.task == "Test task"
        assert agent.ai_tool == AITool.CLAUDE
        assert agent.log_file == mock_log_file
        assert agent.status == AIActivityStatus.IDLE
        assert agent.process is None

    def test_init_with_nonexistent_worktree(self, tmp_path: Path):
        """Test initialization fails with nonexistent worktree."""
        nonexistent = tmp_path / "nonexistent"

        agent = AutoAgent(
            worktree_path=nonexistent,
            task="Test task",
        )

        with pytest.raises(AutoAgentError, match="Worktree path does not exist"):
            agent.start()

    @patch("open_orchestrator.core.auto_agent.AITool.get_executable_path")
    @patch("pexpect.spawn")
    def test_start_success(
        self,
        mock_spawn: Mock,
        mock_get_executable: Mock,
        mock_worktree_path: Path,
        mock_log_file: Path,
    ):
        """Test successful agent start."""
        # Mock executable path
        mock_get_executable.return_value = "/usr/local/bin/claude"

        # Mock process
        mock_process = Mock()
        mock_process.isalive.return_value = True
        mock_process.before = "Output before match"
        mock_process.after = "Output after match"
        mock_spawn.return_value = mock_process

        # Mock expect to simulate ready state immediately (index 0 = READY_STATE)
        mock_process.expect.return_value = 0  # READY_STATE index (not ERROR_PATTERN)

        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
            log_file=mock_log_file,
        )

        agent.start()

        # Verify process was spawned
        assert mock_spawn.called
        assert agent.status == AIActivityStatus.WORKING
        assert agent.started_at is not None

        # Verify task was sent
        mock_process.sendline.assert_called_with("Test task")

    @patch("open_orchestrator.core.auto_agent.AITool.get_executable_path")
    def test_start_with_missing_executable(
        self,
        mock_get_executable: Mock,
        mock_worktree_path: Path,
    ):
        """Test start fails when AI tool is not installed."""
        mock_get_executable.return_value = None

        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
        )

        with pytest.raises(AutoAgentError, match="is not installed"):
            agent.start()

    def test_is_alive_no_process(self):
        """Test is_alive returns False when no process."""
        agent = AutoAgent(
            worktree_path=Path("/tmp"),
            task="Test",
        )

        assert not agent.is_alive()

    @patch("pexpect.spawn")
    def test_is_complete_when_not_alive(self, mock_spawn: Mock, mock_worktree_path: Path):
        """Test is_complete returns True when process exits."""
        mock_process = Mock()
        mock_process.isalive.return_value = False
        mock_spawn.return_value = mock_process

        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
        )

        agent.process = mock_process

        assert agent.is_complete()
        assert agent.status == AIActivityStatus.COMPLETED

    @patch("pexpect.spawn")
    def test_stop_running_process(self, mock_spawn: Mock, mock_worktree_path: Path):
        """Test stopping a running process."""
        mock_process = Mock()
        mock_process.isalive.return_value = True
        mock_spawn.return_value = mock_process

        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
        )

        agent.process = mock_process

        agent.stop()

        # Verify Ctrl+C was sent
        mock_process.sendcontrol.assert_called_with("c")

    @patch("pexpect.spawn")
    def test_stop_force(self, mock_spawn: Mock, mock_worktree_path: Path):
        """Test force stopping a process."""
        mock_process = Mock()
        mock_process.isalive.return_value = True
        mock_spawn.return_value = mock_process

        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
        )

        agent.process = mock_process

        agent.stop(force=True)

        # Verify SIGKILL was used
        mock_process.kill.assert_called_with(signal=9)

    def test_get_output_no_log(self, mock_worktree_path: Path):
        """Test get_output returns empty string when no log file."""
        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
        )

        assert agent.get_output() == ""

    def test_get_output_with_log(self, mock_worktree_path: Path, mock_log_file: Path):
        """Test get_output reads from log file."""
        # Write test content to log
        mock_log_file.write_text("Line 1\nLine 2\nLine 3\n")

        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
            log_file=mock_log_file,
        )

        output = agent.get_output(lines=2)

        assert "Line 2" in output
        assert "Line 3" in output
        assert "Line 1" not in output


class TestAutoAgentMonitor:
    """Test autonomous agent monitoring."""

    @patch("pexpect.spawn")
    def test_check_health_healthy(self, mock_spawn: Mock, mock_worktree_path: Path):
        """Test health check for healthy agent."""
        mock_process = Mock()
        mock_process.isalive.return_value = True
        mock_spawn.return_value = mock_process

        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
        )

        agent.process = mock_process

        monitor = AutoAgentMonitor(agent)

        healthy, issue = monitor.check_health()

        assert healthy
        assert issue is None

    @patch("pexpect.spawn")
    def test_check_health_error_detected(
        self,
        mock_spawn: Mock,
        mock_worktree_path: Path,
        mock_log_file: Path,
    ):
        """Test health check detects errors in output."""
        # Write error output to log
        mock_log_file.write_text("Error: Something went wrong\n" * 5)

        mock_process = Mock()
        mock_process.isalive.return_value = True
        mock_spawn.return_value = mock_process

        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
            log_file=mock_log_file,
        )

        agent.process = mock_process

        monitor = AutoAgentMonitor(agent)

        healthy, issue = monitor.check_health()

        assert not healthy
        assert "errors" in issue.lower()

    @patch("pexpect.spawn")
    def test_auto_recover(self, mock_spawn: Mock, mock_worktree_path: Path):
        """Test auto-recovery attempt."""
        mock_process = Mock()
        mock_process.isalive.return_value = True
        mock_spawn.return_value = mock_process

        agent = AutoAgent(
            worktree_path=mock_worktree_path,
            task="Test task",
        )

        agent.process = mock_process

        # Simulate error state
        agent.error_message = "Test error"

        monitor = AutoAgentMonitor(agent)

        # Auto-recover should send Ctrl+C and retry command
        result = monitor.auto_recover()

        # Verify recovery was attempted
        if result:
            mock_process.sendcontrol.assert_called_with("c")

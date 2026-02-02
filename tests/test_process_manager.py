"""
Tests for ProcessManager class and CLI process commands.
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.config import AITool
from open_orchestrator.core.process_manager import (
    ProcessAlreadyRunningError,
    ProcessError,
    ProcessInfo,
    ProcessManager,
    ProcessManagerConfig,
    ProcessNotFoundError,
)
from open_orchestrator.core.worktree import WorktreeNotFoundError


@pytest.fixture
def temp_process_store(temp_directory: Path) -> Path:
    """Create a temporary process store directory for testing."""
    store_dir = temp_directory / ".open-orchestrator"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir / "processes.json"


@pytest.fixture
def temp_log_dir(temp_directory: Path) -> Path:
    """Create a temporary log directory for testing."""
    log_dir = temp_directory / ".cache" / "open-orchestrator" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


@pytest.fixture
def process_manager(temp_process_store: Path, temp_log_dir: Path) -> ProcessManager:
    """Create a ProcessManager instance with temporary storage."""
    config = ProcessManagerConfig(
        storage_path=temp_process_store,
        log_directory=temp_log_dir,
        auto_cleanup_dead=True,
    )
    return ProcessManager(config=config)


@pytest.fixture
def mock_worktree_path(temp_directory: Path) -> Path:
    """Create a mock worktree directory."""
    worktree_path = temp_directory / "worktree-test"
    worktree_path.mkdir()
    return worktree_path


class TestProcessManager:
    """Test ProcessManager core methods."""

    @patch("os.kill")
    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    def test_start_ai_tool_creates_process_and_persists_state(
        self,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        mock_kill: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that start_ai_tool creates process and persists state."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process
        mock_kill.return_value = None  # Process is alive

        # Act
        proc_info = process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Assert
        assert proc_info.pid == mock_process.pid
        assert proc_info.worktree_name == "test-worktree"
        assert proc_info.ai_tool == "claude"
        assert proc_info.log_file is not None

        # Verify process is in store
        stored_proc = process_manager.get_process("test-worktree")
        assert stored_proc is not None
        assert stored_proc.pid == mock_process.pid

        # Verify command was called correctly
        mock_popen.assert_called_once()
        call_kwargs = mock_popen.call_args.kwargs
        assert call_kwargs["shell"] is True
        assert call_kwargs["cwd"] == mock_worktree_path

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_start_ai_tool_raises_error_for_duplicate_start(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that start_ai_tool raises ProcessAlreadyRunningError for duplicate start."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process
        mock_kill.return_value = None  # Process is alive

        # Start process first time
        process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Act & Assert
        with pytest.raises(ProcessAlreadyRunningError, match="already running"):
            process_manager.start_ai_tool(
                worktree_name="test-worktree",
                worktree_path=mock_worktree_path,
                ai_tool=AITool.CLAUDE,
            )

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_start_ai_tool_replaces_dead_process(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that start_ai_tool replaces dead process entries."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process

        # Start process first time
        first_pid = 12345
        mock_process.pid = first_pid
        mock_kill.return_value = None  # First process is alive

        process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Simulate process death
        mock_kill.side_effect = ProcessLookupError()

        # Start new process
        second_pid = 67890
        mock_process.pid = second_pid

        # Act
        proc_info = process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Assert
        assert proc_info.pid == second_pid

    @patch("open_orchestrator.config.AITool.get_executable_path")
    def test_start_ai_tool_raises_error_for_missing_executable(
        self,
        mock_get_executable: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
    ) -> None:
        """Test that start_ai_tool raises ProcessError for missing executable."""
        # Arrange
        mock_get_executable.return_value = None

        # Act & Assert
        with pytest.raises(ProcessError, match="not installed"):
            process_manager.start_ai_tool(
                worktree_name="test-worktree",
                worktree_path=mock_worktree_path,
                ai_tool=AITool.CLAUDE,
            )

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_stop_ai_tool_terminates_process_gracefully(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that stop_ai_tool terminates process with SIGTERM."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process

        # Start process
        process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Simulate process death after SIGTERM
        def kill_side_effect(pid, sig):
            if sig == 15:  # SIGTERM
                # Next check should fail (process dead)
                mock_kill.side_effect = ProcessLookupError()

        mock_kill.side_effect = kill_side_effect

        # Act
        result = process_manager.stop_ai_tool("test-worktree", force=False)

        # Assert
        assert result is True
        assert process_manager.get_process("test-worktree") is None

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_stop_ai_tool_with_force_uses_sigkill(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that stop_ai_tool with force uses SIGKILL."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process

        # Start process
        process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Simulate process death after SIGKILL
        def kill_side_effect(pid, sig):
            if sig == 9:  # SIGKILL
                mock_kill.side_effect = ProcessLookupError()

        mock_kill.side_effect = kill_side_effect

        # Act
        result = process_manager.stop_ai_tool("test-worktree", force=True)

        # Assert
        assert result is True
        # Verify SIGKILL was sent
        assert any(call.args[1] == 9 for call in mock_kill.call_args_list)

    def test_stop_ai_tool_raises_error_for_missing_process(
        self,
        process_manager: ProcessManager,
    ) -> None:
        """Test that stop_ai_tool raises ProcessNotFoundError for missing process."""
        # Act & Assert
        with pytest.raises(ProcessNotFoundError, match="No process found"):
            process_manager.stop_ai_tool("nonexistent-worktree")

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_stop_ai_tool_returns_false_for_already_stopped_process(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that stop_ai_tool returns False for already stopped process."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process

        # Start process
        mock_kill.return_value = None  # Alive initially
        process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Process dies before stop is called
        mock_kill.side_effect = ProcessLookupError()

        # Act
        result = process_manager.stop_ai_tool("test-worktree")

        # Assert
        assert result is False

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_get_process_returns_process_info_for_running_process(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that get_process returns ProcessInfo for running process."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process
        mock_kill.return_value = None  # Process is alive

        process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Act
        proc_info = process_manager.get_process("test-worktree")

        # Assert
        assert proc_info is not None
        assert proc_info.worktree_name == "test-worktree"
        assert proc_info.pid == mock_process.pid

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_get_process_returns_none_for_dead_process(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that get_process returns None for dead process."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process
        mock_kill.return_value = None  # Alive initially

        process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Process dies
        mock_kill.side_effect = ProcessLookupError()

        # Act
        proc_info = process_manager.get_process("test-worktree")

        # Assert
        assert proc_info is None

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_list_processes_returns_all_active_processes(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        temp_directory: Path,
    ) -> None:
        """Test that list_processes returns all active processes."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_kill.return_value = None  # All processes alive

        # Create multiple worktrees and start processes
        worktree_paths = []
        for i in range(3):
            path = temp_directory / f"worktree-{i}"
            path.mkdir()
            worktree_paths.append(path)

            mock_process = MagicMock()
            mock_process.pid = 12345 + i
            mock_popen.return_value = mock_process

            process_manager.start_ai_tool(
                worktree_name=f"worktree-{i}",
                worktree_path=path,
                ai_tool=AITool.CLAUDE,
            )

        # Act
        processes = process_manager.list_processes()

        # Assert
        assert len(processes) == 3
        assert all(isinstance(p, ProcessInfo) for p in processes)

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_is_running_returns_true_for_active_process(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that is_running returns True for active process."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process
        mock_kill.return_value = None  # Process is alive

        process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Act
        result = process_manager.is_running("test-worktree")

        # Assert
        assert result is True

    def test_is_running_returns_false_for_nonexistent_process(
        self,
        process_manager: ProcessManager,
    ) -> None:
        """Test that is_running returns False for nonexistent process."""
        # Act
        result = process_manager.is_running("nonexistent-worktree")

        # Assert
        assert result is False

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_dead_process_cleanup_removes_stale_entries(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        temp_directory: Path,
    ) -> None:
        """Test that dead process cleanup removes stale entries."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"

        # Start multiple processes
        worktree_paths = []
        for i in range(3):
            path = temp_directory / f"worktree-{i}"
            path.mkdir()
            worktree_paths.append(path)

            mock_process = MagicMock()
            mock_process.pid = 12345 + i
            mock_popen.return_value = mock_process
            mock_kill.return_value = None  # Alive initially

            process_manager.start_ai_tool(
                worktree_name=f"worktree-{i}",
                worktree_path=path,
                ai_tool=AITool.CLAUDE,
            )

        # Kill processes 1 and 2
        def kill_side_effect(pid, sig):
            if pid in [12346, 12347]:  # PIDs for worktree-1 and worktree-2
                raise ProcessLookupError()

        mock_kill.side_effect = kill_side_effect

        # Act
        process_manager._cleanup_dead_processes()

        # Assert
        processes = process_manager.list_processes()
        assert len(processes) == 1
        assert processes[0].worktree_name == "worktree-0"

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    def test_process_persistence_across_manager_instances(
        self,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        temp_process_store: Path,
        temp_log_dir: Path,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that process state persists across ProcessManager instances."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process

        # Create first manager and start process
        config1 = ProcessManagerConfig(
            storage_path=temp_process_store,
            log_directory=temp_log_dir,
            auto_cleanup_dead=False,  # Disable cleanup for this test
        )
        manager1 = ProcessManager(config=config1)

        with patch("os.kill", return_value=None):  # Process alive
            manager1.start_ai_tool(
                worktree_name="test-worktree",
                worktree_path=mock_worktree_path,
                ai_tool=AITool.CLAUDE,
            )

        # Create second manager (simulates restart)
        config2 = ProcessManagerConfig(
            storage_path=temp_process_store,
            log_directory=temp_log_dir,
            auto_cleanup_dead=False,
        )
        manager2 = ProcessManager(config=config2)

        # Act
        with patch("os.kill", return_value=None):  # Process alive
            proc_info = manager2.get_process("test-worktree")

        # Assert
        assert proc_info is not None
        assert proc_info.worktree_name == "test-worktree"
        assert proc_info.pid == mock_process.pid

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_log_file_creation_in_correct_directory(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
        temp_log_dir: Path,
    ) -> None:
        """Test that log files are created in the correct directory."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process
        mock_kill.return_value = None  # Process is alive

        # Act
        proc_info = process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Assert
        assert proc_info.log_file is not None
        log_path = Path(proc_info.log_file)
        assert log_path.parent == temp_log_dir
        assert "test-worktree" in log_path.name

    @patch("subprocess.Popen")
    @patch("open_orchestrator.config.AITool.get_executable_path")
    @patch("open_orchestrator.config.AITool.get_command")
    @patch("os.kill")
    def test_get_log_path_returns_correct_path(
        self,
        mock_kill: MagicMock,
        mock_get_command: MagicMock,
        mock_get_executable: MagicMock,
        mock_popen: MagicMock,
        process_manager: ProcessManager,
        mock_worktree_path: Path,
        mock_process: MagicMock,
    ) -> None:
        """Test that get_log_path returns correct log file path."""
        # Arrange
        mock_get_executable.return_value = "/usr/bin/claude"
        mock_get_command.return_value = "claude"
        mock_popen.return_value = mock_process
        mock_kill.return_value = None  # Process is alive

        proc_info = process_manager.start_ai_tool(
            worktree_name="test-worktree",
            worktree_path=mock_worktree_path,
            ai_tool=AITool.CLAUDE,
        )

        # Act
        log_path = process_manager.get_log_path("test-worktree")

        # Assert
        assert log_path is not None
        assert log_path == Path(proc_info.log_file)

    def test_get_log_path_returns_none_for_missing_process(
        self,
        process_manager: ProcessManager,
    ) -> None:
        """Test that get_log_path returns None for missing process."""
        # Act
        log_path = process_manager.get_log_path("nonexistent-worktree")

        # Assert
        assert log_path is None


class TestProcessCLI:
    """Test CLI process commands."""

    @patch("open_orchestrator.cli.get_worktree_manager")
    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_start_with_default_claude(
        self,
        mock_manager_class: MagicMock,
        mock_get_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_path: Path,
    ) -> None:
        """Test 'owt process start <worktree>' with default claude."""
        # Arrange
        mock_wt = MagicMock()
        mock_wt.name = "test-worktree"
        mock_wt.path = mock_worktree_path
        mock_wt_manager = mock_get_wt_manager.return_value
        mock_wt_manager.get.return_value = mock_wt

        mock_manager = mock_manager_class.return_value
        mock_proc_info = ProcessInfo(
            pid=12345,
            worktree_name="test-worktree",
            worktree_path=str(mock_worktree_path),
            ai_tool="claude",
            command="claude",
            started_at=datetime.now(),
            log_file="/path/to/log.log",
        )
        mock_manager.start_ai_tool.return_value = mock_proc_info

        # Act
        result = cli_runner.invoke(main, ["process", "start", "test-worktree"])

        # Assert
        assert result.exit_code == 0
        assert "Started claude for test-worktree" in result.output
        assert "PID: 12345" in result.output
        assert "Log: /path/to/log.log" in result.output
        mock_manager.start_ai_tool.assert_called_once()

    @patch("open_orchestrator.cli.get_worktree_manager")
    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_start_with_opencode_tool(
        self,
        mock_manager_class: MagicMock,
        mock_get_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_path: Path,
    ) -> None:
        """Test 'owt process start <worktree> --ai-tool opencode'."""
        # Arrange
        mock_wt = MagicMock()
        mock_wt.name = "test-worktree"
        mock_wt.path = mock_worktree_path
        mock_wt_manager = mock_get_wt_manager.return_value
        mock_wt_manager.get.return_value = mock_wt

        mock_manager = mock_manager_class.return_value
        mock_proc_info = ProcessInfo(
            pid=12345,
            worktree_name="test-worktree",
            worktree_path=str(mock_worktree_path),
            ai_tool="opencode",
            command="opencode",
            started_at=datetime.now(),
            log_file="/path/to/log.log",
        )
        mock_manager.start_ai_tool.return_value = mock_proc_info

        # Act
        result = cli_runner.invoke(main, ["process", "start", "test-worktree", "--ai-tool", "opencode"])

        # Assert
        assert result.exit_code == 0
        assert "Started opencode for test-worktree" in result.output

    @patch("open_orchestrator.cli.get_worktree_manager")
    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_start_with_plan_mode_flag(
        self,
        mock_manager_class: MagicMock,
        mock_get_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_path: Path,
    ) -> None:
        """Test 'owt process start' with --plan-mode flag."""
        # Arrange
        mock_wt = MagicMock()
        mock_wt.name = "test-worktree"
        mock_wt.path = mock_worktree_path
        mock_wt_manager = mock_get_wt_manager.return_value
        mock_wt_manager.get.return_value = mock_wt

        mock_manager = mock_manager_class.return_value
        mock_proc_info = ProcessInfo(
            pid=12345,
            worktree_name="test-worktree",
            worktree_path=str(mock_worktree_path),
            ai_tool="claude",
            command="claude --plan",
            started_at=datetime.now(),
            log_file="/path/to/log.log",
        )
        mock_manager.start_ai_tool.return_value = mock_proc_info

        # Act
        result = cli_runner.invoke(main, ["process", "start", "test-worktree", "--plan-mode"])

        # Assert
        assert result.exit_code == 0
        call_kwargs = mock_manager.start_ai_tool.call_args.kwargs
        assert call_kwargs["plan_mode"] is True

    @patch("open_orchestrator.cli.get_worktree_manager")
    def test_process_start_fails_for_missing_worktree(
        self,
        mock_get_wt_manager: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test that process start fails for missing worktree."""
        # Arrange
        mock_wt_manager = mock_get_wt_manager.return_value
        mock_wt_manager.get.side_effect = WorktreeNotFoundError("Worktree not found")

        # Act
        result = cli_runner.invoke(main, ["process", "start", "nonexistent"])

        # Assert
        assert result.exit_code != 0
        assert "Worktree not found" in result.output

    @patch("open_orchestrator.cli.get_worktree_manager")
    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_start_fails_if_already_running(
        self,
        mock_manager_class: MagicMock,
        mock_get_wt_manager: MagicMock,
        cli_runner: CliRunner,
        mock_worktree_path: Path,
    ) -> None:
        """Test that process start fails if process already running."""
        # Arrange
        mock_wt = MagicMock()
        mock_wt.name = "test-worktree"
        mock_wt.path = mock_worktree_path
        mock_wt_manager = mock_get_wt_manager.return_value
        mock_wt_manager.get.return_value = mock_wt

        mock_manager = mock_manager_class.return_value
        mock_manager.start_ai_tool.side_effect = ProcessAlreadyRunningError("Already running")

        # Act
        result = cli_runner.invoke(main, ["process", "start", "test-worktree"])

        # Assert
        assert result.exit_code != 0
        assert "Already running" in result.output

    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_stop_graceful_shutdown(
        self,
        mock_manager_class: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test 'owt process stop <worktree>' graceful shutdown."""
        # Arrange
        mock_manager = mock_manager_class.return_value
        mock_manager.stop_ai_tool.return_value = True

        # Act
        result = cli_runner.invoke(main, ["process", "stop", "test-worktree"])

        # Assert
        assert result.exit_code == 0
        assert "Stopped process for test-worktree" in result.output
        mock_manager.stop_ai_tool.assert_called_once_with("test-worktree", force=False)

    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_stop_with_force_flag(
        self,
        mock_manager_class: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test 'owt process stop <worktree> --force' force kill."""
        # Arrange
        mock_manager = mock_manager_class.return_value
        mock_manager.stop_ai_tool.return_value = True

        # Act
        result = cli_runner.invoke(main, ["process", "stop", "test-worktree", "--force"])

        # Assert
        assert result.exit_code == 0
        mock_manager.stop_ai_tool.assert_called_once_with("test-worktree", force=True)

    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_stop_shows_message_if_already_stopped(
        self,
        mock_manager_class: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test that process stop shows message if already stopped."""
        # Arrange
        mock_manager = mock_manager_class.return_value
        mock_manager.stop_ai_tool.return_value = False

        # Act
        result = cli_runner.invoke(main, ["process", "stop", "test-worktree"])

        # Assert
        assert result.exit_code == 0
        assert "already stopped" in result.output

    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_stop_fails_for_missing_process(
        self,
        mock_manager_class: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test that process stop fails for missing process."""
        # Arrange
        mock_manager = mock_manager_class.return_value
        mock_manager.stop_ai_tool.side_effect = ProcessNotFoundError("No process found")

        # Act
        result = cli_runner.invoke(main, ["process", "stop", "nonexistent"])

        # Assert
        assert result.exit_code != 0
        assert "No process found" in result.output

    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_list_displays_table_output(
        self,
        mock_manager_class: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test 'owt process list' displays table output."""
        # Arrange
        mock_manager = mock_manager_class.return_value
        mock_processes = [
            ProcessInfo(
                pid=12345,
                worktree_name="worktree-1",
                worktree_path="/path/to/worktree-1",
                ai_tool="claude",
                command="claude",
                started_at=datetime(2024, 2, 1, 10, 0, 0),
                log_file="/path/to/log1.log",
            ),
            ProcessInfo(
                pid=67890,
                worktree_name="worktree-2",
                worktree_path="/path/to/worktree-2",
                ai_tool="opencode",
                command="opencode",
                started_at=datetime(2024, 2, 1, 11, 0, 0),
                log_file="/path/to/log2.log",
            ),
        ]
        mock_manager.list_processes.return_value = mock_processes

        # Act
        result = cli_runner.invoke(main, ["process", "list"])

        # Assert
        assert result.exit_code == 0
        assert "worktree-1" in result.output
        assert "worktree-2" in result.output
        assert "claude" in result.output
        assert "opencode" in result.output
        assert "12345" in result.output
        assert "67890" in result.output

    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_list_shows_message_for_no_processes(
        self,
        mock_manager_class: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test that process list shows message when no processes running."""
        # Arrange
        mock_manager = mock_manager_class.return_value
        mock_manager.list_processes.return_value = []

        # Act
        result = cli_runner.invoke(main, ["process", "list"])

        # Assert
        assert result.exit_code == 0
        assert "No AI tool processes running" in result.output

    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_list_json_outputs_valid_json(
        self,
        mock_manager_class: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test 'owt process list --json' outputs valid JSON."""
        # Arrange
        mock_manager = mock_manager_class.return_value
        mock_processes = [
            ProcessInfo(
                pid=12345,
                worktree_name="worktree-1",
                worktree_path="/path/to/worktree-1",
                ai_tool="claude",
                command="claude",
                started_at=datetime(2024, 2, 1, 10, 0, 0),
                log_file="/path/to/log1.log",
            ),
        ]
        mock_manager.list_processes.return_value = mock_processes

        # Act
        result = cli_runner.invoke(main, ["process", "list", "--json"])

        # Assert
        assert result.exit_code == 0
        output_data = json.loads(result.output)
        assert isinstance(output_data, list)
        assert len(output_data) == 1
        assert output_data[0]["pid"] == 12345
        assert output_data[0]["worktree_name"] == "worktree-1"
        assert output_data[0]["ai_tool"] == "claude"

    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_logs_displays_log_content(
        self,
        mock_manager_class: MagicMock,
        cli_runner: CliRunner,
        temp_directory: Path,
    ) -> None:
        """Test 'owt process logs <worktree>' displays log content."""
        # Arrange
        mock_manager = mock_manager_class.return_value
        log_file = temp_directory / "test.log"
        log_content = "Line 1\nLine 2\nLine 3\n"
        log_file.write_text(log_content)
        mock_manager.get_log_path.return_value = log_file

        # Act
        result = cli_runner.invoke(main, ["process", "logs", "test-worktree"])

        # Assert
        assert result.exit_code == 0
        assert "Line 1" in result.output
        assert "Line 2" in result.output
        assert "Line 3" in result.output

    @patch("open_orchestrator.core.process_manager.ProcessManager")
    def test_process_logs_fails_for_missing_log_file(
        self,
        mock_manager_class: MagicMock,
        cli_runner: CliRunner,
    ) -> None:
        """Test that process logs fails for missing log file."""
        # Arrange
        mock_manager = mock_manager_class.return_value
        mock_manager.get_log_path.return_value = None

        # Act
        result = cli_runner.invoke(main, ["process", "logs", "test-worktree"])

        # Assert
        assert result.exit_code != 0
        assert "No log file found" in result.output

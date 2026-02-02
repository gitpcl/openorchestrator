"""
Process manager for non-tmux AI tool sessions.

This module provides functionality to manage AI tool processes without tmux,
allowing users who don't use tmux to still benefit from worktree management.
"""

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from open_orchestrator.config import AITool, DroidAutoLevel
from open_orchestrator.utils.io import atomic_write_text, shared_file_lock

logger = logging.getLogger(__name__)


class ProcessInfo(BaseModel):
    """Information about a managed AI tool process."""

    model_config = ConfigDict(use_enum_values=True)

    pid: int = Field(description="Process ID")
    worktree_name: str = Field(description="Associated worktree name")
    worktree_path: str = Field(description="Path to the worktree")
    ai_tool: str = Field(description="AI tool being run")
    command: str = Field(description="Full command that was executed")
    started_at: datetime = Field(default_factory=datetime.now)
    log_file: str | None = Field(default=None, description="Path to output log file")


class ProcessStore(BaseModel):
    """Persistent store for managed processes."""

    model_config = ConfigDict(use_enum_values=True)

    processes: dict[str, ProcessInfo] = Field(
        default_factory=dict,
        description="Map of worktree name to process info",
    )
    version: int = Field(default=1, description="Store format version")


class ProcessError(Exception):
    """Base exception for process management errors."""

    pass


class ProcessNotFoundError(ProcessError):
    """Raised when a process is not found."""

    pass


class ProcessAlreadyRunningError(ProcessError):
    """Raised when trying to start a process that's already running."""

    pass


@dataclass
class ProcessManagerConfig:
    """Configuration for the process manager."""

    storage_path: Path | None = None
    log_directory: Path | None = None
    auto_cleanup_dead: bool = True

    def __post_init__(self) -> None:
        if self.log_directory is None:
            self.log_directory = Path.home() / ".cache" / "open-orchestrator" / "logs"


class ProcessManager:
    """
    Manages AI tool processes without tmux.

    This provides an alternative to tmux for users who prefer simpler
    process management or don't have tmux installed.
    """

    DEFAULT_STORE_FILENAME = "processes.json"

    def __init__(self, config: ProcessManagerConfig | None = None):
        self.config = config or ProcessManagerConfig()
        self._storage_path = self.config.storage_path or self._get_default_path()
        self._store: ProcessStore = ProcessStore()
        self._load_store()

        # Ensure log directory exists
        if self.config.log_directory:
            self.config.log_directory.mkdir(parents=True, exist_ok=True)

    def _get_default_path(self) -> Path:
        """Get the default storage path for process store."""
        cache_dir = Path.home() / ".cache" / "open-orchestrator"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / self.DEFAULT_STORE_FILENAME

    def _load_store(self) -> None:
        """Load the process store from disk."""
        if not self._storage_path.exists():
            self._store = ProcessStore()
            return

        try:
            with open(self._storage_path) as f:
                with shared_file_lock(f):
                    data = f.read()
                    self._store = ProcessStore.model_validate_json(data)

            # Clean up dead processes on load
            if self.config.auto_cleanup_dead:
                self._cleanup_dead_processes()

        except Exception as e:
            logger.warning(f"Failed to load process store: {e}")
            self._store = ProcessStore()

    def _save_store(self) -> None:
        """Persist the process store to disk."""
        try:
            atomic_write_text(
                self._storage_path,
                self._store.model_dump_json(indent=2),
            )
        except Exception as e:
            logger.error(f"Failed to save process store: {e}")
            raise ProcessError(f"Failed to save process store: {e}") from e

    def _cleanup_dead_processes(self) -> None:
        """Remove entries for processes that are no longer running."""
        dead_worktrees = []

        for worktree_name, proc_info in self._store.processes.items():
            if not self._is_process_alive(proc_info.pid):
                dead_worktrees.append(worktree_name)
                logger.debug(f"Cleaning up dead process for {worktree_name}")

        for name in dead_worktrees:
            del self._store.processes[name]

        if dead_worktrees:
            self._save_store()

    def _is_process_alive(self, pid: int) -> bool:
        """Check if a process is still running."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def start_ai_tool(
        self,
        worktree_name: str,
        worktree_path: str | Path,
        ai_tool: AITool = AITool.CLAUDE,
        droid_auto: DroidAutoLevel | None = None,
        droid_skip_permissions: bool = False,
        opencode_config: str | None = None,
        plan_mode: bool = False,
    ) -> ProcessInfo:
        """
        Start an AI tool process for a worktree.

        Args:
            worktree_name: Name of the worktree.
            worktree_path: Path to the worktree directory.
            ai_tool: The AI tool to start.
            droid_auto: Droid auto mode level.
            droid_skip_permissions: Skip Droid permissions check.
            opencode_config: Path to OpenCode config file.
            plan_mode: Start Claude in plan mode.

        Returns:
            ProcessInfo for the started process.

        Raises:
            ProcessAlreadyRunningError: If a process is already running.
            ProcessError: If the process fails to start.
        """
        worktree_path = Path(worktree_path)

        # Check if already running
        if worktree_name in self._store.processes:
            existing = self._store.processes[worktree_name]
            if self._is_process_alive(existing.pid):
                raise ProcessAlreadyRunningError(
                    f"AI tool already running for {worktree_name} (PID: {existing.pid})"
                )
            # Dead process, remove it
            del self._store.processes[worktree_name]

        # Get executable path
        executable_path = AITool.get_executable_path(ai_tool)
        if not executable_path:
            raise ProcessError(
                f"{ai_tool.value} is not installed. {AITool.get_install_hint(ai_tool)}"
            )

        # Build command
        command = AITool.get_command(
            tool=ai_tool,
            executable_path=executable_path,
            droid_auto=droid_auto,
            droid_skip_permissions=droid_skip_permissions,
            opencode_config=opencode_config,
            plan_mode=plan_mode,
        )

        # Set up log file
        log_file = None
        if self.config.log_directory:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = self.config.log_directory / f"{worktree_name}_{timestamp}.log"

        try:
            # Start the process
            with open(log_file, "w") if log_file else subprocess.DEVNULL as output:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=worktree_path,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,  # Detach from parent
                )

            proc_info = ProcessInfo(
                pid=process.pid,
                worktree_name=worktree_name,
                worktree_path=str(worktree_path),
                ai_tool=ai_tool.value,
                command=command,
                started_at=datetime.now(),
                log_file=str(log_file) if log_file else None,
            )

            self._store.processes[worktree_name] = proc_info
            self._save_store()

            logger.info(f"Started {ai_tool.value} for {worktree_name} (PID: {process.pid})")
            return proc_info

        except Exception as e:
            raise ProcessError(f"Failed to start {ai_tool.value}: {e}") from e

    def stop_ai_tool(
        self,
        worktree_name: str,
        force: bool = False,
        timeout: int = 10,
    ) -> bool:
        """
        Stop an AI tool process for a worktree.

        Args:
            worktree_name: Name of the worktree.
            force: Use SIGKILL instead of SIGTERM.
            timeout: Seconds to wait for graceful shutdown before force kill.

        Returns:
            True if process was stopped, False if not found.

        Raises:
            ProcessNotFoundError: If no process found for worktree.
        """
        if worktree_name not in self._store.processes:
            raise ProcessNotFoundError(f"No process found for {worktree_name}")

        proc_info = self._store.processes[worktree_name]

        if not self._is_process_alive(proc_info.pid):
            del self._store.processes[worktree_name]
            self._save_store()
            return False

        try:
            # Try graceful shutdown first
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.kill(proc_info.pid, sig)

            if not force:
                # Wait for graceful shutdown
                for _ in range(timeout * 10):
                    if not self._is_process_alive(proc_info.pid):
                        break
                    time.sleep(0.1)
                else:
                    # Force kill if still running
                    os.kill(proc_info.pid, signal.SIGKILL)

            del self._store.processes[worktree_name]
            self._save_store()

            logger.info(f"Stopped process for {worktree_name} (PID: {proc_info.pid})")
            return True

        except ProcessLookupError:
            # Process already dead
            del self._store.processes[worktree_name]
            self._save_store()
            return False
        except Exception as e:
            raise ProcessError(f"Failed to stop process: {e}") from e

    def get_process(self, worktree_name: str) -> ProcessInfo | None:
        """Get process info for a worktree."""
        proc_info = self._store.processes.get(worktree_name)

        if proc_info and not self._is_process_alive(proc_info.pid):
            # Clean up dead process
            del self._store.processes[worktree_name]
            self._save_store()
            return None

        return proc_info

    def list_processes(self) -> list[ProcessInfo]:
        """List all running processes."""
        # Clean up dead processes first
        self._cleanup_dead_processes()
        return list(self._store.processes.values())

    def is_running(self, worktree_name: str) -> bool:
        """Check if a process is running for a worktree."""
        return self.get_process(worktree_name) is not None

    def get_log_path(self, worktree_name: str) -> Path | None:
        """Get the log file path for a worktree's process."""
        proc_info = self.get_process(worktree_name)
        if proc_info and proc_info.log_file:
            return Path(proc_info.log_file)
        return None


__all__ = [
    "ProcessInfo",
    "ProcessStore",
    "ProcessError",
    "ProcessNotFoundError",
    "ProcessAlreadyRunningError",
    "ProcessManagerConfig",
    "ProcessManager",
]

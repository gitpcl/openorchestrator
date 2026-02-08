"""
Autonomous AI agent that handles interactive prompts automatically.

This module provides functionality to run Claude Code (or other AI tools)
autonomously by automating responses to interactive prompts like workspace trust,
command execution, and other user input requirements.
"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Pattern

import pexpect

from open_orchestrator.config import AITool, DroidAutoLevel
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)


class AutoAgentError(Exception):
    """Base exception for autonomous agent errors."""

    pass


class AutoAgentTimeoutError(AutoAgentError):
    """Raised when agent times out waiting for expected output."""

    pass


class AutoAgentPromptPatterns:
    """Patterns for detecting and handling Claude Code interactive prompts."""

    # Workspace trust prompt
    WORKSPACE_TRUST = re.compile(r"Do you trust|trust this folder|workspace trust", re.IGNORECASE)

    # Ready state (Claude is waiting for input)
    READY_STATE = re.compile(r"How can I help|What can I do|Ready|Waiting for input", re.IGNORECASE)

    # Error patterns
    ERROR_PATTERN = re.compile(r"Error|Failed|Exception|Traceback", re.IGNORECASE)

    # Completion markers
    COMPLETION_MARKERS = re.compile(r"Task complete|Done|Finished|Completed successfully", re.IGNORECASE)

    # Blocked/stuck patterns
    BLOCKED_PATTERN = re.compile(r"Cannot proceed|Blocked|Stuck|Need help|clarification needed", re.IGNORECASE)


class AutoAgent:
    """
    Autonomous AI agent that handles interactive prompts automatically.

    Uses pexpect to spawn Claude Code and automate responses to:
    - Workspace trust dialogs
    - Command execution (auto-press Enter)
    - Permission prompts
    - Other interactive inputs
    """

    DEFAULT_TIMEOUT = 300  # 5 minutes
    WORKSPACE_TRUST_TIMEOUT = 30  # 30 seconds for workspace trust

    def __init__(
        self,
        worktree_path: Path,
        task: str,
        ai_tool: AITool = AITool.CLAUDE,
        log_file: Path | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        droid_auto: DroidAutoLevel | None = None,
        droid_skip_permissions: bool = False,
        opencode_config: str | None = None,
        plan_mode: bool = False,
    ):
        """
        Initialize autonomous agent.

        Args:
            worktree_path: Path to the worktree directory
            task: The task description to send to the AI
            ai_tool: AI tool to use (default: Claude)
            log_file: Optional file to log output to
            timeout: Default timeout in seconds for expect operations
            droid_auto: Droid auto mode level (if using droid)
            droid_skip_permissions: Skip droid permissions
            opencode_config: OpenCode config path (if using opencode)
            plan_mode: Start Claude in plan mode
        """
        self.worktree_path = Path(worktree_path)
        self.task = task
        self.ai_tool = ai_tool
        self.log_file = log_file
        self.timeout = timeout
        self.droid_auto = droid_auto
        self.droid_skip_permissions = droid_skip_permissions
        self.opencode_config = opencode_config
        self.plan_mode = plan_mode

        self.process: pexpect.spawn | None = None
        self.log_handle = None
        self.started_at: datetime | None = None
        self.status: AIActivityStatus = AIActivityStatus.IDLE
        self.error_message: str | None = None

    def start(self) -> None:
        """
        Start the autonomous agent.

        Spawns the AI tool process, handles workspace trust, and sends the task.

        Raises:
            AutoAgentError: If agent fails to start or encounters an error
        """
        if not self.worktree_path.exists():
            raise AutoAgentError(f"Worktree path does not exist: {self.worktree_path}")

        # Get AI tool command
        executable_path = AITool.get_executable_path(self.ai_tool)
        if not executable_path:
            raise AutoAgentError(f"{self.ai_tool.value} is not installed. {AITool.get_install_hint(self.ai_tool)}")

        command = AITool.get_command(
            tool=self.ai_tool,
            executable_path=executable_path,
            droid_auto=self.droid_auto,
            droid_skip_permissions=self.droid_skip_permissions,
            opencode_config=self.opencode_config,
            plan_mode=self.plan_mode,
        )

        logger.info(f"Starting autonomous agent: {command}")
        logger.info(f"Working directory: {self.worktree_path}")
        logger.info(f"Task: {self.task}")

        # Set up log file
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            self.log_handle = open(self.log_file, "wb")

        try:
            # Spawn process
            self.process = pexpect.spawn(
                command,
                cwd=str(self.worktree_path),
                timeout=self.timeout,
                encoding="utf-8",
                logfile=self.log_handle,
            )

            self.started_at = datetime.now()
            self.status = AIActivityStatus.WORKING

            # Handle interactive prompts
            self._handle_workspace_trust()

            # Wait for ready state
            self._wait_for_ready()

            # Send the task
            logger.info(f"Sending task to agent: {self.task}")
            self.process.sendline(self.task)

            logger.info("Autonomous agent started successfully")

        except Exception as e:
            self.status = AIActivityStatus.BLOCKED
            self.error_message = str(e)
            self._cleanup()
            raise AutoAgentError(f"Failed to start autonomous agent: {e}") from e

    def _handle_workspace_trust(self) -> None:
        """
        Handle workspace trust prompt.

        Claude Code may ask for workspace trust on first run in a new directory.
        This automatically approves the trust.
        """
        if not self.process:
            return

        try:
            index = self.process.expect(
                [
                    AutoAgentPromptPatterns.WORKSPACE_TRUST,
                    AutoAgentPromptPatterns.READY_STATE,
                    pexpect.TIMEOUT,
                    pexpect.EOF,
                ],
                timeout=self.WORKSPACE_TRUST_TIMEOUT,
            )

            if index == 0:  # Workspace trust prompt
                logger.info("Detected workspace trust prompt, approving...")
                # Common responses: "y", "yes", "1", etc.
                # Try multiple approaches
                self.process.sendline("yes")
                time.sleep(0.5)
                self.process.sendline("y")
                time.sleep(0.5)
                self.process.sendline("1")

                # Wait for ready state after approval
                self._wait_for_ready()

            elif index == 1:  # Already in ready state
                logger.info("Agent is ready, no workspace trust needed")

            elif index == 2:  # Timeout
                logger.warning("Timeout waiting for workspace trust or ready state")

            elif index == 3:  # EOF
                raise AutoAgentError("Process terminated unexpectedly during workspace trust")

        except pexpect.TIMEOUT:
            logger.warning("Timeout handling workspace trust, continuing anyway...")

    def _wait_for_ready(self, timeout: int | None = None) -> None:
        """
        Wait for the AI tool to reach ready state.

        Args:
            timeout: Optional timeout override

        Raises:
            AutoAgentTimeoutError: If timeout is reached
        """
        if not self.process:
            return

        wait_timeout = timeout or self.timeout

        try:
            index = self.process.expect(
                [AutoAgentPromptPatterns.READY_STATE, AutoAgentPromptPatterns.ERROR_PATTERN, pexpect.TIMEOUT, pexpect.EOF],
                timeout=wait_timeout,
            )

            if index == 0:  # Ready
                logger.info("Agent reached ready state")
            elif index == 1:  # Error
                error_context = self.process.before + self.process.after
                raise AutoAgentError(f"Error detected while waiting for ready state: {error_context}")
            elif index == 2:  # Timeout
                raise AutoAgentTimeoutError(f"Timeout waiting for ready state after {wait_timeout}s")
            elif index == 3:  # EOF
                raise AutoAgentError("Process terminated while waiting for ready state")

        except pexpect.TIMEOUT as e:
            raise AutoAgentTimeoutError(f"Timeout waiting for ready state: {e}") from e

    def is_alive(self) -> bool:
        """Check if the agent process is still running."""
        if not self.process:
            return False
        return self.process.isalive()

    def is_complete(self) -> bool:
        """
        Check if the agent has completed its task.

        Returns:
            True if task is complete, False otherwise
        """
        if not self.process:
            return True

        # Check if process has exited
        if not self.is_alive():
            self.status = AIActivityStatus.COMPLETED
            return True

        # Check for completion markers in recent output
        # This is a heuristic - in reality we'd need more sophisticated detection
        try:
            # Non-blocking check for completion patterns
            self.process.expect([AutoAgentPromptPatterns.COMPLETION_MARKERS, pexpect.TIMEOUT], timeout=0.1)
            self.status = AIActivityStatus.COMPLETED
            return True
        except pexpect.TIMEOUT:
            pass

        return False

    def get_status(self) -> AIActivityStatus:
        """
        Get current agent status.

        Returns:
            Current AIActivityStatus
        """
        if not self.is_alive():
            if self.error_message:
                return AIActivityStatus.BLOCKED
            return AIActivityStatus.COMPLETED

        return self.status

    def stop(self, force: bool = False) -> None:
        """
        Stop the agent gracefully or forcefully.

        Args:
            force: If True, use SIGKILL instead of SIGTERM
        """
        if not self.process:
            return

        logger.info(f"Stopping autonomous agent (force={force})")

        try:
            if self.process.isalive():
                if force:
                    self.process.kill(signal=9)  # SIGKILL
                else:
                    # Send Ctrl+C to interrupt
                    self.process.sendcontrol("c")
                    time.sleep(0.5)

                    # If still alive, terminate
                    if self.process.isalive():
                        self.process.terminate()
                        self.process.wait()

            self._cleanup()

        except Exception as e:
            logger.error(f"Error stopping agent: {e}")
            self._cleanup()

    def _cleanup(self) -> None:
        """Clean up resources."""
        if self.log_handle:
            try:
                self.log_handle.close()
            except Exception as e:
                logger.warning(f"Error closing log file: {e}")
            self.log_handle = None

    def get_output(self, lines: int = 50) -> str:
        """
        Get recent output from the agent.

        Args:
            lines: Number of lines to retrieve

        Returns:
            Recent output as string
        """
        if not self.log_file or not self.log_file.exists():
            return ""

        try:
            with open(self.log_file, "r") as f:
                all_lines = f.readlines()
                recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
                return "".join(recent)
        except Exception as e:
            logger.warning(f"Error reading agent output: {e}")
            return ""

    def __enter__(self) -> "AutoAgent":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Context manager exit."""
        self.stop()
        return False


class AutoAgentMonitor:
    """
    Monitors autonomous agents and detects health issues.

    Checks for:
    - Stuck tasks (no progress for X minutes)
    - Error loops (repeated failures)
    - High resource usage
    - Unexpected termination
    """

    def __init__(self, agent: AutoAgent):
        """
        Initialize monitor.

        Args:
            agent: The AutoAgent to monitor
        """
        self.agent = agent
        self.last_check: datetime | None = None
        self.last_output_size = 0

    def check_health(self) -> tuple[bool, str | None]:
        """
        Check agent health.

        Returns:
            Tuple of (healthy: bool, issue_description: str | None)
        """
        if not self.agent.is_alive():
            if self.agent.error_message:
                return False, f"Agent terminated with error: {self.agent.error_message}"
            return True, None

        # Check for stuck (no output change)
        if self.agent.log_file and self.agent.log_file.exists():
            current_size = self.agent.log_file.stat().st_size
            if self.last_output_size == current_size and self.last_check:
                stuck_duration = (datetime.now() - self.last_check).total_seconds()
                if stuck_duration > 300:  # 5 minutes
                    return False, f"Agent appears stuck (no output for {int(stuck_duration)}s)"

            self.last_output_size = current_size

        # Check for error patterns in recent output
        recent_output = self.agent.get_output(lines=20)
        if AutoAgentPromptPatterns.ERROR_PATTERN.search(recent_output):
            error_count = len(AutoAgentPromptPatterns.ERROR_PATTERN.findall(recent_output))
            if error_count >= 3:
                return False, f"Multiple errors detected in recent output ({error_count} errors)"

        # Check for blocked state
        if AutoAgentPromptPatterns.BLOCKED_PATTERN.search(recent_output):
            return False, "Agent appears to be blocked or requesting help"

        self.last_check = datetime.now()
        return True, None

    def auto_recover(self) -> bool:
        """
        Attempt to recover from common issues.

        Returns:
            True if recovery was attempted, False otherwise
        """
        healthy, issue = self.check_health()

        if healthy:
            return False

        logger.warning(f"Attempting auto-recovery: {issue}")

        # Try sending Ctrl+C to break out of stuck state
        if self.agent.process and self.agent.process.isalive():
            try:
                self.agent.process.sendcontrol("c")
                time.sleep(1)

                # Try sending a simpler follow-up
                self.agent.process.sendline("Let's try a different approach")
                logger.info("Sent recovery commands to agent")
                return True

            except Exception as e:
                logger.error(f"Recovery failed: {e}")

        return False


__all__ = [
    "AutoAgent",
    "AutoAgentMonitor",
    "AutoAgentError",
    "AutoAgentTimeoutError",
    "AutoAgentPromptPatterns",
]

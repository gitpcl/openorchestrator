"""
tmux session management for Open Orchestrator.

This module handles tmux session creation, management, and integration
with git worktrees for parallel development workflows.
"""

import logging
import os
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum

import libtmux
from libtmux.constants import PaneDirection

from open_orchestrator.config import AITool, DroidAutoLevel
from open_orchestrator.core.theme import COLORS

logger = logging.getLogger(__name__)


class TmuxLayout(Enum):
    """Available tmux pane layouts."""

    SINGLE = "single"
    MAIN_VERTICAL = "main-vertical"


@dataclass
class TmuxSessionConfig:
    """Configuration for tmux session creation."""

    session_name: str
    working_directory: str
    layout: TmuxLayout = TmuxLayout.SINGLE
    pane_count: int = 1
    auto_start_ai: bool = True
    ai_tool: AITool = field(default=AITool.CLAUDE)
    droid_auto: DroidAutoLevel | None = None
    droid_skip_permissions: bool = False
    opencode_config: str | None = None
    plan_mode: bool = False
    auto_exit: bool = False
    prompt: str | None = None
    window_name: str | None = None
    mouse_mode: bool = True
    prefix_key: str | None = None


@dataclass
class TmuxSessionInfo:
    """Information about an existing tmux session."""

    session_name: str
    session_id: str
    window_count: int
    pane_count: int
    created_at: str
    attached: bool
    working_directory: str | None = None


class TmuxError(Exception):
    """Base exception for tmux operations."""

    pass


class TmuxSessionExistsError(TmuxError):
    """Raised when trying to create a session that already exists."""

    pass


class TmuxSessionNotFoundError(TmuxError):
    """Raised when a requested session doesn't exist."""

    pass


class TmuxServerNotRunningError(TmuxError):
    """Raised when tmux server is not running."""

    pass


class TmuxManager:
    """
    Manages tmux sessions for worktree-based development.

    Provides methods to create, attach, list, and manage tmux sessions
    with support for pane layouts optimized for development workflows.
    """

    SESSION_PREFIX = "owt"

    def __init__(self) -> None:
        """Initialize TmuxManager with libtmux server connection."""
        self._server: libtmux.Server | None = None

    def __enter__(self) -> "TmuxManager":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> None:
        self.close()

    def close(self) -> None:
        """Explicitly close the tmux server connection."""
        if self._server is not None:
            self._server = None

    def __del__(self) -> None:
        self.close()

    @property
    def server(self) -> libtmux.Server:
        """Get or create libtmux server instance."""
        if self._server is None:
            self._server = libtmux.Server()
        return self._server

    def generate_session_name(self, worktree_name: str) -> str:
        """Generate consistent session name from worktree name."""
        sanitized = worktree_name.replace("/", "-").replace(".", "-")
        return f"{self.SESSION_PREFIX}-{sanitized}"

    def session_exists(self, session_name: str) -> bool:
        """Check if a tmux session exists."""
        try:
            return self.server.has_session(session_name)
        except libtmux.exc.LibTmuxException:
            return False

    def create_session(self, config: TmuxSessionConfig) -> TmuxSessionInfo:
        """Create a new tmux session with specified configuration."""
        if self.session_exists(config.session_name):
            raise TmuxSessionExistsError(f"Session '{config.session_name}' already exists.")

        if not os.path.isdir(config.working_directory):
            raise TmuxError(f"Working directory does not exist: {config.working_directory}")

        try:
            window_name = config.window_name or "main"

            session = self.server.new_session(
                session_name=config.session_name, start_directory=config.working_directory, window_name=window_name, attach=False
            )

            window = session.active_window

            if config.layout == TmuxLayout.MAIN_VERTICAL and config.pane_count > 1:
                self._setup_main_vertical(window, config.pane_count, config.working_directory)

            if config.auto_start_ai:
                pane = window.active_pane
                if pane is None:
                    raise TmuxError("No active pane available to start AI tool")
                self._start_ai_tool_in_pane(
                    pane,
                    config.ai_tool,
                    droid_auto=config.droid_auto,
                    droid_skip_permissions=config.droid_skip_permissions,
                    opencode_config=config.opencode_config,
                    plan_mode=config.plan_mode,
                    auto_exit=config.auto_exit,
                    prompt=config.prompt,
                )

            if config.mouse_mode:
                session.set_option("mouse", "on")

            if config.prefix_key:
                session.set_option("prefix", config.prefix_key)

            if window:
                window.set_window_option("pane-border-status", "top")
                window.set_window_option("pane-border-format", " #{pane_title} ")
                if window.panes:
                    self._set_pane_title(window.panes[0], "main")

            # Install status bar
            self.install_status_bar(config.session_name)

            return self._get_session_info(session)

        except libtmux.exc.LibTmuxException as e:
            error_msg = str(e) if str(e) else "tmux server may not be running. Start it with: tmux new-session -d"
            raise TmuxError(f"Failed to create tmux session: {error_msg}") from e

    def _setup_main_vertical(self, window: libtmux.Window, pane_count: int, working_dir: str) -> None:
        """Create main-vertical layout: large left pane, smaller right panes."""
        for i in range(pane_count - 1):
            window.split(start_directory=working_dir, direction=PaneDirection.Right)
        window.select_layout("main-vertical")
        panes = window.panes
        if panes:
            panes[0].select()

    def _start_ai_tool_in_pane(
        self,
        pane: libtmux.Pane,
        ai_tool: AITool = AITool.CLAUDE,
        droid_auto: DroidAutoLevel | None = None,
        droid_skip_permissions: bool = False,
        opencode_config: str | None = None,
        plan_mode: bool = False,
        auto_exit: bool = False,
        prompt: str | None = None,
    ) -> None:
        """Start the specified AI tool in the pane.

        Args:
            auto_exit: If True, the shell exits after the AI tool exits,
                       causing the tmux pane (and session) to close.
                       Used by orchestrator/batch for reliable completion detection.
            prompt: If provided, run the AI tool in non-interactive mode with
                    this prompt. The tool will exit automatically when done.
        """
        if not AITool.is_installed(ai_tool):
            hint = AITool.get_install_hint(ai_tool)
            raise TmuxError(f"AI tool '{ai_tool.value}' is not installed. {hint}")

        executable = AITool.get_executable_path(ai_tool)
        command = AITool.get_command(
            ai_tool,
            executable_path=executable,
            droid_auto=droid_auto,
            droid_skip_permissions=droid_skip_permissions,
            opencode_config=opencode_config,
            plan_mode=plan_mode,
            prompt=prompt,
        )

        # For Claude with a prompt, write to a temp file and pipe via cat.
        # Using `cat file | claude -p` rather than `claude -p < file` because
        # pipe-based stdin is the confirmed working pattern (file redirects
        # fail silently inside tmux panes on some configurations).
        if prompt and ai_tool == AITool.CLAUDE:
            prompt_path = self._write_prompt_file(prompt)
            quoted_path = shlex.quote(prompt_path)
            if auto_exit:
                command = (
                    f"export OWT_AUTOMATED=1; "
                    f"cat {quoted_path} | {command}; "
                    f"rm -f {quoted_path}; exit"
                )
            else:
                command = f"cat {quoted_path} | {command}; rm -f {quoted_path}"
        elif auto_exit:
            # OWT_AUTOMATED lets user hooks distinguish automated agents
            # from interactive sessions (e.g. skip commit-blocking hooks).
            command = f"export OWT_AUTOMATED=1; {command}; exit"

        logger.debug("AI tool command for pane: %s", command)
        self._wait_for_shell_ready(pane)
        self._send_command_to_pane(pane, command)

    @staticmethod
    def _pane_target(pane: libtmux.Pane) -> str:
        """Build a ``session:window.pane`` target string for tmux commands."""
        return f"{pane.session.name}:{pane.window.index}.{pane.pane_index}"

    def _wait_for_shell_ready(
        self, pane: libtmux.Pane, timeout: float = 3.0,
    ) -> None:
        """Wait for the shell in a newly created pane to finish initializing.

        Uses ``tmux display-message -p -t <pane>`` to poll the pane's current
        command.  Without this, commands sent immediately after session creation
        can be lost because the shell hasn't started reading from the pty yet.
        """
        shells = {"bash", "zsh", "fish", "sh", "dash", "login"}
        target = self._pane_target(pane)
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            try:
                result = subprocess.run(
                    ["tmux", "display-message", "-p", "-t", target,
                     "#{pane_current_command}"],
                    capture_output=True, text=True, timeout=2,
                )
                cmd = result.stdout.strip()
                if cmd and cmd in shells:
                    logger.debug("Shell ready in %s (%s)", target, cmd)
                    return
            except (subprocess.TimeoutExpired, OSError):
                pass
            time.sleep(0.1)
        logger.warning(
            "Shell readiness timeout (%.1fs) for %s — sending anyway",
            timeout, target,
        )

    @classmethod
    def _send_command_to_pane(cls, pane: libtmux.Pane, command: str) -> None:
        """Send a command to a pane using set-buffer + paste-buffer.

        Uses ``-p`` (bracketed paste) so the shell doesn't auto-execute
        on any trailing newline in the buffer.  The explicit ``send-keys
        Enter`` is the single execution trigger.
        """
        target = cls._pane_target(pane)
        buf_name = "owt-init"
        subprocess.run(
            ["tmux", "set-buffer", "-b", buf_name, "--", command],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-b", buf_name, "-d", "-p", "-t", target],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Enter"],
            check=True, capture_output=True, text=True,
        )

    @staticmethod
    def _write_prompt_file(prompt: str) -> str:
        """Write a prompt to a temp file for stdin piping to AI tools.

        Returns the path to the temp file.  Caller is responsible for cleanup.
        """
        fd, path = tempfile.mkstemp(prefix="owt-prompt-", suffix=".md")
        with os.fdopen(fd, "w") as f:
            f.write(prompt)
        return path

    @staticmethod
    def _set_pane_title(pane: libtmux.Pane, title: str) -> None:
        """Set a pane's title via tmux command."""
        pane.cmd("select-pane", "-T", title)

    def _get_session_info(self, session: libtmux.Session) -> TmuxSessionInfo:
        """Extract session information from libtmux session object."""
        windows = session.windows
        total_panes = sum(len(w.panes) for w in windows)

        working_dir = None
        if windows and windows[0].panes:
            working_dir = windows[0].panes[0].pane_current_path

        return TmuxSessionInfo(
            session_name=str(session.name),
            session_id=str(session.id),
            window_count=len(windows),
            pane_count=total_panes,
            created_at=session.created.strftime("%Y-%m-%d %H:%M:%S") if hasattr(session, "created") else "unknown",
            attached=session.attached_count > 0 if hasattr(session, "attached_count") else False,
            working_directory=working_dir,
        )

    def attach(self, session_name: str) -> None:
        """Attach to an existing tmux session."""
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found.")
        subprocess.run(["tmux", "attach-session", "-t", session_name], check=True)

    def switch_client(self, session_name: str) -> None:
        """Switch current tmux client to another session."""
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found.")
        subprocess.run(["tmux", "switch-client", "-t", session_name], check=True)

    def list_sessions(self, filter_prefix: bool = True) -> list[TmuxSessionInfo]:
        """List all tmux sessions, optionally filtered by prefix."""
        try:
            sessions = self.server.sessions
        except libtmux.exc.LibTmuxException:
            return []

        result = []
        for session in sessions:
            name = session.name or ""
            if filter_prefix and not name.startswith(self.SESSION_PREFIX):
                continue
            result.append(self._get_session_info(session))

        return result

    def kill_session(self, session_name: str) -> None:
        """Kill a tmux session."""
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found.")

        try:
            session = self.server.sessions.filter(session_name=session_name)[0]
            session.kill()
        except (libtmux.exc.LibTmuxException, IndexError) as e:
            raise TmuxError(f"Failed to kill session '{session_name}': {e}") from e

    def create_worktree_session(
        self,
        worktree_name: str,
        worktree_path: str,
        layout: TmuxLayout = TmuxLayout.SINGLE,
        auto_start_ai: bool = True,
        ai_tool: AITool = AITool.CLAUDE,
        droid_auto: DroidAutoLevel | None = None,
        droid_skip_permissions: bool = False,
        opencode_config: str | None = None,
        plan_mode: bool = False,
        auto_exit: bool = False,
        prompt: str | None = None,
        mouse_mode: bool = True,
    ) -> TmuxSessionInfo:
        """Create a tmux session for a worktree."""
        session_name = self.generate_session_name(worktree_name)

        config = TmuxSessionConfig(
            session_name=session_name,
            working_directory=worktree_path,
            layout=layout,
            auto_start_ai=auto_start_ai,
            ai_tool=ai_tool,
            droid_auto=droid_auto,
            droid_skip_permissions=droid_skip_permissions,
            opencode_config=opencode_config,
            plan_mode=plan_mode,
            auto_exit=auto_exit,
            prompt=prompt,
            window_name=worktree_name,
            mouse_mode=mouse_mode,
        )

        return self.create_session(config)

    def get_session_for_worktree(self, worktree_name: str) -> TmuxSessionInfo | None:
        """Find existing tmux session for a worktree."""
        session_name = self.generate_session_name(worktree_name)
        if not self.session_exists(session_name):
            return None
        try:
            session = self.server.sessions.filter(session_name=session_name)[0]
            return self._get_session_info(session)
        except (libtmux.exc.LibTmuxException, IndexError):
            return None

    def is_inside_tmux(self) -> bool:
        """Check if currently running inside a tmux session."""
        return "TMUX" in os.environ

    def get_current_session_name(self) -> str | None:
        """Get the name of the current tmux session if inside tmux."""
        if not self.is_inside_tmux():
            return None
        try:
            result = subprocess.run(["tmux", "display-message", "-p", "#S"], capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def send_keys_to_pane(self, session_name: str, keys: str, pane_index: int = 0, window_index: int = 0) -> None:
        """Send keys to a specific pane in a session.

        Uses tmux set-buffer + paste-buffer (-p for bracketed paste) for
        reliable delivery without double-execution from trailing newlines.
        """
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found.")
        target = f"{session_name}:{window_index}.{pane_index}"
        try:
            buf_name = "owt-send"
            subprocess.run(
                ["tmux", "set-buffer", "-b", buf_name, "--", keys],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-b", buf_name, "-d", "-p", "-t", target],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Enter"],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            raise TmuxError(f"Failed to send keys to pane: {e}") from e

    def is_ai_running_in_session(self, session_name: str) -> bool:
        """Check if an AI tool process is still running in the session's pane.

        Returns False if the session is gone or the pane has fallen back to a
        shell (meaning the AI tool exited).  Used by the orchestrator as a
        fallback completion signal when hooks fail to fire.
        """
        if not self.session_exists(session_name):
            return False
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-t", session_name,
                 "-F", "#{pane_current_command}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return False
            shells = {"bash", "zsh", "fish", "sh", "dash", "login"}
            commands = [c.strip() for c in result.stdout.strip().split("\n") if c.strip()]
            return bool(commands) and not all(c in shells for c in commands)
        except (subprocess.TimeoutExpired, OSError):
            return False

    def get_pane_count(self, session_name: str) -> int:
        """Get the number of panes in a session."""
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found")
        try:
            session = self.server.sessions.filter(session_name=session_name)[0]
            window = session.active_window
            return len(window.panes)
        except (libtmux.exc.LibTmuxException, IndexError) as e:
            raise TmuxError(f"Failed to get pane count: {e}") from e

    @staticmethod
    def get_tmux_version() -> tuple[int, int]:
        """Get the installed tmux version as (major, minor) tuple."""
        try:
            result = subprocess.run(
                ["tmux", "-V"], capture_output=True, text=True, check=True
            )
            version_str = result.stdout.strip().split()[-1].removeprefix("next-")
            match = re.match(r"(\d+)\.(\d+)", version_str)
            if match:
                return (int(match.group(1)), int(match.group(2)))
            match = re.match(r"(\d+)", version_str)
            if match:
                return (int(match.group(1)), 0)
            return (0, 0)
        except (subprocess.CalledProcessError, ValueError, IndexError):
            return (0, 0)

    @staticmethod
    def _run_tmux_cmd(*args: str) -> bool:
        """Run a tmux command, return True on success."""
        result = subprocess.run(
            ["tmux", *args], check=False, capture_output=True, text=True,
        )
        return result.returncode == 0

    @staticmethod
    def _run_tmux_batch(*commands: tuple[str, ...]) -> bool:
        """Run multiple tmux commands in a single subprocess."""
        if not commands:
            return True
        cmd: list[str] = ["tmux"]
        for i, args in enumerate(commands):
            if i > 0:
                cmd.append(";")
            cmd.extend(args)
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        return result.returncode == 0

    def install_status_bar(self, session_name: str) -> None:
        """Configure tmux status bar with OWT branding and pane borders."""
        border_inactive = COLORS["border_inactive"]
        bg = COLORS["background"]
        text = "#888888"
        border_fmt = (
            f"#{{?pane_active,#[fg=white bold],#[fg={border_inactive}]}}"
            f" #{{pane_title}} "
        )
        self._run_tmux_batch(
            ("set-option", "-t", session_name,
             "status-right", "[owt] %H:%M"),
            ("set-option", "-t", session_name, "status-interval", "5"),
            ("set-option", "-t", session_name, "status-right-length", "40"),
            ("set-option", "-t", session_name,
             "status-style", f"bg={bg},fg={text}"),
            ("set-option", "-t", session_name,
             "pane-border-style", f"fg={border_inactive}"),
            ("set-option", "-t", session_name,
             "pane-active-border-style", "fg=white"),
            ("set-option", "-t", session_name,
             "pane-border-indicators", "arrows"),
            ("set-option", "-t", session_name,
             "pane-border-lines", "heavy"),
            ("set-option", "-t", session_name,
             "pane-border-format", border_fmt),
        )


"""
tmux session management for Claude Orchestrator.

This module handles tmux session creation, management, and integration
with git worktrees for parallel development workflows.
"""

import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import libtmux
from libtmux.constants import PaneDirection


class TmuxLayout(Enum):
    """Available tmux pane layouts."""

    MAIN_VERTICAL = "main-vertical"
    THREE_PANE = "three-pane"
    QUAD = "quad"
    EVEN_HORIZONTAL = "even-horizontal"
    EVEN_VERTICAL = "even-vertical"


@dataclass
class TmuxSessionConfig:
    """Configuration for tmux session creation."""

    session_name: str
    working_directory: str
    layout: TmuxLayout = TmuxLayout.MAIN_VERTICAL
    pane_count: int = 2
    auto_start_claude: bool = True
    window_name: Optional[str] = None


@dataclass
class TmuxSessionInfo:
    """Information about an existing tmux session."""

    session_name: str
    session_id: str
    window_count: int
    pane_count: int
    created_at: str
    attached: bool
    working_directory: Optional[str] = None


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
    with support for various pane layouts optimized for development workflows.
    """

    SESSION_PREFIX = "cwt"

    def __init__(self):
        """Initialize TmuxManager with libtmux server connection."""
        self._server: Optional[libtmux.Server] = None

    @property
    def server(self) -> libtmux.Server:
        """Get or create libtmux server instance."""
        if self._server is None:
            self._server = libtmux.Server()
        return self._server

    def _generate_session_name(self, worktree_name: str) -> str:
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
        """
        Create a new tmux session with specified configuration.

        Args:
            config: TmuxSessionConfig with session parameters

        Returns:
            TmuxSessionInfo with created session details

        Raises:
            TmuxSessionExistsError: If session already exists
            TmuxError: If session creation fails
        """
        if self.session_exists(config.session_name):
            raise TmuxSessionExistsError(
                f"Session '{config.session_name}' already exists. "
                f"Use 'cwt tmux attach {config.session_name}' to attach."
            )

        if not os.path.isdir(config.working_directory):
            raise TmuxError(
                f"Working directory does not exist: {config.working_directory}"
            )

        try:
            window_name = config.window_name or "main"

            session = self.server.new_session(
                session_name=config.session_name,
                start_directory=config.working_directory,
                window_name=window_name,
                attach=False
            )

            window = session.active_window

            self._setup_layout(window, config)

            if config.auto_start_claude:
                self._start_claude_in_pane(window.active_pane)

            return self._get_session_info(session)

        except libtmux.exc.LibTmuxException as e:
            error_msg = str(e) if str(e) else "tmux server may not be running. Start it with: tmux new-session -d"
            raise TmuxError(f"Failed to create tmux session: {error_msg}") from e

    def _setup_layout(self, window: libtmux.Window, config: TmuxSessionConfig) -> None:
        """Set up pane layout for the window."""
        layout = config.layout
        pane_count = config.pane_count
        working_dir = config.working_directory

        if layout == TmuxLayout.MAIN_VERTICAL:
            self._setup_main_vertical(window, pane_count, working_dir)
        elif layout == TmuxLayout.THREE_PANE:
            self._setup_three_pane(window, working_dir)
        elif layout == TmuxLayout.QUAD:
            self._setup_quad(window, working_dir)
        elif layout == TmuxLayout.EVEN_HORIZONTAL:
            self._setup_even_horizontal(window, pane_count, working_dir)
        elif layout == TmuxLayout.EVEN_VERTICAL:
            self._setup_even_vertical(window, pane_count, working_dir)

    def _setup_main_vertical(
        self,
        window: libtmux.Window,
        pane_count: int,
        working_dir: str
    ) -> None:
        """
        Create main-vertical layout: large left pane, smaller right panes.

        Layout (pane_count=2):
        ┌────────────┬─────────┐
        │            │         │
        │   Main     │  Side   │
        │   (60%)    │  (40%)  │
        │            │         │
        └────────────┴─────────┘
        """
        for i in range(pane_count - 1):
            window.split(
                start_directory=working_dir,
                direction=PaneDirection.Right
            )

        window.select_layout("main-vertical")

        panes = window.panes
        if panes:
            panes[0].select()

    def _setup_three_pane(self, window: libtmux.Window, working_dir: str) -> None:
        """
        Create three-pane layout: main top, two bottom.

        Layout:
        ┌─────────────────────────┐
        │                         │
        │        Main (60%)       │
        │                         │
        ├────────────┬────────────┤
        │  Bottom L  │  Bottom R  │
        │   (40%)    │   (40%)    │
        └────────────┴────────────┘
        """
        window.split(start_directory=working_dir, direction=PaneDirection.Below)

        bottom_pane = window.panes[-1]
        bottom_pane.split(start_directory=working_dir, direction=PaneDirection.Right)

        window.panes[0].select()

    def _setup_quad(self, window: libtmux.Window, working_dir: str) -> None:
        """
        Create quad layout: four equal panes.

        Layout:
        ┌────────────┬────────────┐
        │            │            │
        │   Top L    │   Top R    │
        │            │            │
        ├────────────┼────────────┤
        │            │            │
        │  Bottom L  │  Bottom R  │
        │            │            │
        └────────────┴────────────┘
        """
        window.split(start_directory=working_dir, direction=PaneDirection.Right)

        window.panes[0].split(start_directory=working_dir, direction=PaneDirection.Below)

        window.panes[2].split(start_directory=working_dir, direction=PaneDirection.Below)

        window.select_layout("tiled")

        window.panes[0].select()

    def _setup_even_horizontal(
        self,
        window: libtmux.Window,
        pane_count: int,
        working_dir: str
    ) -> None:
        """Create horizontally split equal panes."""
        for _ in range(pane_count - 1):
            window.split(start_directory=working_dir, direction=PaneDirection.Right)

        window.select_layout("even-horizontal")
        window.panes[0].select()

    def _setup_even_vertical(
        self,
        window: libtmux.Window,
        pane_count: int,
        working_dir: str
    ) -> None:
        """Create vertically split equal panes."""
        for _ in range(pane_count - 1):
            window.split(start_directory=working_dir, direction=PaneDirection.Below)

        window.select_layout("even-vertical")
        window.panes[0].select()

    def _start_claude_in_pane(self, pane: libtmux.Pane) -> None:
        """Start Claude Code in the specified pane."""
        pane.send_keys("claude", enter=True)

    def _get_session_info(self, session: libtmux.Session) -> TmuxSessionInfo:
        """Extract session information from libtmux session object."""
        windows = session.windows
        total_panes = sum(len(w.panes) for w in windows)

        working_dir = None
        if windows and windows[0].panes:
            working_dir = windows[0].panes[0].pane_current_path

        return TmuxSessionInfo(
            session_name=session.name,
            session_id=session.id,
            window_count=len(windows),
            pane_count=total_panes,
            created_at=session.created.strftime("%Y-%m-%d %H:%M:%S") if hasattr(session, 'created') else "unknown",
            attached=session.attached_count > 0 if hasattr(session, 'attached_count') else False,
            working_directory=working_dir
        )

    def attach(self, session_name: str) -> None:
        """
        Attach to an existing tmux session.

        Args:
            session_name: Name of the session to attach to

        Raises:
            TmuxSessionNotFoundError: If session doesn't exist
        """
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(
                f"Session '{session_name}' not found. "
                f"Use 'cwt tmux list' to see available sessions."
            )

        subprocess.run(["tmux", "attach-session", "-t", session_name], check=True)

    def switch_client(self, session_name: str) -> None:
        """
        Switch current tmux client to another session.

        Use this when already inside tmux to switch sessions without detaching.

        Args:
            session_name: Name of the session to switch to

        Raises:
            TmuxSessionNotFoundError: If session doesn't exist
        """
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(
                f"Session '{session_name}' not found."
            )

        subprocess.run(["tmux", "switch-client", "-t", session_name], check=True)

    def list_sessions(self, filter_prefix: bool = True) -> list[TmuxSessionInfo]:
        """
        List all tmux sessions.

        Args:
            filter_prefix: If True, only return sessions with SESSION_PREFIX

        Returns:
            List of TmuxSessionInfo objects
        """
        try:
            sessions = self.server.sessions
        except libtmux.exc.LibTmuxException:
            return []

        result = []
        for session in sessions:
            if filter_prefix and not session.name.startswith(self.SESSION_PREFIX):
                continue

            result.append(self._get_session_info(session))

        return result

    def kill_session(self, session_name: str) -> None:
        """
        Kill a tmux session.

        Args:
            session_name: Name of the session to kill

        Raises:
            TmuxSessionNotFoundError: If session doesn't exist
        """
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(
                f"Session '{session_name}' not found."
            )

        try:
            session = self.server.sessions.filter(session_name=session_name)[0]
            session.kill()
        except (libtmux.exc.LibTmuxException, IndexError) as e:
            raise TmuxError(f"Failed to kill session '{session_name}': {e}") from e

    def create_worktree_session(
        self,
        worktree_name: str,
        worktree_path: str,
        layout: TmuxLayout = TmuxLayout.MAIN_VERTICAL,
        pane_count: int = 2,
        auto_start_claude: bool = True
    ) -> TmuxSessionInfo:
        """
        Create a tmux session for a worktree.

        Convenience method that generates an appropriate session name
        and creates a session configured for worktree development.

        Args:
            worktree_name: Name of the worktree (used to generate session name)
            worktree_path: Path to the worktree directory
            layout: Pane layout to use
            pane_count: Number of panes (for layouts that support variable counts)
            auto_start_claude: Whether to start Claude Code automatically

        Returns:
            TmuxSessionInfo with created session details
        """
        session_name = self._generate_session_name(worktree_name)

        config = TmuxSessionConfig(
            session_name=session_name,
            working_directory=worktree_path,
            layout=layout,
            pane_count=pane_count,
            auto_start_claude=auto_start_claude,
            window_name=worktree_name
        )

        return self.create_session(config)

    def get_session_for_worktree(self, worktree_name: str) -> Optional[TmuxSessionInfo]:
        """
        Find existing tmux session for a worktree.

        Args:
            worktree_name: Name of the worktree

        Returns:
            TmuxSessionInfo if found, None otherwise
        """
        session_name = self._generate_session_name(worktree_name)

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

    def get_current_session_name(self) -> Optional[str]:
        """Get the name of the current tmux session if inside tmux."""
        if not self.is_inside_tmux():
            return None

        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#S"],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def send_keys_to_pane(
        self,
        session_name: str,
        keys: str,
        pane_index: int = 0,
        window_index: int = 0
    ) -> None:
        """
        Send keys to a specific pane in a session.

        Args:
            session_name: Name of the target session
            keys: Keys to send
            pane_index: Index of the pane within the window
            window_index: Index of the window within the session
        """
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found.")

        try:
            session = self.server.sessions.filter(session_name=session_name)[0]
            window = session.windows[window_index]
            pane = window.panes[pane_index]
            pane.send_keys(keys, enter=True)
        except (libtmux.exc.LibTmuxException, IndexError) as e:
            raise TmuxError(f"Failed to send keys to pane: {e}") from e

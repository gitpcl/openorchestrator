"""
tmux session management for Open Orchestrator.

This module handles tmux session creation, management, and integration
with git worktrees for parallel development workflows.
"""

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from enum import Enum

import libtmux
from libtmux.constants import PaneDirection

from open_orchestrator.config import AITool, DroidAutoLevel


class TmuxLayout(Enum):
    """Available tmux pane layouts."""

    SINGLE = "single"  # Single pane, on-demand workspace mode (dmux-like)
    MAIN_VERTICAL = "main-vertical"
    MAIN_FOCUS = "main-focus"  # 1/3 left + 3 horizontal right (workspace default)
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
    auto_start_ai: bool = True
    ai_tool: AITool = field(default=AITool.CLAUDE)
    # Tool-specific options
    droid_auto: DroidAutoLevel | None = None
    droid_skip_permissions: bool = False
    opencode_config: str | None = None
    plan_mode: bool = False
    window_name: str | None = None
    mouse_mode: bool = True
    prefix_key: str | None = None  # e.g. "C-z" to override default Ctrl+b


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
    with support for various pane layouts optimized for development workflows.
    """

    SESSION_PREFIX = "owt"
    SIDEBAR_WIDTH = 40

    def __init__(self) -> None:
        """Initialize TmuxManager with libtmux server connection."""
        self._server: libtmux.Server | None = None

    def __enter__(self) -> "TmuxManager":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Context manager exit - clean up resources."""
        self.close()
        return False

    def close(self) -> None:
        """Explicitly close the tmux server connection."""
        if self._server is not None:
            # libtmux doesn't have explicit close, but we can clear the reference
            self._server = None

    def __del__(self) -> None:
        """Cleanup on garbage collection."""
        self.close()

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
                f"Session '{config.session_name}' already exists. Use 'owt tmux attach {config.session_name}' to attach."
            )

        if not os.path.isdir(config.working_directory):
            raise TmuxError(f"Working directory does not exist: {config.working_directory}")

        try:
            window_name = config.window_name or "main"

            session = self.server.new_session(
                session_name=config.session_name, start_directory=config.working_directory, window_name=window_name, attach=False
            )

            window = session.active_window

            self._setup_layout(window, config)

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
                )

            # Enable mouse mode if configured
            if config.mouse_mode:
                session.set_option("mouse", "on")

            # Override prefix key if configured (e.g. "C-z" for Ctrl+z)
            # Uses session-scoped set_option so only this session is affected
            if config.prefix_key:
                session.set_option("prefix", config.prefix_key)

            # Enable pane border status to show worktree names
            if window:
                window.set_window_option("pane-border-status", "top")
                window.set_window_option("pane-border-format", " #{pane_title} ")

                # Set title for main pane
                if window.panes:
                    main_pane = window.panes[0]
                    self._set_pane_title(main_pane, "main")

            return self._get_session_info(session)

        except libtmux.exc.LibTmuxException as e:
            error_msg = str(e) if str(e) else "tmux server may not be running. Start it with: tmux new-session -d"
            raise TmuxError(f"Failed to create tmux session: {error_msg}") from e

    def _setup_layout(self, window: libtmux.Window, config: TmuxSessionConfig) -> None:
        """Set up pane layout for the window."""
        layout = config.layout
        pane_count = config.pane_count
        working_dir = config.working_directory

        if layout == TmuxLayout.SINGLE:
            pass  # Session starts with 1 pane by default — no setup needed
        elif layout == TmuxLayout.MAIN_VERTICAL:
            self._setup_main_vertical(window, pane_count, working_dir)
        elif layout == TmuxLayout.MAIN_FOCUS:
            self._setup_main_focus(window, working_dir)
        elif layout == TmuxLayout.THREE_PANE:
            self._setup_three_pane(window, working_dir)
        elif layout == TmuxLayout.QUAD:
            self._setup_quad(window, working_dir)
        elif layout == TmuxLayout.EVEN_HORIZONTAL:
            self._setup_even_horizontal(window, pane_count, working_dir)
        elif layout == TmuxLayout.EVEN_VERTICAL:
            self._setup_even_vertical(window, pane_count, working_dir)

    def _setup_main_vertical(self, window: libtmux.Window, pane_count: int, working_dir: str) -> None:
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
            window.split(start_directory=working_dir, direction=PaneDirection.Right)

        window.select_layout("main-vertical")

        panes = window.panes
        if panes:
            panes[0].select()

    def _setup_main_focus(self, window: libtmux.Window, working_dir: str) -> None:
        """
        Create main-focus layout: 1/3 left main + 3 horizontal right (workspace default).

        Layout:
        ┌──────────┬─────────────────────┐
        │          │   Worktree 1        │
        │          ├─────────────────────┤
        │   Main   │   Worktree 2        │
        │  (33%)   ├─────────────────────┤
        │          │   Worktree 3        │
        └──────────┴─────────────────────┘
        """
        # Create right pane (2/3 width)
        window.split(start_directory=working_dir, direction=PaneDirection.Right)

        # Resize main (left) pane to 33%
        panes = window.panes
        if len(panes) >= 2:
            # Select right pane and resize to 67% of total width
            panes[1].select()
            panes[1].resize(width="67%")

            # Split right side into 3 horizontal panes
            # First split creates 2 panes
            window.split(start_directory=working_dir, direction=PaneDirection.Below)

            # Second split creates 3 panes total on right
            panes = window.panes
            if len(panes) >= 2:
                # Select middle-right pane and split it
                panes[1].select()
                window.split(start_directory=working_dir, direction=PaneDirection.Below)

        # Select main (left) pane
        window.panes[0].select()

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

    def _setup_even_horizontal(self, window: libtmux.Window, pane_count: int, working_dir: str) -> None:
        """Create horizontally split equal panes."""
        for _ in range(pane_count - 1):
            window.split(start_directory=working_dir, direction=PaneDirection.Right)

        window.select_layout("even-horizontal")
        window.panes[0].select()

    def _setup_even_vertical(self, window: libtmux.Window, pane_count: int, working_dir: str) -> None:
        """Create vertically split equal panes."""
        for _ in range(pane_count - 1):
            window.split(start_directory=working_dir, direction=PaneDirection.Below)

        window.select_layout("even-vertical")
        window.panes[0].select()

    def _start_ai_tool_in_pane(
        self,
        pane: libtmux.Pane,
        ai_tool: AITool = AITool.CLAUDE,
        droid_auto: DroidAutoLevel | None = None,
        droid_skip_permissions: bool = False,
        opencode_config: str | None = None,
        plan_mode: bool = False,
    ) -> None:
        """
        Start the specified AI tool in the pane.

        Args:
            pane: The tmux pane to start the AI tool in
            ai_tool: Which AI tool to start
            droid_auto: Droid auto mode level (if using droid)
            droid_skip_permissions: Skip permissions for droid
            opencode_config: Custom config path for opencode
            plan_mode: Start Claude in plan mode

        Raises:
            TmuxError: If AI tool is not installed
        """
        if not AITool.is_installed(ai_tool):
            hint = AITool.get_install_hint(ai_tool)
            raise TmuxError(f"AI tool '{ai_tool.value}' is not installed. {hint}")

        # Get executable path (may be full path if not in PATH)
        executable = AITool.get_executable_path(ai_tool)

        command = AITool.get_command(
            ai_tool,
            executable_path=executable,
            droid_auto=droid_auto,
            droid_skip_permissions=droid_skip_permissions,
            opencode_config=opencode_config,
            plan_mode=plan_mode,
        )
        pane.send_keys(command, enter=True)

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
        """
        Attach to an existing tmux session.

        Args:
            session_name: Name of the session to attach to

        Raises:
            TmuxSessionNotFoundError: If session doesn't exist
        """
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found. Use 'owt tmux list' to see available sessions.")

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
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found.")

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
            name = session.name or ""
            if filter_prefix and not name.startswith(self.SESSION_PREFIX):
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
        layout: TmuxLayout = TmuxLayout.MAIN_VERTICAL,
        pane_count: int = 2,
        auto_start_ai: bool = True,
        ai_tool: AITool = AITool.CLAUDE,
        droid_auto: DroidAutoLevel | None = None,
        droid_skip_permissions: bool = False,
        opencode_config: str | None = None,
        plan_mode: bool = False,
        mouse_mode: bool = True,
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
            auto_start_ai: Whether to start AI tool automatically
            ai_tool: Which AI tool to start (default: claude)
            droid_auto: Droid auto mode level
            droid_skip_permissions: Skip droid permissions check
            opencode_config: OpenCode config path
            plan_mode: Start Claude in plan mode
            mouse_mode: Enable mouse support (click to switch panes, drag to resize)

        Returns:
            TmuxSessionInfo with created session details
        """
        session_name = self._generate_session_name(worktree_name)

        config = TmuxSessionConfig(
            session_name=session_name,
            working_directory=worktree_path,
            layout=layout,
            pane_count=pane_count,
            auto_start_ai=auto_start_ai,
            ai_tool=ai_tool,
            droid_auto=droid_auto,
            droid_skip_permissions=droid_skip_permissions,
            opencode_config=opencode_config,
            plan_mode=plan_mode,
            window_name=worktree_name,
            mouse_mode=mouse_mode,
        )

        return self.create_session(config)

    def create_tui_session(
        self,
        workspace_name: str,
        repo_path: str,
    ) -> TmuxSessionInfo:
        """Create a tmux session with owt TUI in pane 0 (dmux-style).

        The TUI sidebar runs in the first pane, agent panes are added alongside
        it. Mouse mode is disabled so Textual handles mouse events.

        Args:
            workspace_name: Name for the workspace/session.
            repo_path: Path to the main repository.

        Returns:
            TmuxSessionInfo with created session details.

        Raises:
            TmuxSessionExistsError: If session already exists.
            TmuxError: If session creation fails.
        """
        config = TmuxSessionConfig(
            session_name=workspace_name,
            working_directory=repo_path,
            layout=TmuxLayout.SINGLE,
            pane_count=1,
            auto_start_ai=False,
            window_name="owt",
            mouse_mode=True,  # Enable mouse so users can click between panes
        )

        session_info = self.create_session(config)

        # Set environment variables for workspace discovery
        self._run_tmux_cmd("set-environment", "-t", workspace_name, "OWT_WORKSPACE", workspace_name)
        self._run_tmux_cmd("set-environment", "-t", workspace_name, "OWT_REPO", repo_path)

        # Start owt tui in pane 0
        try:
            session = self.server.sessions.filter(session_name=workspace_name)[0]
            pane = session.active_window.active_pane
            if pane:
                pane.send_keys(
                    f"OWT_WORKSPACE={shlex.quote(workspace_name)} "
                    f"OWT_REPO={shlex.quote(repo_path)} "
                    f"owt tui",
                    enter=True,
                )
        except libtmux.exc.LibTmuxException:
            pass

        # Install status bar
        self.install_status_bar(workspace_name)

        return session_info

    def get_session_for_worktree(self, worktree_name: str) -> TmuxSessionInfo | None:
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

    def add_worktree_pane(
        self,
        session_name: str,
        worktree_path: str,
        worktree_name: str | None = None,
        ai_tool: AITool = AITool.CLAUDE,
        plan_mode: bool = False,
        droid_auto: DroidAutoLevel | None = None,
        droid_skip_permissions: bool = False,
        opencode_config: str | None = None,
    ) -> int:
        """
        Add a new worktree pane to an existing workspace session.

        Creates a new pane in the rightmost column of the main-focus layout.

        Args:
            session_name: Name of the workspace session
            worktree_path: Path to the worktree
            worktree_name: Name of the worktree (for pane title)
            ai_tool: AI tool to start in the pane
            plan_mode: Start Claude in plan mode
            droid_auto: Droid auto mode level
            droid_skip_permissions: Skip droid permissions
            opencode_config: Custom config for OpenCode

        Returns:
            Index of the newly created pane

        Raises:
            TmuxSessionNotFoundError: If session doesn't exist
            TmuxError: If pane creation fails
        """
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found")

        if not os.path.isdir(worktree_path):
            raise TmuxError(f"Worktree directory does not exist: {worktree_path}")

        try:
            session = self.server.sessions.filter(session_name=session_name)[0]
            window = session.active_window

            # Grid layout: agent panes fill columns of 2.
            # - Odd agent count (1st, 3rd, ...): split right → new column
            # - Even agent count (2nd, 4th, ...): split below → halve the column
            agent_panes = window.panes[1:]  # exclude sidebar (pane 0)
            agent_count = len(agent_panes)

            if agent_count > 0 and agent_count % 2 == 1:
                # Odd number of agents → split the last agent below (halve its column)
                target_pane = agent_panes[-1]
                target_pane.select()
                new_pane = window.split(
                    start_directory=worktree_path,
                    direction=PaneDirection.Below,
                )
            else:
                # Zero or even agents → split right of the last pane (new column)
                window.panes[-1].select()
                new_pane = window.split(
                    start_directory=worktree_path,
                    direction=PaneDirection.Right,
                )

            # Apply a balanced grid layout (sidebar + agent columns)
            if new_pane:
                self._apply_grid_layout(window)

            # Start AI tool in the new pane
            if new_pane:
                # Set pane title to worktree name
                if worktree_name:
                    self._set_pane_title(new_pane, worktree_name)

                self._start_ai_tool_in_pane(
                    new_pane,
                    ai_tool=ai_tool,
                    plan_mode=plan_mode,
                    droid_auto=droid_auto,
                    droid_skip_permissions=droid_skip_permissions,
                    opencode_config=opencode_config,
                )

            # Return the new pane's index
            return int(new_pane.pane_index) if new_pane else -1

        except libtmux.exc.LibTmuxException as e:
            raise TmuxError(f"Failed to add worktree pane: {e}") from e

    def remove_pane(self, session_name: str, pane_index: int) -> None:
        """
        Remove a pane from a workspace session.

        Args:
            session_name: Name of the workspace session
            pane_index: Index of the pane to remove

        Raises:
            TmuxSessionNotFoundError: If session doesn't exist
            TmuxError: If pane removal fails or trying to remove main pane
        """
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found")

        try:
            session = self.server.sessions.filter(session_name=session_name)[0]
            window = session.active_window

            # Don't allow removing pane 0 (main pane)
            if pane_index == 0:
                raise TmuxError("Cannot remove main pane (pane 0) from workspace")

            # Find and kill the pane
            for pane in window.panes:
                if int(pane.pane_index) == pane_index:
                    pane.kill()
                    # Rebalance remaining panes
                    self._apply_grid_layout(window)
                    return

            raise TmuxError(f"Pane {pane_index} not found in session '{session_name}'")

        except libtmux.exc.LibTmuxException as e:
            raise TmuxError(f"Failed to remove pane: {e}") from e

    def get_pane_count(self, session_name: str) -> int:
        """
        Get the number of panes in a workspace session.

        Args:
            session_name: Name of the workspace session

        Returns:
            Number of panes

        Raises:
            TmuxSessionNotFoundError: If session doesn't exist
        """
        if not self.session_exists(session_name):
            raise TmuxSessionNotFoundError(f"Session '{session_name}' not found")

        try:
            session = self.server.sessions.filter(session_name=session_name)[0]
            window = session.active_window
            return len(window.panes)
        except libtmux.exc.LibTmuxException as e:
            raise TmuxError(f"Failed to get pane count: {e}") from e

    @staticmethod
    def _apply_grid_layout(window: libtmux.Window) -> None:
        """Apply sidebar + equal-width agent columns layout.

        Agent panes are arranged in a grid: columns of 2 panes each.
        The sidebar stays at a fixed width on the left.

        Layout with 4 agents:
        ┌──────────┬──────────┬──────────┐
        │          │ agent 1  │ agent 3  │
        │ sidebar  ├──────────┼──────────┤
        │          │ agent 2  │ agent 4  │
        └──────────┴──────────┴──────────┘
        """
        try:
            panes = window.panes
            agent_count = len(panes) - 1
        except (TypeError, libtmux.exc.LibTmuxException):
            return

        if agent_count <= 0:
            return

        import math
        num_columns = math.ceil(agent_count / 2)

        # Use tiled layout as a starting point, then fix sidebar width.
        # tiled handles arbitrary pane counts better than even-horizontal
        # for mixed horizontal/vertical splits.
        session_name = window.session.name or ""
        TmuxManager._run_tmux_cmd(
            "select-layout", "-t", session_name, "tiled",
        )

        # Shrink sidebar to fixed width — tmux redistributes the rest
        sidebar_pane_id = panes[0].pane_id
        TmuxManager._run_tmux_cmd(
            "resize-pane", "-t", sidebar_pane_id, "-x",
            str(TmuxManager.SIDEBAR_WIDTH),
        )

    @staticmethod
    def get_tmux_version() -> tuple[int, int]:
        """Get the installed tmux version as (major, minor) tuple."""
        try:
            result = subprocess.run(
                ["tmux", "-V"], capture_output=True, text=True, check=True
            )
            # Output like "tmux 3.4", "tmux 3.6a", or "tmux next-3.5"
            version_str = result.stdout.strip().split()[-1].lstrip("next-")
            # Strip non-numeric suffixes (e.g. "3.6a" → "3.6")
            match = re.match(r"(\d+)\.(\d+)", version_str)
            if match:
                return (int(match.group(1)), int(match.group(2)))
            # Fallback: try just major version
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
        """Run multiple tmux commands in a single subprocess via \\; chaining."""
        if not commands:
            return True
        cmd: list[str] = ["tmux"]
        for i, args in enumerate(commands):
            if i > 0:
                cmd.append(";")
            cmd.extend(args)
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        return result.returncode == 0

    def install_keybindings(
        self,
        session_name: str,
        workspace_name: str,
        repo_path: str,
    ) -> None:
        """
        Install tmux keybindings for on-demand pane creation in a workspace session.

        Binds (prefix+key):
        - n → tmux display-popup running owt-popup picker
        - X → confirm-before close pane + cleanup worktree

        Requires tmux >= 3.2 for display-popup support.

        Args:
            session_name: tmux session to bind keys in
            workspace_name: workspace name for the owt pane add command
            repo_path: repository path for worktree creation
        """
        major, minor = self.get_tmux_version()
        if (major, minor) < (3, 2):
            raise TmuxError(
                f"tmux >= 3.2 required for display-popup (found {major}.{minor}). "
                f"Upgrade tmux or use 'owt pane add' directly."
            )

        # Temp file path unique to this session
        result_file = f"/tmp/owt-popup-{session_name}.json"
        log_file = f"/tmp/owt-popup-{session_name}.log"

        # Quote paths that may contain spaces
        q_result = shlex.quote(result_file)
        q_workspace = shlex.quote(workspace_name)
        q_repo = shlex.quote(repo_path)
        q_log = shlex.quote(log_file)

        # prefix+n: open popup picker, then run owt pane add with the result
        # Log errors so failed popups can be debugged
        popup_shell_cmd = (
            f"owt-popup {q_result} 2>{q_log} && "
            f"owt pane add --from-popup {q_result} "
            f"--workspace {q_workspace} --repo {q_repo} 2>>{q_log}"
        )

        # Bind prefix+n → popup picker
        if not self._run_tmux_cmd(
            "bind-key", "-T", "prefix", "n",
            "display-popup", "-E", "-w", "60", "-h", "20",
            popup_shell_cmd,
        ):
            raise TmuxError("Failed to bind prefix+n")

        # Bind prefix+X → close pane + cleanup worktree
        close_cmd = (
            f"owt pane remove --pane-id '#{{pane_id}}' --workspace {q_workspace}"
        )
        self._run_tmux_cmd(
            "bind-key", "-T", "prefix", "X",
            "confirm-before", "-p", "Close pane and delete worktree? (y/n)",
            f"run-shell {shlex.quote(close_cmd)} ; kill-pane",
        )

        # Set environment variables on the session for discovery
        self._run_tmux_cmd("set-environment", "-t", session_name, "OWT_WORKSPACE", workspace_name)
        self._run_tmux_cmd("set-environment", "-t", session_name, "OWT_REPO", repo_path)

        # Install status bar showing worktree status
        self.install_status_bar(session_name)

    def install_status_bar(self, session_name: str) -> None:
        """Configure tmux status bar to show worktree activity summary.

        Reads from ~/.open-orchestrator/ai_status.json and displays a compact
        summary like: [owt] feat/auth:working | feat/api:idle  (2 active)

        Args:
            session_name: tmux session to configure.
        """
        from pathlib import Path

        status_file = Path.home() / ".open-orchestrator" / "ai_status.json"

        # Shell script that reads status JSON and formats it for the status bar.
        # Uses python for reliable JSON parsing (available since we're a python tool).
        status_script = (
            f"python3 -c \""
            f"import json, sys; "
            f"f='{status_file}'; "
            f"d=json.load(open(f)) if __import__('os').path.exists(f) else {{}}; "
            f"ss=d.get('statuses',{{}}); "
            f"parts=[]; "
            f"[parts.append(v.get('branch','?').split('/')[-1]+':'+v.get('activity_status','?')) for v in ss.values() if v.get('activity_status','idle')!='idle']; "
            f"active=len(parts); "
            f"out=' | '.join(parts[:3]); "
            f"print(f'[owt] {{out}}  ({{active}} active)' if active else '[owt] idle')"
            f"\""
        )

        # Resolve theme accent for status bar styling
        from open_orchestrator.config import get_active_theme

        accent_hex = get_active_theme().accent

        # All status bar options are best-effort — batch into one subprocess
        border_fmt = (
            f"#{{?pane_active,#[fg={accent_hex} bold],#[fg=#444444]}}"
            f" #{{pane_title}} "
        )
        self._run_tmux_batch(
            ("set-option", "-t", session_name,
             "status-right",
             f"#(sh -c {shlex.quote(status_script)}) | %H:%M"),
            ("set-option", "-t", session_name, "status-interval", "5"),
            ("set-option", "-t", session_name, "status-right-length", "80"),
            ("set-option", "-t", session_name,
             "status-style", f"bg=#262626,fg={accent_hex}"),
            ("set-option", "-t", session_name,
             "pane-border-style", "fg=#444444"),
            ("set-option", "-t", session_name,
             "pane-active-border-style", f"fg={accent_hex}"),
            ("set-option", "-t", session_name,
             "pane-border-indicators", "arrows"),
            ("set-option", "-t", session_name,
             "pane-border-lines", "heavy"),
            ("set-option", "-t", session_name,
             "pane-border-format", border_fmt),
        )

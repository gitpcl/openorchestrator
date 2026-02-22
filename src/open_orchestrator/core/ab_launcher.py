"""A/B agent launcher for dual-worktree orchestration.

This module provides the ABLauncher class for creating side-by-side
AI tool comparisons by launching two agents in isolated worktrees
with identical prompts.
"""

from datetime import datetime
from pathlib import Path

import libtmux
from libtmux.constants import PaneDirection

from open_orchestrator.config import AITool
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.ab_workspace import ABWorkspace, ABWorkspaceStore
from open_orchestrator.utils.io import safe_read_json, safe_write_json


class ABLauncherError(Exception):
    """Base exception for A/B launcher operations."""


class ToolNotInstalledError(ABLauncherError):
    """Raised when an AI tool is not installed."""


class ABLauncher:
    """Manages A/B comparison workflows with dual worktrees and split agents."""

    def __init__(
        self,
        repo_path: Path | None = None,
        store_path: Path | None = None,
    ):
        """
        Initialize the ABLauncher.

        Args:
            repo_path: Path to git repository (defaults to current directory)
            store_path: Path to A/B workspace store JSON file
                       (defaults to ~/.open-orchestrator/ab_workspaces.json)
        """
        self.worktree_manager = WorktreeManager(repo_path=repo_path)
        self.tmux_manager = TmuxManager()

        if store_path is None:
            self.store_path = Path.home() / ".open-orchestrator" / "ab_workspaces.json"
        else:
            self.store_path = store_path

        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_store()

    def _load_store(self) -> None:
        """Load A/B workspace store from disk."""
        data = safe_read_json(self.store_path)
        if data:
            self.store = ABWorkspaceStore(**data)
        else:
            self.store = ABWorkspaceStore()

    def _save_store(self) -> None:
        """Save A/B workspace store to disk."""
        safe_write_json(self.store_path, self.store.model_dump(mode="json"))

    def _validate_tools(self, tool_a: AITool, tool_b: AITool) -> None:
        """
        Validate that both AI tools are installed.

        Args:
            tool_a: First AI tool
            tool_b: Second AI tool

        Raises:
            ToolNotInstalledError: If either tool is not installed
        """
        if not AITool.is_installed(tool_a):
            hint = AITool.get_install_hint(tool_a)
            raise ToolNotInstalledError(f"AI tool '{tool_a.value}' is not installed. {hint}")

        if not AITool.is_installed(tool_b):
            hint = AITool.get_install_hint(tool_b)
            raise ToolNotInstalledError(f"AI tool '{tool_b.value}' is not installed. {hint}")

    def _generate_workspace_id(self, branch: str) -> str:
        """
        Generate unique workspace ID.

        Args:
            branch: Base branch name

        Returns:
            Unique workspace ID
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        sanitized_branch = branch.replace("/", "-").replace(".", "-")
        return f"ab-{sanitized_branch}-{timestamp}"

    def _generate_worktree_name(self, branch: str, tool: AITool) -> str:
        """
        Generate worktree name with tool suffix.

        Args:
            branch: Base branch name
            tool: AI tool for suffix

        Returns:
            Branch name with tool suffix (e.g., 'feature/auth-claude')
        """
        return f"{branch}-{tool.value}"

    def _generate_session_name(self, workspace_id: str) -> str:
        """
        Generate tmux session name for A/B workspace.

        Args:
            workspace_id: Workspace ID

        Returns:
            tmux session name
        """
        return f"owt-{workspace_id}"

    def launch(
        self,
        branch: str,
        tool_a: AITool,
        tool_b: AITool,
        base_branch: str | None = None,
        initial_prompt: str | None = None,
    ) -> ABWorkspace:
        """
        Launch A/B comparison with two AI tools in isolated worktrees.

        Creates two worktrees from the same base branch, opens a split tmux
        session with both agents side-by-side, and optionally dispatches
        an identical initial prompt to both agents.

        Args:
            branch: Base branch name (will be suffixed with tool names)
            tool_a: First AI tool to use
            tool_b: Second AI tool to use
            base_branch: Base branch for creating new branches
            initial_prompt: Optional prompt to send to both agents

        Returns:
            ABWorkspace with metadata about the paired worktrees

        Raises:
            ToolNotInstalledError: If either AI tool is not installed
            ABLauncherError: If worktree or session creation fails
        """
        # Validate both tools are installed
        self._validate_tools(tool_a, tool_b)

        # Generate workspace ID and names
        workspace_id = self._generate_workspace_id(branch)
        worktree_a_branch = self._generate_worktree_name(branch, tool_a)
        worktree_b_branch = self._generate_worktree_name(branch, tool_b)
        session_name = self._generate_session_name(workspace_id)

        # Create both worktrees
        try:
            worktree_a = self.worktree_manager.create(
                branch=worktree_a_branch,
                base_branch=base_branch,
            )
            worktree_b = self.worktree_manager.create(
                branch=worktree_b_branch,
                base_branch=base_branch,
            )
        except Exception as e:
            raise ABLauncherError(f"Failed to create worktrees: {e}") from e

        # Create split tmux session
        try:
            self._create_split_session(
                session_name=session_name,
                worktree_a_path=str(worktree_a.path),
                worktree_b_path=str(worktree_b.path),
                tool_a=tool_a,
                tool_b=tool_b,
                worktree_a_name=worktree_a_branch,
                worktree_b_name=worktree_b_branch,
            )
        except Exception as e:
            raise ABLauncherError(f"Failed to create tmux session: {e}") from e

        # Dispatch initial prompt if provided
        if initial_prompt:
            try:
                self._dispatch_prompt(session_name, initial_prompt)
            except Exception as e:
                raise ABLauncherError(f"Failed to dispatch initial prompt: {e}") from e

        # Create and store workspace metadata
        ab_workspace = ABWorkspace(
            id=workspace_id,
            branch=branch,
            worktree_a=worktree_a_branch,
            worktree_b=worktree_b_branch,
            tool_a=tool_a,
            tool_b=tool_b,
            tmux_session=session_name,
            initial_prompt=initial_prompt,
        )

        self.store.add_workspace(ab_workspace)
        self._save_store()

        return ab_workspace

    def _create_split_session(
        self,
        session_name: str,
        worktree_a_path: str,
        worktree_b_path: str,
        tool_a: AITool,
        tool_b: AITool,
        worktree_a_name: str,
        worktree_b_name: str,
    ) -> libtmux.Session:
        """
        Create split tmux session with both AI tools.

        Args:
            session_name: Name for the tmux session
            worktree_a_path: Path to first worktree
            worktree_b_path: Path to second worktree
            tool_a: First AI tool
            tool_b: Second AI tool
            worktree_a_name: Name of first worktree (for pane title)
            worktree_b_name: Name of second worktree (for pane title)

        Returns:
            libtmux.Session object

        Raises:
            ABLauncherError: If session creation fails
        """
        # Create session in worktree_a directory first
        session = self.tmux_manager.server.new_session(
            session_name=session_name,
            start_directory=worktree_a_path,
            window_name="ab-comparison",
            attach=False,
        )

        window = session.active_window
        if not window:
            raise ABLauncherError("Failed to get active window")

        # Split horizontally to create second pane
        window.split(
            start_directory=worktree_b_path,
            direction=PaneDirection.Right,
        )

        # Apply even-horizontal layout for equal sizing
        window.select_layout("even-horizontal")

        # Enable pane border status to show tool names
        window.set_window_option("pane-border-status", "top")
        window.set_window_option("pane-border-format", " #{pane_title} ")

        # Enable mouse mode for easy navigation
        session.set_option("mouse", "on")

        # Set pane titles
        panes = window.panes
        if len(panes) >= 2:
            panes[0].set_title(f"{worktree_a_name} ({tool_a.value})")
            panes[1].set_title(f"{worktree_b_name} ({tool_b.value})")

            # Start AI tools in each pane
            self.tmux_manager._start_ai_tool_in_pane(panes[0], tool_a)
            self.tmux_manager._start_ai_tool_in_pane(panes[1], tool_b)

        # Select first pane
        panes[0].select()

        return session

    def _dispatch_prompt(self, session_name: str, prompt: str) -> None:
        """
        Dispatch identical prompt to both panes.

        Args:
            session_name: Name of tmux session
            prompt: Prompt to send to both agents

        Raises:
            ABLauncherError: If prompt dispatch fails
        """
        try:
            # Send to pane 0 (tool_a)
            self.tmux_manager.send_keys_to_pane(
                session_name=session_name,
                keys=prompt,
                pane_index=0,
                window_index=0,
            )

            # Send to pane 1 (tool_b)
            self.tmux_manager.send_keys_to_pane(
                session_name=session_name,
                keys=prompt,
                pane_index=1,
                window_index=0,
            )
        except Exception as e:
            raise ABLauncherError(f"Failed to dispatch prompt: {e}") from e

    def get_workspace(self, workspace_id: str) -> ABWorkspace | None:
        """
        Get A/B workspace by ID.

        Args:
            workspace_id: Workspace ID to look up

        Returns:
            ABWorkspace if found, None otherwise
        """
        return self.store.get_workspace(workspace_id)

    def list_workspaces(self) -> list[ABWorkspace]:
        """
        List all A/B workspaces.

        Returns:
            List of ABWorkspace objects
        """
        return self.store.list_workspaces()

    def find_by_worktree(self, worktree_name: str) -> ABWorkspace | None:
        """
        Find A/B workspace containing a specific worktree.

        Args:
            worktree_name: Name of worktree to search for

        Returns:
            ABWorkspace if found, None otherwise
        """
        return self.store.find_by_worktree(worktree_name)

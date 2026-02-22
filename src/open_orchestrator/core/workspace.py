"""Workspace management for unified multi-pane development.

This module provides the WorkspaceManager class for creating and managing
unified workspaces where multiple worktrees are visible as panes in a single
tmux session.
"""

from pathlib import Path

from open_orchestrator.models.workspace import Workspace, WorkspaceLayout, WorkspacePane, WorkspaceStore
from open_orchestrator.utils.io import safe_read_json, safe_write_json


class WorkspaceError(Exception):
    """Base exception for workspace operations."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when a workspace cannot be found."""


class WorkspaceFullError(WorkspaceError):
    """Raised when trying to add pane to full workspace."""


class WorkspaceManager:
    """Manages unified workspaces for multi-pane worktree development."""

    def __init__(self, store_path: Path | None = None):
        """
        Initialize the WorkspaceManager.

        Args:
            store_path: Path to workspace store JSON file.
                       Defaults to ~/.open-orchestrator/workspaces.json
        """
        if store_path is None:
            self.store_path = Path.home() / ".open-orchestrator" / "workspaces.json"
        else:
            self.store_path = store_path

        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_store()

    def _load_store(self) -> None:
        """Load workspace store from disk."""
        data = safe_read_json(self.store_path)
        if data:
            self.store = WorkspaceStore(**data)
        else:
            self.store = WorkspaceStore()

    def _save_store(self) -> None:
        """Save workspace store to disk."""
        safe_write_json(self.store_path, self.store.model_dump(mode="json"))

    def create_workspace(
        self,
        name: str,
        session_id: str,
        layout: WorkspaceLayout = WorkspaceLayout.MAIN_FOCUS,
        main_pane_index: int = 0,
        max_panes: int = 4,
    ) -> Workspace:
        """
        Create a new workspace.

        Args:
            name: Workspace name (also used as tmux session name)
            session_id: tmux session ID
            layout: Pane layout to use
            main_pane_index: Index of main repository pane
            max_panes: Maximum panes allowed

        Returns:
            Created Workspace

        Raises:
            WorkspaceError: If workspace already exists
        """
        if self.store.get_workspace(name):
            raise WorkspaceError(f"Workspace '{name}' already exists")

        workspace = Workspace(
            name=name,
            session_id=session_id,
            layout=layout,
            main_pane_index=main_pane_index,
            max_panes=max_panes,
        )

        # Add main pane
        main_pane = WorkspacePane(
            pane_index=main_pane_index,
            worktree_name=None,  # Main is not a worktree
            is_main=True,
            is_active=True,
        )
        workspace.add_pane(main_pane)

        self.store.add_workspace(workspace)
        self._save_store()
        return workspace

    def get_workspace(self, name: str) -> Workspace:
        """
        Get workspace by name.

        Args:
            name: Workspace name

        Returns:
            Workspace

        Raises:
            WorkspaceNotFoundError: If workspace not found
        """
        workspace = self.store.get_workspace(name)
        if not workspace:
            raise WorkspaceNotFoundError(f"Workspace '{name}' not found")
        return workspace

    def get_or_create_default(self, project_name: str, session_id: str = "") -> Workspace:
        """
        Get default workspace or create one if none exists.

        Args:
            project_name: Project name for default workspace
            session_id: tmux session ID (if creating new workspace)

        Returns:
            Workspace
        """
        if self.store.default_workspace:
            try:
                return self.get_workspace(self.store.default_workspace)
            except WorkspaceNotFoundError:
                pass

        # Create default workspace
        name = f"owt-{project_name}"
        return self.create_workspace(name, session_id)

    def list_workspaces(self) -> list[Workspace]:
        """
        List all workspaces.

        Returns:
            List of Workspace objects
        """
        return self.store.list_workspaces()

    def delete_workspace(self, name: str) -> None:
        """
        Delete a workspace.

        Args:
            name: Workspace name

        Raises:
            WorkspaceNotFoundError: If workspace not found
        """
        if not self.store.remove_workspace(name):
            raise WorkspaceNotFoundError(f"Workspace '{name}' not found")
        self._save_store()

    def add_worktree_pane(
        self,
        workspace_name: str,
        pane_index: int,
        worktree_name: str,
        worktree_path: Path,
    ) -> WorkspacePane:
        """
        Add a worktree pane to a workspace.

        Args:
            workspace_name: Workspace to add pane to
            pane_index: tmux pane index
            worktree_name: Worktree name
            worktree_path: Path to worktree

        Returns:
            Created WorkspacePane

        Raises:
            WorkspaceNotFoundError: If workspace not found
            WorkspaceFullError: If workspace is full
        """
        workspace = self.get_workspace(workspace_name)

        if workspace.is_full:
            raise WorkspaceFullError(
                f"Workspace '{workspace_name}' is full ({workspace.max_panes} panes). "
                f"Create a new workspace or use --separate-session flag."
            )

        pane = WorkspacePane(
            pane_index=pane_index,
            worktree_name=worktree_name,
            worktree_path=worktree_path,
            is_main=False,
            is_active=False,
        )

        workspace.add_pane(pane)
        self._save_store()
        return pane

    def remove_worktree_pane(self, workspace_name: str, worktree_name: str) -> bool:
        """
        Remove a worktree pane from workspace.

        Args:
            workspace_name: Workspace name
            worktree_name: Worktree to remove

        Returns:
            True if pane was removed, False if not found

        Raises:
            WorkspaceNotFoundError: If workspace not found
        """
        workspace = self.get_workspace(workspace_name)

        pane = workspace.get_pane_by_worktree(worktree_name)
        if not pane:
            return False

        workspace.remove_pane(pane.pane_index)
        self._save_store()
        return True

    def find_workspace_for_pane(self, pane_index: int) -> Workspace | None:
        """
        Find workspace containing a specific pane index.

        Args:
            pane_index: tmux pane index to find

        Returns:
            Workspace containing the pane, or None if not found
        """
        for workspace in self.list_workspaces():
            for pane in workspace.panes:
                if pane.pane_index == pane_index:
                    return workspace
        return None

    def get_workspace_by_worktree(self, worktree_name: str) -> Workspace | None:
        """
        Find workspace containing a specific worktree.

        Args:
            worktree_name: Worktree name to find

        Returns:
            Workspace containing the worktree, or None if not found
        """
        for workspace in self.list_workspaces():
            if workspace.get_pane_by_worktree(worktree_name):
                return workspace
        return None

    def set_default_workspace(self, name: str) -> None:
        """
        Set the default workspace.

        Args:
            name: Workspace name

        Raises:
            WorkspaceNotFoundError: If workspace not found
        """
        if not self.store.get_workspace(name):
            raise WorkspaceNotFoundError(f"Workspace '{name}' not found")

        self.store.default_workspace = name
        self._save_store()

    def get_layout_definition(self, layout: WorkspaceLayout) -> dict[str, str]:
        """
        Get tmux layout definition for a workspace layout.

        Args:
            layout: WorkspaceLayout enum value

        Returns:
            Dict with tmux layout commands and percentages
        """
        layouts = {
            WorkspaceLayout.MAIN_FOCUS: {
                "description": "1/3 left main + 3 horizontal right",
                "tmux_layout": "main-vertical",
                "main_pane_percentage": 33,
                "split_orientation": "horizontal",  # Right side splits horizontally
            },
            WorkspaceLayout.GRID: {
                "description": "2x2 grid",
                "tmux_layout": "tiled",
                "main_pane_percentage": 50,
                "split_orientation": "both",
            },
            WorkspaceLayout.STACK: {
                "description": "Vertical stack",
                "tmux_layout": "even-vertical",
                "main_pane_percentage": 100,
                "split_orientation": "vertical",
            },
            WorkspaceLayout.FOCUS: {
                "description": "Large main + small sidebar",
                "tmux_layout": "main-vertical",
                "main_pane_percentage": 70,
                "split_orientation": "vertical",
            },
            WorkspaceLayout.TILE: {
                "description": "Auto-tile all panes",
                "tmux_layout": "tiled",
                "main_pane_percentage": 50,
                "split_orientation": "auto",
            },
        }
        return layouts.get(layout, layouts[WorkspaceLayout.MAIN_FOCUS])

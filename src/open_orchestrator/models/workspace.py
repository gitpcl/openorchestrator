"""Pydantic models for unified workspace management.

This module provides data models for:
- Workspace sessions that contain multiple worktree panes
- Pane layout and arrangement within workspaces
- Workspace-level configuration and state
"""

from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class WorkspaceLayout(str, Enum):
    """Predefined workspace layouts."""

    MAIN_FOCUS = "main-focus"  # 1/3 left main + 3 horizontal right (default)
    GRID = "grid"  # 2x2 grid layout
    STACK = "stack"  # Vertical stack
    FOCUS = "focus"  # One large pane + small sidebar
    TILE = "tile"  # Auto-tile all panes


class WorkspacePane(BaseModel):
    """Represents a single pane within a workspace."""

    pane_index: int = Field(..., description="tmux pane index")
    worktree_name: str | None = Field(default=None, description="Associated worktree name")
    worktree_path: Path | None = Field(default=None, description="Path to worktree")
    is_main: bool = Field(default=False, description="Whether this is the main repository pane")
    is_active: bool = Field(default=False, description="Whether this pane currently has focus")
    created_at: datetime = Field(default_factory=datetime.now, description="When this pane was created")


class Workspace(BaseModel):
    """Unified workspace containing multiple worktree panes."""

    name: str = Field(..., description="Workspace name (also tmux session name)")
    session_id: str = Field(..., description="tmux session ID")
    layout: WorkspaceLayout = Field(default=WorkspaceLayout.MAIN_FOCUS, description="Current pane layout")
    panes: list[WorkspacePane] = Field(default_factory=list, description="Panes in this workspace")
    main_pane_index: int = Field(default=0, description="Index of the main repository pane")
    max_panes: int = Field(default=4, description="Maximum panes allowed (1 main + 3 worktrees)")
    auto_balance: bool = Field(default=True, description="Auto-resize panes when adding/removing")
    created_at: datetime = Field(default_factory=datetime.now, description="When workspace was created")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update time")

    @property
    def is_full(self) -> bool:
        """Check if workspace has reached max panes."""
        return len(self.panes) >= self.max_panes

    @property
    def available_panes(self) -> int:
        """Get number of available pane slots."""
        return max(0, self.max_panes - len(self.panes))

    @property
    def worktree_panes(self) -> list[WorkspacePane]:
        """Get only panes with worktrees (exclude main)."""
        return [p for p in self.panes if not p.is_main]

    def get_pane_by_worktree(self, worktree_name: str) -> WorkspacePane | None:
        """Find pane by worktree name."""
        for pane in self.panes:
            if pane.worktree_name == worktree_name:
                return pane
        return None

    def get_main_pane(self) -> WorkspacePane | None:
        """Get the main repository pane."""
        for pane in self.panes:
            if pane.is_main:
                return pane
        return None

    def add_pane(self, pane: WorkspacePane) -> None:
        """Add a pane to the workspace."""
        if self.is_full:
            raise ValueError(f"Workspace {self.name} is full ({self.max_panes} panes)")
        self.panes.append(pane)
        self.updated_at = datetime.now()

    def remove_pane(self, pane_index: int) -> bool:
        """Remove a pane by index. Returns True if removed."""
        for i, pane in enumerate(self.panes):
            if pane.pane_index == pane_index:
                # Don't allow removing main pane
                if pane.is_main:
                    raise ValueError("Cannot remove main pane from workspace")
                self.panes.pop(i)
                self.updated_at = datetime.now()
                return True
        return False


class WorkspaceStore(BaseModel):
    """Persistent storage for workspaces."""

    version: str = Field(default="1.0", description="Storage format version")
    updated_at: datetime = Field(default_factory=datetime.now, description="When store was last updated")
    workspaces: dict[str, Workspace] = Field(default_factory=dict, description="Map of workspace name to Workspace")
    default_workspace: str | None = Field(default=None, description="Default workspace to use")

    def get_workspace(self, name: str) -> Workspace | None:
        """Get workspace by name."""
        return self.workspaces.get(name)

    def add_workspace(self, workspace: Workspace) -> None:
        """Add or update a workspace."""
        self.workspaces[workspace.name] = workspace
        if not self.default_workspace:
            self.default_workspace = workspace.name
        self.updated_at = datetime.now()

    def remove_workspace(self, name: str) -> bool:
        """Remove workspace. Returns True if removed."""
        if name in self.workspaces:
            del self.workspaces[name]
            if self.default_workspace == name:
                self.default_workspace = next(iter(self.workspaces.keys()), None)
            self.updated_at = datetime.now()
            return True
        return False

    def list_workspaces(self) -> list[Workspace]:
        """Get all workspaces."""
        return list(self.workspaces.values())

    def get_or_create_default(self, project_name: str) -> Workspace:
        """Get default workspace or create one if none exists."""
        if self.default_workspace and self.default_workspace in self.workspaces:
            return self.workspaces[self.default_workspace]

        # Create default workspace
        default_name = f"owt-{project_name}"
        workspace = Workspace(
            name=default_name,
            session_id="",  # Will be set when created
            layout=WorkspaceLayout.MAIN_FOCUS,
        )
        self.add_workspace(workspace)
        return workspace

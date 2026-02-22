"""Data models for A/B workspace management.

This module provides data models for tracking A/B comparison workspaces
where two AI tools work on the same task in parallel isolated worktrees.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from open_orchestrator.config import AITool


class ABWorkspace(BaseModel):
    """A/B comparison workspace with two paired worktrees."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "feature-auth-ab-20240201-120000",
                "branch": "feature/authentication",
                "worktree_a": "feature-authentication-claude",
                "worktree_b": "feature-authentication-opencode",
                "tool_a": "claude",
                "tool_b": "opencode",
                "tmux_session": "owt-ab-feature-auth",
                "initial_prompt": "Implement JWT authentication",
                "created_at": "2024-02-01T12:00:00",
            }
        }
    )

    id: str = Field(
        description="Unique identifier for the A/B workspace",
    )
    branch: str = Field(
        description="Base branch name (before tool suffixes)",
    )
    worktree_a: str = Field(
        description="Name of first worktree (with tool suffix)",
    )
    worktree_b: str = Field(
        description="Name of second worktree (with tool suffix)",
    )
    tool_a: AITool = Field(
        description="AI tool used in first worktree",
    )
    tool_b: AITool = Field(
        description="AI tool used in second worktree",
    )
    tmux_session: str = Field(
        description="tmux session name for split-pane view",
    )
    initial_prompt: str | None = Field(
        default=None,
        description="Initial prompt sent to both agents",
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="Timestamp when workspace was created",
    )

    def get_worktrees(self) -> tuple[str, str]:
        """
        Get tuple of worktree names.

        Returns:
            Tuple of (worktree_a, worktree_b)
        """
        return (self.worktree_a, self.worktree_b)

    def get_tools(self) -> tuple[AITool, AITool]:
        """
        Get tuple of AI tools.

        Returns:
            Tuple of (tool_a, tool_b)
        """
        return (self.tool_a, self.tool_b)


class ABWorkspaceStore(BaseModel):
    """Persistent store for A/B workspaces."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "workspaces": {},
            }
        }
    )

    workspaces: dict[str, ABWorkspace] = Field(
        default_factory=dict,
        description="Map of workspace ID to ABWorkspace",
    )

    def add_workspace(self, workspace: ABWorkspace) -> None:
        """
        Add workspace to store.

        Args:
            workspace: ABWorkspace to add

        Raises:
            ValueError: If workspace ID already exists
        """
        if workspace.id in self.workspaces:
            raise ValueError(f"A/B workspace '{workspace.id}' already exists")
        self.workspaces[workspace.id] = workspace

    def get_workspace(self, workspace_id: str) -> ABWorkspace | None:
        """
        Get workspace by ID.

        Args:
            workspace_id: Workspace ID to look up

        Returns:
            ABWorkspace if found, None otherwise
        """
        return self.workspaces.get(workspace_id)

    def remove_workspace(self, workspace_id: str) -> None:
        """
        Remove workspace from store.

        Args:
            workspace_id: Workspace ID to remove

        Raises:
            KeyError: If workspace not found
        """
        if workspace_id not in self.workspaces:
            raise KeyError(f"A/B workspace '{workspace_id}' not found")
        del self.workspaces[workspace_id]

    def list_workspaces(self) -> list[ABWorkspace]:
        """
        List all A/B workspaces.

        Returns:
            List of ABWorkspace objects
        """
        return list(self.workspaces.values())

    def find_by_worktree(self, worktree_name: str) -> ABWorkspace | None:
        """
        Find A/B workspace containing a specific worktree.

        Args:
            worktree_name: Name of worktree to search for

        Returns:
            ABWorkspace if found, None otherwise
        """
        for workspace in self.workspaces.values():
            if worktree_name in (workspace.worktree_a, workspace.worktree_b):
                return workspace
        return None

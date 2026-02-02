"""
Pydantic models for GitHub PR linking.

This module provides data models for:
- Tracking PR associations with worktrees
- PR status and metadata
- PR-centric worktree management
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PRStatus(str, Enum):
    """Status of a GitHub Pull Request."""

    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"
    DRAFT = "draft"
    UNKNOWN = "unknown"


class PRInfo(BaseModel):
    """Information about a linked GitHub Pull Request."""

    model_config = ConfigDict(use_enum_values=True)

    worktree_name: str = Field(..., description="Name of the worktree")
    repo_owner: str = Field(..., description="GitHub repository owner")
    repo_name: str = Field(..., description="GitHub repository name")
    pr_number: int = Field(..., ge=1, description="PR number")
    pr_url: str = Field(..., description="Full URL to the PR")
    branch: str = Field(..., description="Branch name")
    title: str | None = Field(default=None, description="PR title")
    status: PRStatus = Field(default=PRStatus.UNKNOWN, description="PR status")
    auto_detected: bool = Field(default=False, description="Was PR auto-detected from branch name?")
    linked_at: datetime = Field(default_factory=datetime.now, description="When the PR was linked")
    last_checked: datetime | None = Field(default=None, description="When the PR status was last checked")
    created_at: datetime = Field(default_factory=datetime.now, description="When this record was created")
    updated_at: datetime = Field(default_factory=datetime.now, description="When this record was last updated")

    @property
    def full_repo(self) -> str:
        """Get the full repository path (owner/repo)."""
        return f"{self.repo_owner}/{self.repo_name}"

    @property
    def short_url(self) -> str:
        """Get a short URL format for display."""
        return f"{self.repo_owner}/{self.repo_name}#{self.pr_number}"

    @property
    def is_open(self) -> bool:
        """Check if the PR is still open."""
        return self.status in (PRStatus.OPEN, PRStatus.DRAFT)

    @property
    def is_merged(self) -> bool:
        """Check if the PR was merged."""
        return self.status == PRStatus.MERGED


class PRLinkResult(BaseModel):
    """Result of a PR link operation."""

    model_config = ConfigDict(use_enum_values=True)

    success: bool = Field(..., description="Whether linking succeeded")
    worktree_name: str = Field(..., description="Worktree that was linked")
    pr_number: int | None = Field(default=None, description="PR number if found")
    pr_url: str | None = Field(default=None, description="PR URL if found")
    message: str = Field(default="", description="Human-readable result message")
    auto_detected: bool = Field(default=False, description="Was PR auto-detected?")


class PRStore(BaseModel):
    """Persistent storage for PR links."""

    version: str = Field(default="1.0", description="Storage format version")
    updated_at: datetime = Field(default_factory=datetime.now, description="When the store was last updated")
    links: dict[str, PRInfo] = Field(default_factory=dict, description="Map of worktree name to PR info")

    def get_pr(self, worktree_name: str) -> PRInfo | None:
        """Get PR info for a worktree."""
        return self.links.get(worktree_name)

    def set_pr(self, pr_info: PRInfo) -> None:
        """Set PR info for a worktree."""
        self.links[pr_info.worktree_name] = pr_info
        self.updated_at = datetime.now()

    def remove_pr(self, worktree_name: str) -> bool:
        """Remove PR link for a worktree. Returns True if removed."""
        if worktree_name in self.links:
            del self.links[worktree_name]
            self.updated_at = datetime.now()
            return True
        return False

    def get_all_prs(self) -> list[PRInfo]:
        """Get all PR links."""
        return list(self.links.values())

    def get_prs_by_status(self, status: PRStatus) -> list[PRInfo]:
        """Get all PR links with a specific status."""
        return [p for p in self.links.values() if p.status == status]

    def get_merged_prs(self) -> list[PRInfo]:
        """Get all worktrees with merged PRs."""
        return [p for p in self.links.values() if p.is_merged]

    def get_open_prs(self) -> list[PRInfo]:
        """Get all worktrees with open PRs."""
        return [p for p in self.links.values() if p.is_open]

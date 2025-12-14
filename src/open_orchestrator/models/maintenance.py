"""
Pydantic models for maintenance features (cleanup and sync).

This module provides data models for:
- Worktree usage tracking and statistics
- Cleanup operation reporting
- Sync operation reporting
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class WorktreeStatus(str, Enum):
    """Status of a worktree."""

    ACTIVE = "active"
    STALE = "stale"
    DIRTY = "dirty"
    UNKNOWN = "unknown"


class SyncStatus(str, Enum):
    """Status of a sync operation."""

    SUCCESS = "success"
    UP_TO_DATE = "up_to_date"
    CONFLICTS = "conflicts"
    NO_UPSTREAM = "no_upstream"
    ERROR = "error"
    UNCOMMITTED_CHANGES = "uncommitted_changes"


class WorktreeUsageStats(BaseModel):
    """Usage statistics for a single worktree."""

    worktree_path: str = Field(..., description="Absolute path to the worktree")
    branch_name: str = Field(..., description="Git branch name")
    created_at: datetime = Field(..., description="When the worktree was created")
    last_accessed: datetime = Field(..., description="Last access timestamp")
    access_count: int = Field(default=0, ge=0, description="Number of times accessed")
    last_commit_date: Optional[datetime] = Field(
        default=None,
        description="Date of the most recent commit"
    )
    has_uncommitted_changes: bool = Field(
        default=False,
        description="Whether worktree has uncommitted changes"
    )
    has_unpushed_commits: bool = Field(
        default=False,
        description="Whether worktree has commits not pushed to remote"
    )
    status: WorktreeStatus = Field(
        default=WorktreeStatus.UNKNOWN,
        description="Current status of the worktree"
    )

    class Config:
        use_enum_values = True


class CleanupReport(BaseModel):
    """Report generated after cleanup operation."""

    timestamp: datetime = Field(..., description="When the cleanup was performed")
    dry_run: bool = Field(..., description="Whether this was a dry run")
    stale_threshold_days: int = Field(
        ...,
        ge=1,
        description="Number of days after which a worktree is considered stale"
    )
    worktrees_scanned: int = Field(
        default=0,
        ge=0,
        description="Total worktrees scanned"
    )
    stale_worktrees_found: int = Field(
        default=0,
        ge=0,
        description="Number of stale worktrees found"
    )
    worktrees_cleaned: int = Field(
        default=0,
        ge=0,
        description="Number of worktrees cleaned up"
    )
    worktrees_skipped: int = Field(
        default=0,
        ge=0,
        description="Number of worktrees skipped due to protection"
    )
    errors: List[str] = Field(
        default_factory=list,
        description="List of errors encountered during cleanup"
    )
    cleaned_paths: List[str] = Field(
        default_factory=list,
        description="Paths of worktrees that were cleaned"
    )
    skipped_paths: List[str] = Field(
        default_factory=list,
        description="Paths of worktrees that were skipped with reasons"
    )


class WorktreeSyncResult(BaseModel):
    """Result of syncing a single worktree."""

    worktree_path: str = Field(..., description="Path to the worktree")
    branch_name: str = Field(..., description="Current branch name")
    status: SyncStatus = Field(..., description="Result status of the sync")
    message: str = Field(..., description="Human-readable status message")
    commits_pulled: int = Field(
        default=0,
        ge=0,
        description="Number of commits pulled from upstream"
    )
    commits_behind: int = Field(
        default=0,
        ge=0,
        description="Number of commits behind upstream before sync"
    )
    commits_ahead: int = Field(
        default=0,
        ge=0,
        description="Number of commits ahead of upstream"
    )
    upstream_branch: Optional[str] = Field(
        default=None,
        description="Name of the upstream branch"
    )

    class Config:
        use_enum_values = True


class SyncReport(BaseModel):
    """Report generated after sync operation."""

    timestamp: datetime = Field(..., description="When the sync was performed")
    worktrees_synced: int = Field(
        default=0,
        ge=0,
        description="Total number of worktrees processed"
    )
    successful: int = Field(
        default=0,
        ge=0,
        description="Number of successful syncs"
    )
    failed: int = Field(
        default=0,
        ge=0,
        description="Number of failed syncs"
    )
    up_to_date: int = Field(
        default=0,
        ge=0,
        description="Number of worktrees already up to date"
    )
    with_conflicts: int = Field(
        default=0,
        ge=0,
        description="Number of syncs with merge conflicts"
    )
    results: List[WorktreeSyncResult] = Field(
        default_factory=list,
        description="Individual results for each worktree"
    )


class UsageStatsSummary(BaseModel):
    """Summary of usage statistics across all worktrees."""

    total_worktrees: int = Field(default=0, ge=0)
    active_worktrees: int = Field(default=0, ge=0)
    stale_worktrees: int = Field(default=0, ge=0)
    dirty_worktrees: int = Field(default=0, ge=0)
    total_access_count: int = Field(default=0, ge=0)
    oldest_worktree: Optional[datetime] = None
    newest_worktree: Optional[datetime] = None
    most_recently_accessed: Optional[datetime] = None
    least_recently_accessed: Optional[datetime] = None

"""
Sync service for synchronizing worktrees with upstream.

This module provides functionality to:
- Sync individual worktrees with their upstream branches
- Sync all worktrees at once
- Handle upstream tracking and merge conflicts
"""

import subprocess
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel


class SyncStatus(str, Enum):
    """Status of a sync operation."""

    SUCCESS = "success"
    UP_TO_DATE = "up_to_date"
    CONFLICTS = "conflicts"
    NO_UPSTREAM = "no_upstream"
    ERROR = "error"
    UNCOMMITTED_CHANGES = "uncommitted_changes"


class WorktreeSyncResult(BaseModel):
    """Result of syncing a single worktree."""

    worktree_path: str
    branch_name: str
    status: SyncStatus
    message: str
    commits_pulled: int = 0
    commits_behind: int = 0
    commits_ahead: int = 0
    upstream_branch: Optional[str] = None


class SyncReport(BaseModel):
    """Report generated after sync operation."""

    timestamp: datetime
    worktrees_synced: int
    successful: int
    failed: int
    up_to_date: int
    with_conflicts: int
    results: List[WorktreeSyncResult] = []


@dataclass
class SyncConfig:
    """Configuration for sync operations."""

    strategy: str = "merge"
    auto_stash: bool = True
    prune_remote: bool = True
    fetch_all: bool = False
    timeout_seconds: int = 60


class SyncService:
    """
    Service for synchronizing worktrees with upstream branches.

    This service handles fetching, pulling, and managing upstream
    tracking for git worktrees.
    """

    def __init__(self, config: Optional[SyncConfig] = None):
        self.config = config or SyncConfig()

    def sync_worktree(self, worktree_path: str) -> WorktreeSyncResult:
        """
        Sync a single worktree with its upstream branch.

        Args:
            worktree_path: Path to the worktree to sync

        Returns:
            WorktreeSyncResult with details of the operation
        """
        path = Path(worktree_path)

        if not path.exists():
            return WorktreeSyncResult(
                worktree_path=worktree_path,
                branch_name="unknown",
                status=SyncStatus.ERROR,
                message=f"Worktree path does not exist: {worktree_path}"
            )

        branch_name = self._get_current_branch(path)
        upstream = self._get_upstream_branch(path)

        if not upstream:
            return WorktreeSyncResult(
                worktree_path=worktree_path,
                branch_name=branch_name,
                status=SyncStatus.NO_UPSTREAM,
                message="No upstream branch configured"
            )

        if self._has_uncommitted_changes(path) and not self.config.auto_stash:
            return WorktreeSyncResult(
                worktree_path=worktree_path,
                branch_name=branch_name,
                status=SyncStatus.UNCOMMITTED_CHANGES,
                message="Uncommitted changes present and auto_stash is disabled",
                upstream_branch=upstream
            )

        try:
            stashed = False

            if self._has_uncommitted_changes(path) and self.config.auto_stash:
                self._run_git_command(path, ["stash", "push", "-m", "cwt-sync-autostash"])
                stashed = True

            self._fetch_upstream(path)

            commits_behind, commits_ahead = self._get_commit_counts(path, upstream)

            if commits_behind == 0:
                if stashed:
                    self._run_git_command(path, ["stash", "pop"])

                return WorktreeSyncResult(
                    worktree_path=worktree_path,
                    branch_name=branch_name,
                    status=SyncStatus.UP_TO_DATE,
                    message="Already up to date",
                    commits_behind=0,
                    commits_ahead=commits_ahead,
                    upstream_branch=upstream
                )

            pull_result = self._pull_changes(path)

            if stashed:
                stash_result = self._run_git_command(path, ["stash", "pop"])

                if stash_result.returncode != 0:
                    return WorktreeSyncResult(
                        worktree_path=worktree_path,
                        branch_name=branch_name,
                        status=SyncStatus.CONFLICTS,
                        message=f"Pulled {commits_behind} commits but stash pop failed: {stash_result.stderr}",
                        commits_pulled=commits_behind,
                        commits_ahead=commits_ahead,
                        upstream_branch=upstream
                    )

            if pull_result.returncode != 0:
                return WorktreeSyncResult(
                    worktree_path=worktree_path,
                    branch_name=branch_name,
                    status=SyncStatus.CONFLICTS,
                    message=f"Merge conflicts detected: {pull_result.stderr}",
                    commits_behind=commits_behind,
                    commits_ahead=commits_ahead,
                    upstream_branch=upstream
                )

            return WorktreeSyncResult(
                worktree_path=worktree_path,
                branch_name=branch_name,
                status=SyncStatus.SUCCESS,
                message=f"Successfully pulled {commits_behind} commits",
                commits_pulled=commits_behind,
                commits_ahead=commits_ahead,
                upstream_branch=upstream
            )

        except Exception as e:
            return WorktreeSyncResult(
                worktree_path=worktree_path,
                branch_name=branch_name,
                status=SyncStatus.ERROR,
                message=f"Sync failed: {str(e)}",
                upstream_branch=upstream
            )

    def sync_all(self, worktree_paths: List[str]) -> SyncReport:
        """
        Sync all provided worktrees with their upstream branches.

        Args:
            worktree_paths: List of worktree paths to sync

        Returns:
            SyncReport with aggregate results
        """
        report = SyncReport(
            timestamp=datetime.now(),
            worktrees_synced=len(worktree_paths),
            successful=0,
            failed=0,
            up_to_date=0,
            with_conflicts=0
        )

        for path in worktree_paths:
            result = self.sync_worktree(path)
            report.results.append(result)

            if result.status == SyncStatus.SUCCESS:
                report.successful += 1
            elif result.status == SyncStatus.UP_TO_DATE:
                report.up_to_date += 1
            elif result.status == SyncStatus.CONFLICTS:
                report.with_conflicts += 1
            else:
                report.failed += 1

        return report

    def get_sync_status(self, worktree_path: str) -> WorktreeSyncResult:
        """
        Get the current sync status of a worktree without making changes.

        Args:
            worktree_path: Path to the worktree

        Returns:
            WorktreeSyncResult with current status
        """
        path = Path(worktree_path)

        if not path.exists():
            return WorktreeSyncResult(
                worktree_path=worktree_path,
                branch_name="unknown",
                status=SyncStatus.ERROR,
                message=f"Worktree path does not exist: {worktree_path}"
            )

        branch_name = self._get_current_branch(path)
        upstream = self._get_upstream_branch(path)

        if not upstream:
            return WorktreeSyncResult(
                worktree_path=worktree_path,
                branch_name=branch_name,
                status=SyncStatus.NO_UPSTREAM,
                message="No upstream branch configured"
            )

        try:
            self._fetch_upstream(path)
            commits_behind, commits_ahead = self._get_commit_counts(path, upstream)

            if commits_behind == 0 and commits_ahead == 0:
                status = SyncStatus.UP_TO_DATE
                message = "Up to date with upstream"
            elif commits_behind == 0:
                status = SyncStatus.UP_TO_DATE
                message = f"Up to date, {commits_ahead} commits ahead of upstream"
            else:
                status = SyncStatus.SUCCESS
                message = f"{commits_behind} commits behind, {commits_ahead} commits ahead"

            return WorktreeSyncResult(
                worktree_path=worktree_path,
                branch_name=branch_name,
                status=status,
                message=message,
                commits_behind=commits_behind,
                commits_ahead=commits_ahead,
                upstream_branch=upstream
            )

        except Exception as e:
            return WorktreeSyncResult(
                worktree_path=worktree_path,
                branch_name=branch_name,
                status=SyncStatus.ERROR,
                message=f"Failed to get status: {str(e)}",
                upstream_branch=upstream
            )

    def setup_upstream(
        self,
        worktree_path: str,
        remote: str = "origin",
        branch: Optional[str] = None
    ) -> bool:
        """
        Setup upstream tracking for a worktree.

        Args:
            worktree_path: Path to the worktree
            remote: Remote name (default: origin)
            branch: Branch name (default: current branch)

        Returns:
            True if successful, False otherwise
        """
        path = Path(worktree_path)

        if not path.exists():
            return False

        if branch is None:
            branch = self._get_current_branch(path)

        result = self._run_git_command(
            path,
            ["branch", "--set-upstream-to", f"{remote}/{branch}"]
        )

        return result.returncode == 0

    def _run_git_command(
        self,
        worktree_path: Path,
        args: List[str]
    ) -> subprocess.CompletedProcess:
        """Run a git command in the worktree directory."""
        return subprocess.run(
            ["git"] + args,
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=self.config.timeout_seconds
        )

    def _get_current_branch(self, worktree_path: Path) -> str:
        """Get the current branch name."""
        result = self._run_git_command(worktree_path, ["branch", "--show-current"])
        return result.stdout.strip() or "HEAD"

    def _get_upstream_branch(self, worktree_path: Path) -> Optional[str]:
        """Get the upstream branch for the current branch."""
        result = self._run_git_command(
            worktree_path,
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        )

        if result.returncode == 0:
            return result.stdout.strip()

        return None

    def _has_uncommitted_changes(self, worktree_path: Path) -> bool:
        """Check if the worktree has uncommitted changes."""
        result = self._run_git_command(worktree_path, ["status", "--porcelain"])
        return bool(result.stdout.strip())

    def _fetch_upstream(self, worktree_path: Path) -> None:
        """Fetch from upstream remote."""
        args = ["fetch"]

        if self.config.prune_remote:
            args.append("--prune")

        if self.config.fetch_all:
            args.append("--all")

        self._run_git_command(worktree_path, args)

    def _get_commit_counts(
        self,
        worktree_path: Path,
        upstream: str
    ) -> tuple[int, int]:
        """Get the number of commits behind and ahead of upstream."""
        result = self._run_git_command(
            worktree_path,
            ["rev-list", "--left-right", "--count", f"{upstream}...HEAD"]
        )

        if result.returncode != 0:
            return 0, 0

        parts = result.stdout.strip().split()

        if len(parts) == 2:
            return int(parts[0]), int(parts[1])

        return 0, 0

    def _pull_changes(self, worktree_path: Path) -> subprocess.CompletedProcess:
        """Pull changes from upstream."""
        args = ["pull"]

        if self.config.strategy == "rebase":
            args.append("--rebase")

        return self._run_git_command(worktree_path, args)

"""
Cleanup service for managing stale worktrees.

This module provides functionality to:
- Track worktree usage statistics
- Detect stale worktrees based on configurable age
- Clean up worktrees with dry-run support
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

from pydantic import BaseModel


class WorktreeUsageStats(BaseModel):
    """Usage statistics for a single worktree."""

    worktree_path: str
    branch_name: str
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    last_commit_date: Optional[datetime] = None
    has_uncommitted_changes: bool = False
    has_unpushed_commits: bool = False


class CleanupReport(BaseModel):
    """Report generated after cleanup operation."""

    timestamp: datetime
    dry_run: bool
    stale_threshold_days: int
    worktrees_scanned: int
    stale_worktrees_found: int
    worktrees_cleaned: int
    worktrees_skipped: int
    errors: List[str] = []
    cleaned_paths: List[str] = []
    skipped_paths: List[str] = []


@dataclass
class CleanupConfig:
    """Configuration for cleanup operations."""

    stale_threshold_days: int = 14
    protect_uncommitted: bool = True
    protect_unpushed: bool = True
    stats_file_path: Optional[Path] = None

    def __post_init__(self):
        if self.stale_threshold_days < 1:
            raise ValueError("stale_threshold_days must be at least 1")


class UsageTracker:
    """Tracks and persists worktree usage statistics."""

    DEFAULT_STATS_FILENAME = ".worktree_stats.json"

    def __init__(self, stats_file_path: Optional[Path] = None):
        self._stats_file = stats_file_path or self._get_default_stats_path()
        self._usage_data: Dict[str, Dict[str, Any]] = {}
        self._load_stats()

    def _get_default_stats_path(self) -> Path:
        """Get default path for stats file in user's home directory."""
        return Path.home() / ".open-orchestrator" / self.DEFAULT_STATS_FILENAME

    def _load_stats(self) -> None:
        """Load usage statistics from persistent storage."""
        if self._stats_file.exists():
            try:
                with open(self._stats_file, 'r') as f:
                    self._usage_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                self._usage_data = {}

    def _save_stats(self) -> None:
        """Persist usage statistics to storage."""
        self._stats_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self._stats_file, 'w') as f:
            json.dump(self._usage_data, f, indent=2, default=str)

    def record_access(self, worktree_path: str, branch_name: str) -> None:
        """Record an access event for a worktree."""
        path_key = str(worktree_path)
        now = datetime.now().isoformat()

        if path_key not in self._usage_data:
            self._usage_data[path_key] = {
                "branch_name": branch_name,
                "created_at": now,
                "last_accessed": now,
                "access_count": 1
            }
        else:
            self._usage_data[path_key]["last_accessed"] = now
            self._usage_data[path_key]["access_count"] = (
                self._usage_data[path_key].get("access_count", 0) + 1
            )

        self._save_stats()

    def get_stats(self, worktree_path: str) -> Optional[Dict[str, Any]]:
        """Get usage statistics for a specific worktree."""
        return self._usage_data.get(str(worktree_path))

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get all tracked usage statistics."""
        return self._usage_data.copy()

    def remove_stats(self, worktree_path: str) -> None:
        """Remove statistics for a deleted worktree."""
        path_key = str(worktree_path)

        if path_key in self._usage_data:
            del self._usage_data[path_key]
            self._save_stats()

    def get_last_accessed(self, worktree_path: str) -> Optional[datetime]:
        """Get the last access time for a worktree."""
        stats = self.get_stats(worktree_path)

        if stats and "last_accessed" in stats:
            return datetime.fromisoformat(stats["last_accessed"])

        return None


class CleanupService:
    """
    Service for cleaning up stale worktrees.

    This service identifies worktrees that haven't been used recently
    and provides functionality to clean them up with safety checks.
    """

    def __init__(
        self,
        config: Optional[CleanupConfig] = None,
        usage_tracker: Optional[UsageTracker] = None
    ):
        self.config = config or CleanupConfig()
        self.usage_tracker = usage_tracker or UsageTracker(self.config.stats_file_path)

    def get_stale_worktrees(
        self,
        worktree_paths: List[str],
        threshold_days: Optional[int] = None
    ) -> List[WorktreeUsageStats]:
        """
        Identify worktrees that are considered stale.

        Args:
            worktree_paths: List of worktree paths to check
            threshold_days: Override default stale threshold

        Returns:
            List of WorktreeUsageStats for stale worktrees
        """
        threshold = threshold_days or self.config.stale_threshold_days
        cutoff_date = datetime.now() - timedelta(days=threshold)
        stale_worktrees = []

        for path in worktree_paths:
            stats = self._get_worktree_stats(path)

            if stats and stats.last_accessed < cutoff_date:
                stale_worktrees.append(stats)

        return stale_worktrees

    def _get_worktree_stats(self, worktree_path: str) -> Optional[WorktreeUsageStats]:
        """Get comprehensive stats for a worktree."""
        path = Path(worktree_path)

        if not path.exists():
            return None

        usage_data = self.usage_tracker.get_stats(worktree_path)

        if not usage_data:
            stat_info = path.stat()
            created_at = datetime.fromtimestamp(stat_info.st_ctime)
            last_accessed = datetime.fromtimestamp(stat_info.st_atime)
            access_count = 0
            branch_name = self._get_branch_name(path)
        else:
            created_at = datetime.fromisoformat(usage_data["created_at"])
            last_accessed = datetime.fromisoformat(usage_data["last_accessed"])
            access_count = usage_data.get("access_count", 0)
            branch_name = usage_data.get("branch_name", self._get_branch_name(path))

        has_uncommitted = self._has_uncommitted_changes(path)
        has_unpushed = self._has_unpushed_commits(path)
        last_commit = self._get_last_commit_date(path)

        return WorktreeUsageStats(
            worktree_path=str(path),
            branch_name=branch_name,
            created_at=created_at,
            last_accessed=last_accessed,
            access_count=access_count,
            last_commit_date=last_commit,
            has_uncommitted_changes=has_uncommitted,
            has_unpushed_commits=has_unpushed
        )

    def _get_branch_name(self, worktree_path: Path) -> str:
        """Get the branch name for a worktree."""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    def _has_uncommitted_changes(self, worktree_path: Path) -> bool:
        """Check if worktree has uncommitted changes."""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            return bool(result.stdout.strip())
        except Exception:
            return True

    def _has_unpushed_commits(self, worktree_path: Path) -> bool:
        """Check if worktree has unpushed commits."""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "log", "@{u}..", "--oneline"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            return bool(result.stdout.strip())
        except Exception:
            return True

    def _get_last_commit_date(self, worktree_path: Path) -> Optional[datetime]:
        """Get the date of the last commit in the worktree."""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "log", "-1", "--format=%ci"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.stdout.strip():
                date_str = result.stdout.strip()
                return datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")

            return None
        except Exception:
            return None

    def should_protect_worktree(self, stats: WorktreeUsageStats) -> tuple[bool, str]:
        """
        Determine if a worktree should be protected from cleanup.

        Returns:
            Tuple of (should_protect, reason)
        """
        if self.config.protect_uncommitted and stats.has_uncommitted_changes:
            return True, "has uncommitted changes"

        if self.config.protect_unpushed and stats.has_unpushed_commits:
            return True, "has unpushed commits"

        return False, ""

    def cleanup(
        self,
        worktree_paths: List[str],
        dry_run: bool = True,
        threshold_days: Optional[int] = None,
        force: bool = False
    ) -> CleanupReport:
        """
        Clean up stale worktrees.

        Args:
            worktree_paths: List of worktree paths to consider for cleanup
            dry_run: If True, don't actually delete anything
            threshold_days: Override default stale threshold
            force: If True, ignore protection rules

        Returns:
            CleanupReport with details of the operation
        """
        threshold = threshold_days or self.config.stale_threshold_days
        stale_worktrees = self.get_stale_worktrees(worktree_paths, threshold)

        report = CleanupReport(
            timestamp=datetime.now(),
            dry_run=dry_run,
            stale_threshold_days=threshold,
            worktrees_scanned=len(worktree_paths),
            stale_worktrees_found=len(stale_worktrees),
            worktrees_cleaned=0,
            worktrees_skipped=0
        )

        for stats in stale_worktrees:
            should_protect, reason = self.should_protect_worktree(stats)

            if should_protect and not force:
                report.worktrees_skipped += 1
                report.skipped_paths.append(f"{stats.worktree_path} ({reason})")
                continue

            if dry_run:
                report.worktrees_cleaned += 1
                report.cleaned_paths.append(stats.worktree_path)
            else:
                try:
                    self._delete_worktree(stats.worktree_path)
                    self.usage_tracker.remove_stats(stats.worktree_path)
                    report.worktrees_cleaned += 1
                    report.cleaned_paths.append(stats.worktree_path)
                except Exception as e:
                    report.errors.append(f"Failed to delete {stats.worktree_path}: {e}")

        return report

    def _delete_worktree(self, worktree_path: str) -> None:
        """Delete a worktree using git worktree remove."""
        import subprocess

        result = subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise RuntimeError(f"git worktree remove failed: {result.stderr}")

    def get_usage_report(self, worktree_paths: List[str]) -> List[WorktreeUsageStats]:
        """Generate a usage report for all worktrees."""
        stats_list = []

        for path in worktree_paths:
            stats = self._get_worktree_stats(path)

            if stats:
                stats_list.append(stats)

        return sorted(stats_list, key=lambda s: s.last_accessed)

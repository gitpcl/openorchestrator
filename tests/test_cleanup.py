"""
Unit tests for the CleanupService.

Tests cover:
- Stale worktree detection
- Cleanup with dry-run support
- Protection rules for uncommitted/unpushed changes
- Usage statistics tracking
"""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_orchestrator.core.cleanup import (
    CleanupConfig,
    CleanupReport,
    CleanupService,
    UsageTracker,
    WorktreeUsageStats,
)


class TestCleanupConfig:
    """Tests for CleanupConfig dataclass."""

    def test_default_values(self):
        config = CleanupConfig()

        assert config.stale_threshold_days == 14
        assert config.protect_uncommitted is True
        assert config.protect_unpushed is True
        assert config.stats_file_path is None

    def test_custom_values(self):
        config = CleanupConfig(
            stale_threshold_days=7,
            protect_uncommitted=False,
            protect_unpushed=False
        )

        assert config.stale_threshold_days == 7
        assert config.protect_uncommitted is False
        assert config.protect_unpushed is False

    def test_invalid_threshold_raises_error(self):
        with pytest.raises(ValueError, match="stale_threshold_days must be at least 1"):
            CleanupConfig(stale_threshold_days=0)

        with pytest.raises(ValueError, match="stale_threshold_days must be at least 1"):
            CleanupConfig(stale_threshold_days=-1)


class TestUsageTracker:
    """Tests for UsageTracker class."""

    @pytest.fixture
    def temp_stats_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            yield Path(f.name)

    @pytest.fixture
    def tracker(self, temp_stats_file):
        return UsageTracker(stats_file_path=temp_stats_file)

    def test_record_access_creates_new_entry(self, tracker):
        tracker.record_access("/path/to/worktree", "feature/test")

        stats = tracker.get_stats("/path/to/worktree")

        assert stats is not None
        assert stats["branch_name"] == "feature/test"
        assert stats["access_count"] == 1
        assert "created_at" in stats
        assert "last_accessed" in stats

    def test_record_access_updates_existing_entry(self, tracker):
        tracker.record_access("/path/to/worktree", "feature/test")
        tracker.record_access("/path/to/worktree", "feature/test")

        stats = tracker.get_stats("/path/to/worktree")

        assert stats["access_count"] == 2

    def test_get_stats_returns_none_for_unknown_path(self, tracker):
        stats = tracker.get_stats("/unknown/path")

        assert stats is None

    def test_get_all_stats_returns_copy(self, tracker):
        tracker.record_access("/path/one", "branch-one")
        tracker.record_access("/path/two", "branch-two")

        all_stats = tracker.get_all_stats()

        assert len(all_stats) == 2
        assert "/path/one" in all_stats
        assert "/path/two" in all_stats

    def test_remove_stats_deletes_entry(self, tracker):
        tracker.record_access("/path/to/worktree", "feature/test")
        tracker.remove_stats("/path/to/worktree")

        stats = tracker.get_stats("/path/to/worktree")

        assert stats is None

    def test_get_last_accessed_returns_datetime(self, tracker):
        tracker.record_access("/path/to/worktree", "feature/test")

        last_accessed = tracker.get_last_accessed("/path/to/worktree")

        assert last_accessed is not None
        assert isinstance(last_accessed, datetime)

    def test_get_last_accessed_returns_none_for_unknown(self, tracker):
        last_accessed = tracker.get_last_accessed("/unknown/path")

        assert last_accessed is None

    def test_stats_persist_to_file(self, temp_stats_file):
        tracker1 = UsageTracker(stats_file_path=temp_stats_file)
        tracker1.record_access("/path/to/worktree", "feature/test")

        tracker2 = UsageTracker(stats_file_path=temp_stats_file)
        stats = tracker2.get_stats("/path/to/worktree")

        assert stats is not None
        assert stats["branch_name"] == "feature/test"


class TestCleanupService:
    """Tests for CleanupService class."""

    @pytest.fixture
    def mock_tracker(self):
        return MagicMock(spec=UsageTracker)

    @pytest.fixture
    def service(self, mock_tracker):
        config = CleanupConfig(stale_threshold_days=14)
        return CleanupService(config=config, usage_tracker=mock_tracker)

    def test_get_stale_worktrees_identifies_old_worktrees(self, service, mock_tracker):
        old_date = datetime.now() - timedelta(days=20)
        recent_date = datetime.now() - timedelta(days=5)

        mock_tracker.get_stats.side_effect = lambda path: {
            "branch_name": "test",
            "created_at": old_date.isoformat(),
            "last_accessed": old_date.isoformat() if "old" in path else recent_date.isoformat(),
            "access_count": 1
        }

        with patch.object(service, '_get_worktree_stats') as mock_stats:
            mock_stats.side_effect = lambda path: WorktreeUsageStats(
                worktree_path=path,
                branch_name="test",
                created_at=old_date,
                last_accessed=old_date if "old" in path else recent_date,
                access_count=1
            ) if Path(path).exists() or True else None

            with patch('pathlib.Path.exists', return_value=True):
                stale = service.get_stale_worktrees(
                    ["/path/old-worktree", "/path/recent-worktree"],
                    threshold_days=14
                )

        assert len(stale) == 1
        assert stale[0].worktree_path == "/path/old-worktree"

    def test_should_protect_worktree_with_uncommitted_changes(self, service):
        stats = WorktreeUsageStats(
            worktree_path="/path/to/worktree",
            branch_name="test",
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            has_uncommitted_changes=True
        )

        should_protect, reason = service.should_protect_worktree(stats)

        assert should_protect is True
        assert "uncommitted changes" in reason

    def test_should_protect_worktree_with_unpushed_commits(self, service):
        stats = WorktreeUsageStats(
            worktree_path="/path/to/worktree",
            branch_name="test",
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            has_unpushed_commits=True
        )

        should_protect, reason = service.should_protect_worktree(stats)

        assert should_protect is True
        assert "unpushed commits" in reason

    def test_should_not_protect_clean_worktree(self, service):
        stats = WorktreeUsageStats(
            worktree_path="/path/to/worktree",
            branch_name="test",
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            has_uncommitted_changes=False,
            has_unpushed_commits=False
        )

        should_protect, reason = service.should_protect_worktree(stats)

        assert should_protect is False
        assert reason == ""

    def test_cleanup_dry_run_does_not_delete(self, service, mock_tracker):
        old_date = datetime.now() - timedelta(days=20)

        with patch.object(service, 'get_stale_worktrees') as mock_stale:
            mock_stale.return_value = [
                WorktreeUsageStats(
                    worktree_path="/path/stale-worktree",
                    branch_name="test",
                    created_at=old_date,
                    last_accessed=old_date,
                    has_uncommitted_changes=False,
                    has_unpushed_commits=False
                )
            ]

            with patch.object(service, '_delete_worktree') as mock_delete:
                report = service.cleanup(["/path/stale-worktree"], dry_run=True)

        mock_delete.assert_not_called()
        assert report.dry_run is True
        assert report.worktrees_cleaned == 1
        assert "/path/stale-worktree" in report.cleaned_paths

    def test_cleanup_skips_protected_worktrees(self, service, mock_tracker):
        old_date = datetime.now() - timedelta(days=20)

        with patch.object(service, 'get_stale_worktrees') as mock_stale:
            mock_stale.return_value = [
                WorktreeUsageStats(
                    worktree_path="/path/dirty-worktree",
                    branch_name="test",
                    created_at=old_date,
                    last_accessed=old_date,
                    has_uncommitted_changes=True,
                    has_unpushed_commits=False
                )
            ]

            report = service.cleanup(["/path/dirty-worktree"], dry_run=True)

        assert report.worktrees_skipped == 1
        assert any("uncommitted" in p for p in report.skipped_paths)

    def test_cleanup_force_ignores_protection(self, service, mock_tracker):
        old_date = datetime.now() - timedelta(days=20)

        with patch.object(service, 'get_stale_worktrees') as mock_stale:
            mock_stale.return_value = [
                WorktreeUsageStats(
                    worktree_path="/path/dirty-worktree",
                    branch_name="test",
                    created_at=old_date,
                    last_accessed=old_date,
                    has_uncommitted_changes=True,
                    has_unpushed_commits=False
                )
            ]

            report = service.cleanup(
                ["/path/dirty-worktree"],
                dry_run=True,
                force=True
            )

        assert report.worktrees_cleaned == 1
        assert report.worktrees_skipped == 0

    def test_cleanup_report_structure(self, service, mock_tracker):
        with patch.object(service, 'get_stale_worktrees') as mock_stale:
            mock_stale.return_value = []

            report = service.cleanup(["/path/worktree"], dry_run=True)

        assert isinstance(report, CleanupReport)
        assert isinstance(report.timestamp, datetime)
        assert report.dry_run is True
        assert report.stale_threshold_days == 14
        assert report.worktrees_scanned == 1

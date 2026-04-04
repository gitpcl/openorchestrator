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

from open_orchestrator.core.cleanup import (
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
        config = CleanupConfig(stale_threshold_days=7, protect_uncommitted=False, protect_unpushed=False)

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
            "access_count": 1,
        }

        with patch.object(service, "_get_worktree_stats") as mock_stats:
            mock_stats.side_effect = lambda path: (
                WorktreeUsageStats(
                    worktree_path=path,
                    branch_name="test",
                    created_at=old_date,
                    last_accessed=old_date if "old" in path else recent_date,
                    access_count=1,
                )
                if Path(path).exists() or True
                else None
            )

            with patch("pathlib.Path.exists", return_value=True):
                stale = service.get_stale_worktrees(["/path/old-worktree", "/path/recent-worktree"], threshold_days=14)

        assert len(stale) == 1
        assert stale[0].worktree_path == "/path/old-worktree"

    def test_should_protect_worktree_with_uncommitted_changes(self, service):
        stats = WorktreeUsageStats(
            worktree_path="/path/to/worktree",
            branch_name="test",
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            has_uncommitted_changes=True,
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
            has_unpushed_commits=True,
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
            has_unpushed_commits=False,
        )

        should_protect, reason = service.should_protect_worktree(stats)

        assert should_protect is False
        assert reason == ""

    def test_cleanup_dry_run_does_not_delete(self, service, mock_tracker):
        old_date = datetime.now() - timedelta(days=20)

        with patch.object(service, "get_stale_worktrees") as mock_stale:
            mock_stale.return_value = [
                WorktreeUsageStats(
                    worktree_path="/path/stale-worktree",
                    branch_name="test",
                    created_at=old_date,
                    last_accessed=old_date,
                    has_uncommitted_changes=False,
                    has_unpushed_commits=False,
                )
            ]

            with patch.object(service, "_delete_worktree") as mock_delete:
                report = service.cleanup(["/path/stale-worktree"], dry_run=True)

        mock_delete.assert_not_called()
        assert report.dry_run is True
        assert report.worktrees_cleaned == 1
        assert "/path/stale-worktree" in report.cleaned_paths

    def test_cleanup_skips_protected_worktrees(self, service, mock_tracker):
        old_date = datetime.now() - timedelta(days=20)

        with patch.object(service, "get_stale_worktrees") as mock_stale:
            mock_stale.return_value = [
                WorktreeUsageStats(
                    worktree_path="/path/dirty-worktree",
                    branch_name="test",
                    created_at=old_date,
                    last_accessed=old_date,
                    has_uncommitted_changes=True,
                    has_unpushed_commits=False,
                )
            ]

            report = service.cleanup(["/path/dirty-worktree"], dry_run=True)

        assert report.worktrees_skipped == 1
        assert any("uncommitted" in p for p in report.skipped_paths)

    def test_cleanup_force_ignores_protection(self, service, mock_tracker):
        old_date = datetime.now() - timedelta(days=20)

        with patch.object(service, "get_stale_worktrees") as mock_stale:
            mock_stale.return_value = [
                WorktreeUsageStats(
                    worktree_path="/path/dirty-worktree",
                    branch_name="test",
                    created_at=old_date,
                    last_accessed=old_date,
                    has_uncommitted_changes=True,
                    has_unpushed_commits=False,
                )
            ]

            report = service.cleanup(["/path/dirty-worktree"], dry_run=True, force=True)

        assert report.worktrees_cleaned == 1
        assert report.worktrees_skipped == 0

    def test_cleanup_force_deletes_dirty_worktrees(self, service, mock_tracker):
        old_date = datetime.now() - timedelta(days=20)

        with patch.object(service, "get_stale_worktrees") as mock_stale:
            mock_stale.return_value = [
                WorktreeUsageStats(
                    worktree_path="/path/dirty-worktree",
                    branch_name="test",
                    created_at=old_date,
                    last_accessed=old_date,
                    has_uncommitted_changes=True,
                    has_unpushed_commits=False,
                )
            ]

            with patch.object(service, "_delete_worktree") as mock_delete:
                report = service.cleanup(["/path/dirty-worktree"], dry_run=False, force=True)

        # force=True should bypass protection AND actually delete
        mock_delete.assert_called_once_with("/path/dirty-worktree", force=True)
        assert report.worktrees_cleaned == 1
        assert report.worktrees_skipped == 0

    def test_cleanup_report_structure(self, service, mock_tracker):
        with patch.object(service, "get_stale_worktrees") as mock_stale:
            mock_stale.return_value = []

            report = service.cleanup(["/path/worktree"], dry_run=True)

        assert isinstance(report, CleanupReport)
        assert isinstance(report.timestamp, datetime)
        assert report.dry_run is True
        assert report.stale_threshold_days == 14
        assert report.worktrees_scanned == 1


class TestUsageTrackerEdgeCases:
    """Tests for edge cases in UsageTracker."""

    def test_get_default_stats_path_returns_path_in_home(self):
        tracker = UsageTracker.__new__(UsageTracker)
        path = tracker._get_default_stats_path()
        assert path == Path.home() / ".open-orchestrator" / UsageTracker.DEFAULT_STATS_FILENAME

    def test_load_stats_handles_missing_file(self, tmp_path):
        nonexistent = tmp_path / "does_not_exist.json"
        tracker = UsageTracker(stats_file_path=nonexistent)
        assert tracker._usage_data == {}

    def test_load_stats_handles_corrupted_json(self, tmp_path):
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{not valid json")
        tracker = UsageTracker(stats_file_path=bad_json)
        assert tracker._usage_data == {}

    def test_load_stats_handles_os_error(self, tmp_path):
        stats_file = tmp_path / "stats.json"
        stats_file.write_text(json.dumps({"key": "val"}))
        tracker = UsageTracker.__new__(UsageTracker)
        tracker._stats_file = stats_file
        tracker._usage_data = {}
        with patch("builtins.open", side_effect=OSError("permission denied")):
            tracker._load_stats()
        assert tracker._usage_data == {}

    def test_remove_stats_noop_for_missing_key(self, tmp_path):
        tracker = UsageTracker(stats_file_path=tmp_path / "stats.json")
        # Should not raise and should not call _save_stats
        with patch.object(tracker, "_save_stats") as mock_save:
            tracker.remove_stats("/nonexistent/path")
        mock_save.assert_not_called()


class TestGetWorktreeStats:
    """Tests for CleanupService._get_worktree_stats."""

    @pytest.fixture
    def service(self, tmp_path):
        mock_tracker = MagicMock(spec=UsageTracker)
        config = CleanupConfig(stale_threshold_days=14)
        svc = CleanupService(config=config, usage_tracker=mock_tracker)
        return svc, mock_tracker, tmp_path

    def test_returns_none_when_path_does_not_exist(self, service):
        svc, mock_tracker, tmp_path = service
        result = svc._get_worktree_stats("/nonexistent/path/99999")
        assert result is None

    def test_uses_filesystem_stats_when_no_usage_data(self, service, tmp_path):
        svc, mock_tracker, _ = service
        worktree_dir = tmp_path / "my-worktree"
        worktree_dir.mkdir()
        mock_tracker.get_stats.return_value = None

        with (
            patch.object(svc, "_get_branch_name", return_value="feat/test"),
            patch.object(svc, "_has_uncommitted_changes", return_value=False),
            patch.object(svc, "_has_unpushed_commits", return_value=False),
            patch.object(svc, "_get_last_commit_date", return_value=None),
        ):
            result = svc._get_worktree_stats(str(worktree_dir))

        assert result is not None
        assert result.branch_name == "feat/test"
        assert result.access_count == 0

    def test_uses_usage_data_when_available(self, service, tmp_path):
        svc, mock_tracker, _ = service
        worktree_dir = tmp_path / "tracked-worktree"
        worktree_dir.mkdir()
        now = datetime.now()
        mock_tracker.get_stats.return_value = {
            "branch_name": "feat/tracked",
            "created_at": now.isoformat(),
            "last_accessed": now.isoformat(),
            "access_count": 5,
        }

        with (
            patch.object(svc, "_get_branch_name", return_value="feat/tracked"),
            patch.object(svc, "_has_uncommitted_changes", return_value=False),
            patch.object(svc, "_has_unpushed_commits", return_value=False),
            patch.object(svc, "_get_last_commit_date", return_value=None),
        ):
            result = svc._get_worktree_stats(str(worktree_dir))

        assert result is not None
        assert result.branch_name == "feat/tracked"
        assert result.access_count == 5

    def test_uses_branch_from_git_when_not_in_usage_data(self, service, tmp_path):
        svc, mock_tracker, _ = service
        worktree_dir = tmp_path / "fallback-worktree"
        worktree_dir.mkdir()
        now = datetime.now()
        mock_tracker.get_stats.return_value = {
            "created_at": now.isoformat(),
            "last_accessed": now.isoformat(),
            # no branch_name key
        }

        with (
            patch.object(svc, "_get_branch_name", return_value="feat/fallback"),
            patch.object(svc, "_has_uncommitted_changes", return_value=False),
            patch.object(svc, "_has_unpushed_commits", return_value=False),
            patch.object(svc, "_get_last_commit_date", return_value=None),
        ):
            result = svc._get_worktree_stats(str(worktree_dir))

        assert result is not None
        assert result.branch_name == "feat/fallback"

    def test_returns_stats_with_uncommitted_and_unpushed(self, service, tmp_path):
        svc, mock_tracker, _ = service
        worktree_dir = tmp_path / "dirty-worktree"
        worktree_dir.mkdir()
        mock_tracker.get_stats.return_value = None

        with (
            patch.object(svc, "_get_branch_name", return_value="feat/dirty"),
            patch.object(svc, "_has_uncommitted_changes", return_value=True),
            patch.object(svc, "_has_unpushed_commits", return_value=True),
            patch.object(svc, "_get_last_commit_date", return_value=None),
        ):
            result = svc._get_worktree_stats(str(worktree_dir))

        assert result is not None
        assert result.has_uncommitted_changes is True
        assert result.has_unpushed_commits is True


class TestSubprocessHelpers:
    """Tests for subprocess-based helper methods."""

    @pytest.fixture
    def service(self, tmp_path):
        mock_tracker = MagicMock(spec=UsageTracker)
        config = CleanupConfig(stale_threshold_days=14)
        return CleanupService(config=config, usage_tracker=mock_tracker)

    def test_get_branch_name_returns_stdout(self, service, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="feat/mybranch\n", returncode=0)
            result = service._get_branch_name(tmp_path)
        assert result == "feat/mybranch"

    def test_get_branch_name_returns_unknown_on_empty(self, service, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = service._get_branch_name(tmp_path)
        assert result == "unknown"

    def test_get_branch_name_returns_unknown_on_exception(self, service, tmp_path):
        with patch("subprocess.run", side_effect=Exception("git not found")):
            result = service._get_branch_name(tmp_path)
        assert result == "unknown"

    def test_has_uncommitted_changes_true_when_output(self, service, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=" M dirty.txt\n", returncode=0)
            result = service._has_uncommitted_changes(tmp_path)
        assert result is True

    def test_has_uncommitted_changes_false_when_clean(self, service, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = service._has_uncommitted_changes(tmp_path)
        assert result is False

    def test_has_uncommitted_changes_true_on_exception(self, service, tmp_path):
        with patch("subprocess.run", side_effect=Exception("timeout")):
            result = service._has_uncommitted_changes(tmp_path)
        assert result is True

    def test_has_unpushed_commits_true_when_output(self, service, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="abc123 Add feature\n", returncode=0)
            result = service._has_unpushed_commits(tmp_path)
        assert result is True

    def test_has_unpushed_commits_false_when_empty(self, service, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = service._has_unpushed_commits(tmp_path)
        assert result is False

    def test_has_unpushed_commits_true_on_exception(self, service, tmp_path):
        with patch("subprocess.run", side_effect=Exception("remote not found")):
            result = service._has_unpushed_commits(tmp_path)
        assert result is True

    def test_get_last_commit_date_parses_date(self, service, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="2024-01-15 10:30:00 +0100\n", returncode=0)
            result = service._get_last_commit_date(tmp_path)
        assert result == datetime(2024, 1, 15, 10, 30, 0)

    def test_get_last_commit_date_returns_none_on_empty(self, service, tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = service._get_last_commit_date(tmp_path)
        assert result is None

    def test_get_last_commit_date_returns_none_on_exception(self, service, tmp_path):
        with patch("subprocess.run", side_effect=Exception("error")):
            result = service._get_last_commit_date(tmp_path)
        assert result is None


class TestCleanupActualDelete:
    """Tests for the non-dry-run delete path and _delete_worktree."""

    @pytest.fixture
    def service(self, tmp_path):
        mock_tracker = MagicMock(spec=UsageTracker)
        config = CleanupConfig(stale_threshold_days=14)
        return CleanupService(config=config, usage_tracker=mock_tracker), mock_tracker

    def test_cleanup_non_dry_run_calls_delete_and_tracks_removal(self, service):
        svc, mock_tracker = service
        old_date = datetime.now() - timedelta(days=20)

        stale = WorktreeUsageStats(
            worktree_path="/path/stale",
            branch_name="test",
            created_at=old_date,
            last_accessed=old_date,
            has_uncommitted_changes=False,
            has_unpushed_commits=False,
        )

        with patch.object(svc, "get_stale_worktrees", return_value=[stale]), patch.object(svc, "_delete_worktree") as mock_delete:
            report = svc.cleanup(["/path/stale"], dry_run=False)

        mock_delete.assert_called_once_with("/path/stale", force=False)
        mock_tracker.remove_stats.assert_called_once_with("/path/stale")
        assert report.worktrees_cleaned == 1
        assert "/path/stale" in report.cleaned_paths

    def test_cleanup_non_dry_run_records_error_on_delete_failure(self, service):
        svc, mock_tracker = service
        old_date = datetime.now() - timedelta(days=20)

        stale = WorktreeUsageStats(
            worktree_path="/path/error",
            branch_name="test",
            created_at=old_date,
            last_accessed=old_date,
            has_uncommitted_changes=False,
            has_unpushed_commits=False,
        )

        with (
            patch.object(svc, "get_stale_worktrees", return_value=[stale]),
            patch.object(svc, "_delete_worktree", side_effect=RuntimeError("git error")),
        ):
            report = svc.cleanup(["/path/error"], dry_run=False)

        assert report.worktrees_cleaned == 0
        assert len(report.errors) == 1
        assert "Failed to delete" in report.errors[0]

    def test_delete_worktree_raises_on_git_error(self, service, tmp_path):
        svc, _ = service
        with patch("open_orchestrator.core.pane_actions.teardown_worktree", return_value=["git worktree remove failed"]):
            with pytest.raises(RuntimeError, match="git worktree"):
                svc._delete_worktree(str(tmp_path / "my-worktree"))

    def test_delete_worktree_succeeds_on_no_git_errors(self, service, tmp_path):
        svc, _ = service
        with patch("open_orchestrator.core.pane_actions.teardown_worktree", return_value=[]) as mock_td:
            # Should not raise
            svc._delete_worktree(str(tmp_path / "my-worktree"))
        mock_td.assert_called_once()

    def test_delete_worktree_ignores_non_git_errors(self, service, tmp_path):
        svc, _ = service
        with patch("open_orchestrator.core.pane_actions.teardown_worktree", return_value=["tmux session not found"]):
            # Non-git errors should not raise
            svc._delete_worktree(str(tmp_path / "my-worktree"))


class TestGetUsageReport:
    """Tests for CleanupService.get_usage_report."""

    @pytest.fixture
    def service(self):
        mock_tracker = MagicMock(spec=UsageTracker)
        config = CleanupConfig(stale_threshold_days=14)
        return CleanupService(config=config, usage_tracker=mock_tracker)

    def test_returns_empty_list_when_no_paths(self, service):
        result = service.get_usage_report([])
        assert result == []

    def test_returns_sorted_by_last_accessed(self, service):
        old_date = datetime.now() - timedelta(days=10)
        recent_date = datetime.now() - timedelta(days=1)

        old_stats = WorktreeUsageStats(
            worktree_path="/old",
            branch_name="old",
            created_at=old_date,
            last_accessed=old_date,
        )
        recent_stats = WorktreeUsageStats(
            worktree_path="/recent",
            branch_name="recent",
            created_at=recent_date,
            last_accessed=recent_date,
        )

        with patch.object(service, "_get_worktree_stats") as mock_stats:
            mock_stats.side_effect = lambda p: old_stats if "old" in p else recent_stats
            result = service.get_usage_report(["/recent", "/old"])

        # Sorted by last_accessed ascending (oldest first)
        assert result[0].worktree_path == "/old"
        assert result[1].worktree_path == "/recent"

    def test_skips_paths_that_return_none_stats(self, service):
        with patch.object(service, "_get_worktree_stats", return_value=None):
            result = service.get_usage_report(["/nonexistent1", "/nonexistent2"])
        assert result == []


class TestCleanupCLIJsonOutput:
    """Test JSON output for 'owt cleanup --json' command.

    The CLI calls service.cleanup() and outputs report.model_dump(mode='json').
    The output is a CleanupReport Pydantic model dump.
    """

    @pytest.fixture
    def cli_runner(self):
        from click.testing import CliRunner

        return CliRunner()

    @patch("open_orchestrator.core.cleanup.CleanupService")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_cleanup_json_output_with_no_worktrees(
        self,
        mock_get_wt_manager: MagicMock,
        mock_cleanup_cls: MagicMock,
        cli_runner,
    ) -> None:
        """Test --json output when no worktrees exist."""
        import json

        from open_orchestrator.cli import main
        from open_orchestrator.core.cleanup import CleanupReport

        mock_get_wt_manager.return_value.list_all.return_value = []
        mock_cleanup_cls.return_value.cleanup.return_value = CleanupReport(
            timestamp=datetime.now(),
            dry_run=True,
            stale_threshold_days=14,
            worktrees_scanned=0,
            stale_worktrees_found=0,
            worktrees_cleaned=0,
            worktrees_skipped=0,
        )

        result = cli_runner.invoke(main, ["cleanup", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["stale_worktrees_found"] == 0
        assert output["dry_run"] is True
        assert output["stale_threshold_days"] == 14

    @patch("open_orchestrator.core.cleanup.CleanupService")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_cleanup_json_output_with_stale_worktrees_dry_run(
        self,
        mock_get_wt_manager: MagicMock,
        mock_cleanup_cls: MagicMock,
        cli_runner,
        temp_directory: Path,
    ) -> None:
        """Test --json output with stale worktrees in dry-run mode."""
        import json

        from open_orchestrator.cli import main
        from open_orchestrator.core.cleanup import CleanupReport
        from open_orchestrator.models.worktree_info import WorktreeInfo

        mock_worktree = WorktreeInfo(
            path=temp_directory / "test-worktree",
            branch="feature/test",
            head_commit="abc123f",
            is_bare=False,
            is_detached=False,
            is_locked=False,
            lock_reason=None,
            prunable=None,
        )
        mock_get_wt_manager.return_value.list_all.return_value = [mock_worktree]
        mock_cleanup_cls.return_value.cleanup.return_value = CleanupReport(
            timestamp=datetime.now(),
            dry_run=True,
            stale_threshold_days=14,
            worktrees_scanned=1,
            stale_worktrees_found=1,
            worktrees_cleaned=0,
            worktrees_skipped=1,
            skipped_paths=[str(temp_directory / "test-worktree")],
        )

        result = cli_runner.invoke(main, ["cleanup", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["stale_worktrees_found"] == 1
        assert output["dry_run"] is True
        assert output["stale_threshold_days"] == 14
        assert len(output["skipped_paths"]) == 1

    @patch("open_orchestrator.core.cleanup.CleanupService")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_cleanup_json_output_with_force_clean(
        self,
        mock_get_wt_manager: MagicMock,
        mock_cleanup_cls: MagicMock,
        cli_runner,
        temp_directory: Path,
    ) -> None:
        """Test --json output with --force actually cleaning worktrees."""
        import json

        from open_orchestrator.cli import main
        from open_orchestrator.core.cleanup import CleanupReport
        from open_orchestrator.models.worktree_info import WorktreeInfo

        mock_worktree = WorktreeInfo(
            path=temp_directory / "test-worktree",
            branch="feature/test",
            head_commit="abc123f",
            is_bare=False,
            is_detached=False,
            is_locked=False,
            lock_reason=None,
            prunable=None,
        )
        mock_get_wt_manager.return_value.list_all.return_value = [mock_worktree]
        mock_cleanup_cls.return_value.cleanup.return_value = CleanupReport(
            timestamp=datetime.now(),
            dry_run=False,
            stale_threshold_days=14,
            worktrees_scanned=1,
            stale_worktrees_found=1,
            worktrees_cleaned=1,
            worktrees_skipped=0,
            cleaned_paths=[str(temp_directory / "test-worktree")],
        )

        result = cli_runner.invoke(main, ["cleanup", "--json", "--force"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["dry_run"] is False
        assert output["worktrees_cleaned"] == 1
        assert len(output["cleaned_paths"]) == 1

    @patch("open_orchestrator.core.cleanup.CleanupService")
    @patch("open_orchestrator.commands._shared.WorktreeManager")
    def test_cleanup_json_output_validates_parseable(
        self,
        mock_get_wt_manager: MagicMock,
        mock_cleanup_cls: MagicMock,
        cli_runner,
    ) -> None:
        """Test --json output is valid JSON with expected schema."""
        import json

        from open_orchestrator.cli import main
        from open_orchestrator.core.cleanup import CleanupReport

        mock_get_wt_manager.return_value.list_all.return_value = []
        mock_cleanup_cls.return_value.cleanup.return_value = CleanupReport(
            timestamp=datetime.now(),
            dry_run=True,
            stale_threshold_days=7,
            worktrees_scanned=0,
            stale_worktrees_found=0,
            worktrees_cleaned=0,
            worktrees_skipped=0,
        )

        result = cli_runner.invoke(main, ["cleanup", "--json", "--days", "7"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert isinstance(output, dict)
        assert isinstance(output["stale_threshold_days"], int)
        assert output["stale_threshold_days"] == 7
        assert isinstance(output["dry_run"], bool)

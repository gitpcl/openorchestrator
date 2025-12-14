"""
Unit tests for the SyncService.

Tests cover:
- Single worktree sync
- Sync all worktrees
- Upstream tracking detection
- Handling of uncommitted changes with auto-stash
- Error handling for various edge cases
"""

import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from claude_orchestrator.core.sync import (
    SyncConfig,
    SyncReport,
    SyncService,
    SyncStatus,
    WorktreeSyncResult,
)


class TestSyncConfig:
    """Tests for SyncConfig dataclass."""

    def test_default_values(self):
        config = SyncConfig()

        assert config.strategy == "merge"
        assert config.auto_stash is True
        assert config.prune_remote is True
        assert config.fetch_all is False
        assert config.timeout_seconds == 60

    def test_custom_values(self):
        config = SyncConfig(
            strategy="rebase",
            auto_stash=False,
            prune_remote=False,
            fetch_all=True,
            timeout_seconds=120
        )

        assert config.strategy == "rebase"
        assert config.auto_stash is False
        assert config.prune_remote is False
        assert config.fetch_all is True
        assert config.timeout_seconds == 120


class TestSyncService:
    """Tests for SyncService class."""

    @pytest.fixture
    def service(self):
        return SyncService()

    @pytest.fixture
    def temp_worktree(self, tmp_path):
        worktree_path = tmp_path / "test-worktree"
        worktree_path.mkdir()
        return worktree_path

    def test_sync_worktree_nonexistent_path(self, service):
        result = service.sync_worktree("/nonexistent/path")

        assert result.status == SyncStatus.ERROR
        assert "does not exist" in result.message

    def test_sync_worktree_no_upstream(self, service, temp_worktree):
        with patch.object(service, '_get_current_branch', return_value="main"):
            with patch.object(service, '_get_upstream_branch', return_value=None):
                result = service.sync_worktree(str(temp_worktree))

        assert result.status == SyncStatus.NO_UPSTREAM
        assert result.branch_name == "main"

    def test_sync_worktree_uncommitted_changes_no_auto_stash(self, temp_worktree):
        service = SyncService(config=SyncConfig(auto_stash=False))

        with patch.object(service, '_get_current_branch', return_value="main"):
            with patch.object(service, '_get_upstream_branch', return_value="origin/main"):
                with patch.object(service, '_has_uncommitted_changes', return_value=True):
                    result = service.sync_worktree(str(temp_worktree))

        assert result.status == SyncStatus.UNCOMMITTED_CHANGES
        assert "auto_stash is disabled" in result.message

    def test_sync_worktree_already_up_to_date(self, service, temp_worktree):
        with patch.object(service, '_get_current_branch', return_value="main"):
            with patch.object(service, '_get_upstream_branch', return_value="origin/main"):
                with patch.object(service, '_has_uncommitted_changes', return_value=False):
                    with patch.object(service, '_fetch_upstream'):
                        with patch.object(service, '_get_commit_counts', return_value=(0, 0)):
                            result = service.sync_worktree(str(temp_worktree))

        assert result.status == SyncStatus.UP_TO_DATE
        assert "up to date" in result.message.lower()

    def test_sync_worktree_successful_pull(self, service, temp_worktree):
        mock_pull_result = MagicMock()
        mock_pull_result.returncode = 0

        with patch.object(service, '_get_current_branch', return_value="main"):
            with patch.object(service, '_get_upstream_branch', return_value="origin/main"):
                with patch.object(service, '_has_uncommitted_changes', return_value=False):
                    with patch.object(service, '_fetch_upstream'):
                        with patch.object(service, '_get_commit_counts', return_value=(5, 0)):
                            with patch.object(service, '_pull_changes', return_value=mock_pull_result):
                                result = service.sync_worktree(str(temp_worktree))

        assert result.status == SyncStatus.SUCCESS
        assert result.commits_pulled == 5
        assert "5 commits" in result.message

    def test_sync_worktree_with_conflicts(self, service, temp_worktree):
        mock_pull_result = MagicMock()
        mock_pull_result.returncode = 1
        mock_pull_result.stderr = "CONFLICT (content): Merge conflict in file.txt"

        with patch.object(service, '_get_current_branch', return_value="main"):
            with patch.object(service, '_get_upstream_branch', return_value="origin/main"):
                with patch.object(service, '_has_uncommitted_changes', return_value=False):
                    with patch.object(service, '_fetch_upstream'):
                        with patch.object(service, '_get_commit_counts', return_value=(3, 1)):
                            with patch.object(service, '_pull_changes', return_value=mock_pull_result):
                                result = service.sync_worktree(str(temp_worktree))

        assert result.status == SyncStatus.CONFLICTS
        assert "conflict" in result.message.lower()

    def test_sync_worktree_with_auto_stash(self, service, temp_worktree):
        mock_pull_result = MagicMock()
        mock_pull_result.returncode = 0

        mock_stash_pop_result = MagicMock()
        mock_stash_pop_result.returncode = 0

        stash_called = False

        def mock_git_command(path, args):
            nonlocal stash_called
            if args[0] == "stash" and args[1] == "push":
                stash_called = True
            return mock_stash_pop_result

        with patch.object(service, '_get_current_branch', return_value="main"):
            with patch.object(service, '_get_upstream_branch', return_value="origin/main"):
                # Return True for both calls to _has_uncommitted_changes:
                # 1. First call (line 109): checks if we should return early (we don't because auto_stash=True)
                # 2. Second call (line 121): checks if we should stash (we do)
                with patch.object(service, '_has_uncommitted_changes', return_value=True):
                    with patch.object(service, '_fetch_upstream'):
                        with patch.object(service, '_get_commit_counts', return_value=(2, 0)):
                            with patch.object(service, '_pull_changes', return_value=mock_pull_result):
                                with patch.object(service, '_run_git_command', side_effect=mock_git_command):
                                    result = service.sync_worktree(str(temp_worktree))

        assert result.status == SyncStatus.SUCCESS
        assert stash_called is True

    def test_sync_all_aggregates_results(self, service):
        with patch.object(service, 'sync_worktree') as mock_sync:
            mock_sync.side_effect = [
                WorktreeSyncResult(
                    worktree_path="/path/one",
                    branch_name="main",
                    status=SyncStatus.SUCCESS,
                    message="Success",
                    commits_pulled=3
                ),
                WorktreeSyncResult(
                    worktree_path="/path/two",
                    branch_name="develop",
                    status=SyncStatus.UP_TO_DATE,
                    message="Up to date"
                ),
                WorktreeSyncResult(
                    worktree_path="/path/three",
                    branch_name="feature",
                    status=SyncStatus.ERROR,
                    message="Error"
                )
            ]

            report = service.sync_all(["/path/one", "/path/two", "/path/three"])

        assert report.worktrees_synced == 3
        assert report.successful == 1
        assert report.up_to_date == 1
        assert report.failed == 1
        assert len(report.results) == 3

    def test_sync_report_structure(self, service):
        with patch.object(service, 'sync_worktree') as mock_sync:
            mock_sync.return_value = WorktreeSyncResult(
                worktree_path="/path",
                branch_name="main",
                status=SyncStatus.SUCCESS,
                message="OK"
            )

            report = service.sync_all(["/path"])

        assert isinstance(report, SyncReport)
        assert isinstance(report.timestamp, datetime)
        assert isinstance(report.results, list)

    def test_get_sync_status_without_changes(self, service, temp_worktree):
        with patch.object(service, '_get_current_branch', return_value="main"):
            with patch.object(service, '_get_upstream_branch', return_value="origin/main"):
                with patch.object(service, '_fetch_upstream'):
                    with patch.object(service, '_get_commit_counts', return_value=(0, 2)):
                        result = service.get_sync_status(str(temp_worktree))

        assert result.status == SyncStatus.UP_TO_DATE
        assert result.commits_ahead == 2

    def test_setup_upstream_success(self, service, temp_worktree):
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch.object(service, '_get_current_branch', return_value="feature"):
            with patch.object(service, '_run_git_command', return_value=mock_result):
                success = service.setup_upstream(str(temp_worktree))

        assert success is True

    def test_setup_upstream_failure(self, service, temp_worktree):
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch.object(service, '_get_current_branch', return_value="feature"):
            with patch.object(service, '_run_git_command', return_value=mock_result):
                success = service.setup_upstream(str(temp_worktree))

        assert success is False

    def test_setup_upstream_nonexistent_path(self, service):
        success = service.setup_upstream("/nonexistent/path")

        assert success is False


class TestSyncServiceGitCommands:
    """Tests for internal git command methods."""

    @pytest.fixture
    def service(self):
        return SyncService()

    def test_get_current_branch(self, service, tmp_path):
        worktree = tmp_path / "repo"
        worktree.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="feature/test-branch\n"
            )

            branch = service._get_current_branch(worktree)

        assert branch == "feature/test-branch"
        mock_run.assert_called_once()

    def test_get_upstream_branch_exists(self, service, tmp_path):
        worktree = tmp_path / "repo"
        worktree.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="origin/main\n"
            )

            upstream = service._get_upstream_branch(worktree)

        assert upstream == "origin/main"

    def test_get_upstream_branch_not_configured(self, service, tmp_path):
        worktree = tmp_path / "repo"
        worktree.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=128,
                stdout=""
            )

            upstream = service._get_upstream_branch(worktree)

        assert upstream is None

    def test_has_uncommitted_changes_clean(self, service, tmp_path):
        worktree = tmp_path / "repo"
        worktree.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=""
            )

            has_changes = service._has_uncommitted_changes(worktree)

        assert has_changes is False

    def test_has_uncommitted_changes_dirty(self, service, tmp_path):
        worktree = tmp_path / "repo"
        worktree.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=" M file.txt\n?? new_file.txt\n"
            )

            has_changes = service._has_uncommitted_changes(worktree)

        assert has_changes is True

    def test_get_commit_counts(self, service, tmp_path):
        worktree = tmp_path / "repo"
        worktree.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="3\t5\n"
            )

            behind, ahead = service._get_commit_counts(worktree, "origin/main")

        assert behind == 3
        assert ahead == 5

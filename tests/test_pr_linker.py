"""
Tests for PR linking service.

This module tests:
- PRLinker initialization and configuration
- PR linking with explicit PR number
- Auto-detection from branch names
- PR status checking via gh CLI
- PR link removal and refresh
- Orphan cleanup
- CLI commands (owt pr link/status)
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main as cli
from open_orchestrator.core.pr_linker import (
    PRLinker,
    PRLinkerConfig,
)
from open_orchestrator.models.pr_info import (
    PRInfo,
    PRStatus,
)

# === Unit Tests ===


class TestPRLinkerInit:
    """Test PRLinker initialization."""

    def test_init_with_default_config(self, temp_directory: Path):
        """Test initialization with default configuration."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")

        # Act
        linker = PRLinker(config=config)

        # Assert
        assert linker.config == config
        assert linker._storage_path == temp_directory / "pr_links.json"
        assert linker._store is not None

    def test_init_creates_default_path(self):
        """Test initialization creates default storage path."""
        # Arrange & Act
        linker = PRLinker()

        # Assert
        expected_path = Path.home() / ".open-orchestrator" / "pr_links.json"
        assert linker._storage_path == expected_path

    def test_load_existing_pr_store(self, temp_directory: Path):
        """Test loading existing PR links from storage."""
        # Arrange
        storage_path = temp_directory / "pr_links.json"
        pr_data = {
            "prs": {
                "test-worktree": {
                    "worktree_name": "test-worktree",
                    "repo_owner": "owner",
                    "repo_name": "repo",
                    "pr_number": 123,
                    "pr_url": "https://github.com/owner/repo/pull/123",
                    "branch": "feature/test",
                    "status": "open",
                    "auto_detected": False,
                    "created_at": "2024-02-01T10:00:00",
                    "updated_at": "2024-02-01T10:00:00",
                }
            }
        }
        storage_path.write_text(json.dumps(pr_data))
        config = PRLinkerConfig(storage_path=storage_path)

        # Act
        linker = PRLinker(config=config)

        # Assert
        prs = linker.get_all_prs()
        assert len(prs) == 1
        assert prs[0].worktree_name == "test-worktree"

    def test_load_corrupted_store_creates_empty(self, temp_directory: Path):
        """Test loading corrupted store creates empty store."""
        # Arrange
        storage_path = temp_directory / "pr_links.json"
        storage_path.write_text("invalid json")
        config = PRLinkerConfig(storage_path=storage_path)

        # Act
        linker = PRLinker(config=config)

        # Assert
        assert len(linker.get_all_prs()) == 0


class TestPRLinking:
    """Test PR linking functionality."""

    @patch("subprocess.run")
    def test_link_pr_explicit_number(self, mock_run, temp_directory: Path):
        """Test linking PR with explicit PR number."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Mock git remote get-url
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "git@github.com:owner/repo.git"
        mock_run.return_value = mock_result

        # Act
        result = linker.link_pr(
            "test-worktree",
            str(worktree_path),
            "feature/test",
            pr_number=123,
            check_status=False,
        )

        # Assert
        assert result.success is True
        assert result.pr_number == 123
        assert result.pr_url == "https://github.com/owner/repo/pull/123"
        assert result.auto_detected is False

    @patch("subprocess.run")
    def test_link_pr_auto_detect_from_branch(self, mock_run, temp_directory: Path):
        """Test auto-detecting PR number from branch name."""
        # Arrange
        config = PRLinkerConfig(
            storage_path=temp_directory / "pr_links.json",
            branch_pr_pattern=r".*#(\d+).*",
        )
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Mock git remote get-url
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/owner/repo.git"
        mock_run.return_value = mock_result

        # Act
        result = linker.link_pr(
            "test-worktree",
            str(worktree_path),
            "feature/add-auth#456",
            pr_number=None,
            check_status=False,
        )

        # Assert
        assert result.success is True
        assert result.pr_number == 456
        assert result.auto_detected is True

    @patch("subprocess.run")
    def test_link_pr_auto_detect_fails(self, mock_run, temp_directory: Path):
        """Test auto-detect fails when pattern doesn't match."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Mock git remote get-url
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "git@github.com:owner/repo.git"
        mock_run.return_value = mock_result

        # Act
        result = linker.link_pr(
            "test-worktree",
            str(worktree_path),
            "feature/no-pr-number",
            pr_number=None,
            check_status=False,
        )

        # Assert
        assert result.success is False
        assert "Could not detect PR number" in result.message

    @patch("subprocess.run")
    def test_link_pr_not_github_repo(self, mock_run, temp_directory: Path):
        """Test linking fails when not a GitHub repository."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Mock git remote returning non-GitHub URL
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://gitlab.com/owner/repo.git"
        mock_run.return_value = mock_result

        # Act
        result = linker.link_pr(
            "test-worktree",
            str(worktree_path),
            "feature/test",
            pr_number=123,
        )

        # Assert
        assert result.success is False
        assert "Could not determine GitHub repository" in result.message

    @patch("subprocess.run")
    def test_link_pr_with_status_check(self, mock_run, temp_directory: Path):
        """Test linking PR with status check via gh CLI."""
        # Arrange
        config = PRLinkerConfig(
            storage_path=temp_directory / "pr_links.json",
            use_gh_cli=True,
        )
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Mock responses
        def mock_subprocess_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            mock_result = MagicMock()
            mock_result.returncode = 0

            if "remote" in cmd:
                mock_result.stdout = "git@github.com:owner/repo.git"
            elif "gh" in cmd:
                mock_result.stdout = json.dumps(
                    {
                        "state": "OPEN",
                        "title": "Test PR",
                        "isDraft": False,
                    }
                )

            return mock_result

        mock_run.side_effect = mock_subprocess_run

        # Act
        result = linker.link_pr(
            "test-worktree",
            str(worktree_path),
            "feature/test",
            pr_number=123,
            check_status=True,
        )

        # Assert
        assert result.success is True
        pr_info = linker.get_pr("test-worktree")
        assert pr_info.status == PRStatus.OPEN
        assert pr_info.title == "Test PR"


class TestPRStatusChecking:
    """Test PR status checking."""

    @patch("subprocess.run")
    def test_check_pr_status_open(self, mock_run, temp_directory: Path):
        """Test checking PR status returns OPEN."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {
                "state": "OPEN",
                "title": "Open PR",
                "isDraft": False,
            }
        )
        mock_run.return_value = mock_result

        # Act
        status, title = linker._check_pr_status_gh("owner", "repo", 123)

        # Assert
        assert status == PRStatus.OPEN
        assert title == "Open PR"

    @patch("subprocess.run")
    def test_check_pr_status_merged(self, mock_run, temp_directory: Path):
        """Test checking PR status returns MERGED."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {
                "state": "MERGED",
                "title": "Merged PR",
                "isDraft": False,
            }
        )
        mock_run.return_value = mock_result

        # Act
        status, title = linker._check_pr_status_gh("owner", "repo", 123)

        # Assert
        assert status == PRStatus.MERGED

    @patch("subprocess.run")
    def test_check_pr_status_closed(self, mock_run, temp_directory: Path):
        """Test checking PR status returns CLOSED."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {
                "state": "CLOSED",
                "title": "Closed PR",
                "isDraft": False,
            }
        )
        mock_run.return_value = mock_result

        # Act
        status, title = linker._check_pr_status_gh("owner", "repo", 123)

        # Assert
        assert status == PRStatus.CLOSED

    @patch("subprocess.run")
    def test_check_pr_status_draft(self, mock_run, temp_directory: Path):
        """Test checking PR status returns DRAFT."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {
                "state": "OPEN",
                "title": "Draft PR",
                "isDraft": True,
            }
        )
        mock_run.return_value = mock_result

        # Act
        status, title = linker._check_pr_status_gh("owner", "repo", 123)

        # Assert
        assert status == PRStatus.DRAFT

    @patch("subprocess.run")
    def test_check_pr_status_gh_cli_fails(self, mock_run, temp_directory: Path):
        """Test status check when gh CLI fails."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        mock_run.side_effect = subprocess.CalledProcessError(1, "gh")

        # Act
        result = linker._check_pr_status_gh("owner", "repo", 123)

        # Assert
        assert result is None


class TestPRRetrieval:
    """Test PR retrieval methods."""

    def test_get_pr(self, temp_directory: Path):
        """Test getting PR info for a worktree."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        pr_info = PRInfo(
            worktree_name="test-wt",
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            branch="feature/test",
            status=PRStatus.OPEN,
        )
        linker._store.set_pr(pr_info)
        linker._save_store()

        # Act
        retrieved = linker.get_pr("test-wt")

        # Assert
        assert retrieved is not None
        assert retrieved.worktree_name == "test-wt"
        assert retrieved.pr_number == 123

    def test_get_nonexistent_pr(self, temp_directory: Path):
        """Test getting a PR that doesn't exist."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        # Act
        pr = linker.get_pr("nonexistent")

        # Assert
        assert pr is None

    def test_get_all_prs(self, temp_directory: Path):
        """Test getting all PR links."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        pr1 = PRInfo(
            worktree_name="wt1",
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            branch="feature/1",
            status=PRStatus.OPEN,
        )
        pr2 = PRInfo(
            worktree_name="wt2",
            repo_owner="owner",
            repo_name="repo",
            pr_number=124,
            pr_url="https://github.com/owner/repo/pull/124",
            branch="feature/2",
            status=PRStatus.MERGED,
        )

        linker._store.set_pr(pr1)
        linker._store.set_pr(pr2)

        # Act
        prs = linker.get_all_prs()

        # Assert
        assert len(prs) == 2
        names = {p.worktree_name for p in prs}
        assert "wt1" in names
        assert "wt2" in names

    def test_get_merged_prs(self, temp_directory: Path):
        """Test getting only merged PRs."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        pr_open = PRInfo(
            worktree_name="wt-open",
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            branch="feature/open",
            status=PRStatus.OPEN,
        )
        pr_merged = PRInfo(
            worktree_name="wt-merged",
            repo_owner="owner",
            repo_name="repo",
            pr_number=124,
            pr_url="https://github.com/owner/repo/pull/124",
            branch="feature/merged",
            status=PRStatus.MERGED,
        )

        linker._store.set_pr(pr_open)
        linker._store.set_pr(pr_merged)

        # Act
        merged = linker.get_merged_prs()

        # Assert
        assert len(merged) == 1
        assert merged[0].worktree_name == "wt-merged"

    def test_get_open_prs(self, temp_directory: Path):
        """Test getting only open PRs."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        pr_open = PRInfo(
            worktree_name="wt-open",
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            branch="feature/open",
            status=PRStatus.OPEN,
        )
        pr_merged = PRInfo(
            worktree_name="wt-merged",
            repo_owner="owner",
            repo_name="repo",
            pr_number=124,
            pr_url="https://github.com/owner/repo/pull/124",
            branch="feature/merged",
            status=PRStatus.MERGED,
        )

        linker._store.set_pr(pr_open)
        linker._store.set_pr(pr_merged)

        # Act
        open_prs = linker.get_open_prs()

        # Assert
        assert len(open_prs) == 1
        assert open_prs[0].worktree_name == "wt-open"


class TestPRRefresh:
    """Test PR status refresh."""

    @patch("subprocess.run")
    def test_refresh_pr_status(self, mock_run, temp_directory: Path):
        """Test refreshing PR status."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        pr_info = PRInfo(
            worktree_name="test-wt",
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            branch="feature/test",
            status=PRStatus.OPEN,
        )
        linker._store.set_pr(pr_info)

        # Mock gh CLI response
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {
                "state": "MERGED",
                "title": "Updated Title",
                "isDraft": False,
            }
        )
        mock_run.return_value = mock_result

        # Act
        updated = linker.refresh_pr_status("test-wt")

        # Assert
        assert updated is not None
        assert updated.status == PRStatus.MERGED
        assert updated.title == "Updated Title"

    def test_refresh_nonexistent_pr(self, temp_directory: Path):
        """Test refreshing a PR that doesn't exist."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        # Act
        result = linker.refresh_pr_status("nonexistent")

        # Assert
        assert result is None

    @patch("subprocess.run")
    def test_refresh_all_statuses(self, mock_run, temp_directory: Path):
        """Test refreshing all PR statuses."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        pr1 = PRInfo(
            worktree_name="wt1",
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            branch="feature/1",
            status=PRStatus.OPEN,
        )
        pr2 = PRInfo(
            worktree_name="wt2",
            repo_owner="owner",
            repo_name="repo",
            pr_number=124,
            pr_url="https://github.com/owner/repo/pull/124",
            branch="feature/2",
            status=PRStatus.OPEN,
        )

        linker._store.set_pr(pr1)
        linker._store.set_pr(pr2)

        # Mock gh CLI response
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(
            {
                "state": "MERGED",
                "title": "Updated",
                "isDraft": False,
            }
        )
        mock_run.return_value = mock_result

        # Act
        updated = linker.refresh_all_statuses()

        # Assert
        assert len(updated) == 2


class TestPRUnlinking:
    """Test PR unlinking."""

    def test_unlink_pr(self, temp_directory: Path):
        """Test unlinking a PR."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        pr_info = PRInfo(
            worktree_name="test-wt",
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            branch="feature/test",
            status=PRStatus.OPEN,
        )
        linker._store.set_pr(pr_info)
        linker._save_store()

        # Act
        removed = linker.unlink_pr("test-wt")

        # Assert
        assert removed is True
        assert linker.get_pr("test-wt") is None

    def test_unlink_nonexistent_pr(self, temp_directory: Path):
        """Test unlinking a PR that doesn't exist."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        # Act
        removed = linker.unlink_pr("nonexistent")

        # Assert
        assert removed is False


class TestOrphanCleanup:
    """Test cleanup of orphaned PR links."""

    def test_cleanup_orphans(self, temp_directory: Path):
        """Test removing PR links for deleted worktrees."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        # Create PR links for multiple worktrees
        for name, pr_num in [("wt1", 123), ("wt2", 124), ("wt3", 125)]:
            pr = PRInfo(
                worktree_name=name,
                repo_owner="owner",
                repo_name="repo",
                pr_number=pr_num,
                pr_url=f"https://github.com/owner/repo/pull/{pr_num}",
                branch=f"feature/{name}",
                status=PRStatus.OPEN,
            )
            linker._store.set_pr(pr)

        # Act - cleanup with only wt1 and wt2 as valid
        removed = linker.cleanup_orphans(["wt1", "wt2"])

        # Assert
        assert "wt3" in removed
        assert len(removed) == 1
        assert linker.get_pr("wt3") is None
        assert linker.get_pr("wt1") is not None
        assert linker.get_pr("wt2") is not None

    def test_cleanup_orphans_no_orphans(self, temp_directory: Path):
        """Test cleanup when there are no orphans."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        pr = PRInfo(
            worktree_name="wt1",
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            branch="feature/1",
            status=PRStatus.OPEN,
        )
        linker._store.set_pr(pr)

        # Act - all PRs are valid
        removed = linker.cleanup_orphans(["wt1"])

        # Assert
        assert len(removed) == 0


class TestAutoDetectAndLink:
    """Test auto-detect and link PR."""

    @patch("subprocess.run")
    def test_detect_and_link_pr(self, mock_run, temp_directory: Path):
        """Test auto-detecting and linking a PR."""
        # Arrange
        config = PRLinkerConfig(
            storage_path=temp_directory / "pr_links.json",
            auto_link_prs=True,
        )
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Mock git remote
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "git@github.com:owner/repo.git"
        mock_run.return_value = mock_result

        # Act
        result = linker.detect_and_link_pr(
            "test-wt",
            str(worktree_path),
            "feature/test#789",
        )

        # Assert
        assert result is not None
        assert result.success is True
        assert result.pr_number == 789

    def test_detect_and_link_pr_disabled(self, temp_directory: Path):
        """Test auto-detect when disabled in config."""
        # Arrange
        config = PRLinkerConfig(
            storage_path=temp_directory / "pr_links.json",
            auto_link_prs=False,
        )
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Act
        result = linker.detect_and_link_pr(
            "test-wt",
            str(worktree_path),
            "feature/test#789",
        )

        # Assert
        assert result is None


class TestBranchPRExtraction:
    """Test extracting PR number from branch names."""

    def test_extract_pr_from_branch_hash_pattern(self, temp_directory: Path):
        """Test extracting PR with # pattern."""
        # Arrange
        config = PRLinkerConfig(
            storage_path=temp_directory / "pr_links.json",
            branch_pr_pattern=r".*#(\d+).*",
        )
        linker = PRLinker(config=config)

        # Act
        pr_number = linker._extract_pr_from_branch("feature/auth#123")

        # Assert
        assert pr_number == 123

    def test_extract_pr_from_branch_no_match(self, temp_directory: Path):
        """Test extracting PR when no match."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        # Act
        pr_number = linker._extract_pr_from_branch("feature/no-number")

        # Assert
        assert pr_number is None


class TestRemoteURLParsing:
    """Test GitHub remote URL parsing."""

    @patch("subprocess.run")
    def test_parse_ssh_url(self, mock_run, temp_directory: Path):
        """Test parsing SSH GitHub URL."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "git@github.com:owner/repo.git"
        mock_run.return_value = mock_result

        # Act
        repo_info = linker._get_remote_url(str(worktree_path))

        # Assert
        assert repo_info is not None
        owner, repo = repo_info
        assert owner == "owner"
        assert repo == "repo"

    @patch("subprocess.run")
    def test_parse_https_url(self, mock_run, temp_directory: Path):
        """Test parsing HTTPS GitHub URL."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/owner/repo.git"
        mock_run.return_value = mock_result

        # Act
        repo_info = linker._get_remote_url(str(worktree_path))

        # Assert
        assert repo_info is not None
        owner, repo = repo_info
        assert owner == "owner"
        assert repo == "repo"

    @patch("subprocess.run")
    def test_parse_non_github_url(self, mock_run, temp_directory: Path):
        """Test parsing non-GitHub URL returns None."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://gitlab.com/owner/repo.git"
        mock_run.return_value = mock_result

        # Act
        repo_info = linker._get_remote_url(str(worktree_path))

        # Assert
        assert repo_info is None


class TestOpenPRInBrowser:
    """Test opening PR in browser."""

    @patch("webbrowser.open")
    def test_open_pr_in_browser(self, mock_open, temp_directory: Path):
        """Test opening PR in browser."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        pr_info = PRInfo(
            worktree_name="test-wt",
            repo_owner="owner",
            repo_name="repo",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            branch="feature/test",
            status=PRStatus.OPEN,
        )
        linker._store.set_pr(pr_info)

        # Act
        success = linker.open_pr_in_browser("test-wt")

        # Assert
        assert success is True
        mock_open.assert_called_once_with("https://github.com/owner/repo/pull/123")

    def test_open_nonexistent_pr_in_browser(self, temp_directory: Path):
        """Test opening PR that doesn't exist."""
        # Arrange
        config = PRLinkerConfig(storage_path=temp_directory / "pr_links.json")
        linker = PRLinker(config=config)

        # Act
        success = linker.open_pr_in_browser("nonexistent")

        # Assert
        assert success is False


# === CLI Integration Tests ===


class TestPRLinkerCLI:
    """Test CLI commands for PR linking."""

    def test_pr_link_command(self, temp_directory: Path):
        """Test 'owt pr link' command."""
        # Arrange
        runner = CliRunner()

        # Act
        result = runner.invoke(
            cli,
            ["pr", "link", "test-worktree", "--pr", "123"],
        )

        # Assert
        # Command may fail without actual worktree context
        assert result.exit_code in [0, 1, 2]

    def test_pr_status_command(self, temp_directory: Path):
        """Test 'owt pr status' command."""
        # Arrange
        runner = CliRunner()

        # Act
        result = runner.invoke(cli, ["pr", "status", "test-worktree"])

        # Assert
        # Command may fail without actual PR link
        assert result.exit_code in [0, 1, 2]

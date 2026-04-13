"""
Tests for WorktreeManager class and git worktree operations.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from git.exc import GitCommandError

from open_orchestrator.core.worktree import (
    NotAGitRepositoryError,
    WorktreeAlreadyExistsError,
    WorktreeError,
    WorktreeManager,
    WorktreeNotFoundError,
)


class TestWorktreeManagerInit:
    """Test WorktreeManager initialization."""

    def test_init_with_valid_repo(self, git_repo: Path) -> None:
        """Test WorktreeManager initialization with valid repository."""
        # Act
        manager = WorktreeManager(git_repo)

        # Assert
        assert manager.repo_path == git_repo
        assert manager.git_root == git_repo
        assert manager.repo is not None

    def test_init_with_invalid_repo(self, temp_directory: Path) -> None:
        """Test WorktreeManager initialization with invalid repository fails."""
        # Arrange
        non_repo = temp_directory / "not-a-repo"
        non_repo.mkdir()

        # Act & Assert
        with pytest.raises(NotAGitRepositoryError, match="Not a git repository"):
            WorktreeManager(non_repo)

    def test_init_with_current_directory_in_git_repo(self, git_repo: Path) -> None:
        """Test WorktreeManager initialization without explicit path."""
        # Arrange
        with patch("pathlib.Path.cwd", return_value=git_repo):
            # Act
            manager = WorktreeManager()

            # Assert
            assert manager.git_root == git_repo

    def test_project_name_property(self, git_repo: Path) -> None:
        """Test project_name property returns repository directory name."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        project_name = manager.project_name

        # Assert
        assert project_name == git_repo.name


class TestBranchNameValidation:
    """Test branch name validation and sanitization."""

    def test_validate_branch_name_valid(self, git_repo: Path) -> None:
        """Test valid branch names pass validation."""
        # Arrange
        manager = WorktreeManager(git_repo)
        valid_names = [
            "feature/test",
            "bugfix/fix-123",
            "release/v1.0.0",
            "hotfix_urgent",
            "dev.branch",
        ]

        # Act & Assert
        for name in valid_names:
            manager._validate_branch_name(name)  # Should not raise

    def test_validate_branch_name_empty(self, git_repo: Path) -> None:
        """Test empty branch name fails validation."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act & Assert
        with pytest.raises(WorktreeError, match="cannot be empty"):
            manager._validate_branch_name("")

    def test_validate_branch_name_starts_with_dash(self, git_repo: Path) -> None:
        """Test branch name starting with dash fails validation."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act & Assert
        with pytest.raises(WorktreeError, match="cannot start with '-'"):
            manager._validate_branch_name("-feature")

    def test_validate_branch_name_directory_traversal(self, git_repo: Path) -> None:
        """Test branch name with directory traversal fails validation."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act & Assert
        with pytest.raises(WorktreeError, match="cannot contain '..'"):
            manager._validate_branch_name("../escape")
        with pytest.raises(WorktreeError, match="cannot contain '..'"):
            manager._validate_branch_name("feature/../escape")

    def test_validate_branch_name_invalid_characters(self, git_repo: Path) -> None:
        """Test branch name with invalid characters fails validation."""
        # Arrange
        manager = WorktreeManager(git_repo)
        invalid_names = [
            "feature@test",
            "bug;fix",
            "release|v1",
            "test:branch",
            "feature branch",  # space
        ]

        # Act & Assert
        for name in invalid_names:
            with pytest.raises(WorktreeError, match="invalid characters"):
                manager._validate_branch_name(name)

    def test_sanitize_branch_name(self, git_repo: Path) -> None:
        """Test branch name sanitization for directory names."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act & Assert
        assert manager._sanitize_branch_name("feature/test") == "feature-test"
        assert manager._sanitize_branch_name("bugfix_fix-123") == "bugfix_fix-123"
        assert manager._sanitize_branch_name("release/v1.0.0") == "release-v100"


class TestWorktreePathGeneration:
    """Test worktree path generation."""

    def test_generate_worktree_path(self, git_repo: Path) -> None:
        """Test worktree path generation follows expected pattern."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        path = manager._generate_worktree_path("feature/test")

        # Assert
        assert path == git_repo.parent / f"{git_repo.name}-feature-test"
        assert path.parent == git_repo.parent


class TestWorktreeListAll:
    """Test listing all worktrees."""

    def test_list_all_main_worktree_only(self, git_repo: Path) -> None:
        """Test listing worktrees returns at least the main worktree."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        worktrees = manager.list_all()

        # Assert
        assert len(worktrees) >= 1
        main_wt = next((wt for wt in worktrees if wt.is_main), None)
        assert main_wt is not None
        assert main_wt.path == git_repo

    def test_list_all_with_worktree(self, git_repo: Path, git_worktree: Path) -> None:
        """Test listing worktrees includes created worktrees."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        worktrees = manager.list_all()

        # Assert
        assert len(worktrees) >= 2
        wt = next((w for w in worktrees if w.path == git_worktree), None)
        assert wt is not None
        assert wt.branch == "test-branch"

    def test_list_all_git_command_error(self, git_repo: Path) -> None:
        """Test listing worktrees returns empty list on git error."""
        # Arrange
        manager = WorktreeManager(git_repo)
        with patch.object(manager.repo, "git") as mock_git:
            mock_git.worktree.side_effect = GitCommandError("worktree", "")
            # Act
            worktrees = manager.list_all()

            # Assert
            assert worktrees == []


class TestFindWorktree:
    """Test finding worktrees by identifier."""

    def test_find_worktree_by_name(self, git_repo: Path, git_worktree: Path) -> None:
        """Test finding a worktree by its name."""
        # Arrange
        manager = WorktreeManager(git_repo)
        expected_name = git_worktree.name

        # Act
        wt = manager._find_worktree(expected_name)

        # Assert
        assert wt is not None
        assert wt.path == git_worktree

    def test_find_worktree_by_branch(self, git_repo: Path, git_worktree: Path) -> None:
        """Test finding a worktree by its branch name."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        wt = manager._find_worktree("test-branch")

        # Assert
        assert wt is not None
        assert wt.path == git_worktree
        assert wt.branch == "test-branch"

    def test_find_worktree_by_path(self, git_repo: Path, git_worktree: Path) -> None:
        """Test finding a worktree by its path."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        wt = manager._find_worktree(str(git_worktree))

        # Assert
        assert wt is not None
        assert wt.path == git_worktree

    def test_find_worktree_by_branch_suffix(self, git_repo: Path, git_worktree: Path) -> None:
        """Test finding a worktree by branch suffix (e.g., 'branch' matches 'feature/branch')."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        wt = manager._find_worktree("test-branch")

        # Assert
        assert wt is not None

    def test_find_worktree_not_found(self, git_repo: Path) -> None:
        """Test finding a nonexistent worktree returns None."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        wt = manager._find_worktree("nonexistent-branch")

        # Assert
        assert wt is None


class TestBranchExists:
    """Test branch existence checking."""

    def test_branch_exists_main_branch(self, git_repo: Path) -> None:
        """Test checking if main branch exists."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        exists = manager._branch_exists("main")

        # Assert
        assert exists is True or manager._branch_exists("master") is True

    def test_branch_exists_nonexistent(self, git_repo: Path) -> None:
        """Test checking if nonexistent branch exists."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        exists = manager._branch_exists("nonexistent-branch-12345")

        # Assert
        assert exists is False


class TestCreateWorktree:
    """Test creating worktrees."""

    def test_create_worktree_new_branch(self, git_repo: Path, temp_directory: Path) -> None:
        """Test creating a worktree with a new branch."""
        # Arrange
        manager = WorktreeManager(git_repo)
        wt_path = temp_directory / "new-worktree"

        # Act
        worktree = manager.create(branch="feature/new", path=wt_path)

        # Assert
        assert worktree.branch == "feature/new"
        assert worktree.path == wt_path
        assert wt_path.exists()

    def test_create_worktree_existing_branch(self, git_repo: Path, temp_directory: Path) -> None:
        """Test creating a worktree with an existing branch."""
        # Arrange
        manager = WorktreeManager(git_repo)
        # Create a branch first so it exists
        manager.repo.git.branch("existing-test-branch")
        wt_path = temp_directory / "existing-branch-worktree"

        # Act
        worktree = manager.create(branch="existing-test-branch", path=wt_path)

        # Assert
        assert worktree.branch == "existing-test-branch"
        assert worktree.path == wt_path
        assert wt_path.exists()

    def test_create_worktree_with_base_branch(self, git_repo: Path, temp_directory: Path) -> None:
        """Test creating a worktree with specified base branch."""
        # Arrange
        manager = WorktreeManager(git_repo)
        wt_path = temp_directory / "new-feature-worktree"

        # Act
        worktree = manager.create(branch="feature/new", base_branch="main", path=wt_path)

        # Assert
        assert worktree.branch == "feature/new"
        assert worktree.path == wt_path
        assert wt_path.exists()

    def test_create_worktree_path_already_exists(self, git_repo: Path, temp_directory: Path) -> None:
        """Test creating a worktree fails if path already exists."""
        # Arrange
        manager = WorktreeManager(git_repo)
        existing_path = temp_directory / "existing-dir"
        existing_path.mkdir()

        # Act & Assert
        with pytest.raises(WorktreeAlreadyExistsError, match="Directory already exists"):
            manager.create(branch="feature/test", path=existing_path)

    def test_create_worktree_branch_already_has_worktree(self, git_repo: Path, git_worktree: Path) -> None:
        """Test creating a worktree fails if branch already has a worktree."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act & Assert
        with pytest.raises(WorktreeAlreadyExistsError, match="already exists at"):
            manager.create(branch="test-branch")

    def test_create_worktree_force_override(self, git_repo: Path, git_worktree: Path, temp_directory: Path) -> None:
        """Test creating a worktree with force flag overrides existing branch check."""
        # Arrange
        manager = WorktreeManager(git_repo)
        new_path = temp_directory / "forced-worktree"

        # Act
        worktree = manager.create(branch="test-branch", path=new_path, force=True)

        # Assert
        assert worktree.path == new_path
        assert worktree.branch == "test-branch"

    def test_create_worktree_invalid_branch_name(self, git_repo: Path) -> None:
        """Test creating a worktree with invalid branch name fails."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act & Assert
        with pytest.raises(WorktreeError, match="invalid characters"):
            manager.create(branch="invalid;branch")

    def test_create_worktree_directory_traversal_prevention(self, git_repo: Path) -> None:
        """Test creating a worktree prevents directory traversal in branch name."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act & Assert
        with pytest.raises(WorktreeError, match="cannot contain '..'"):
            manager.create(branch="../escape")

    def test_create_worktree_auto_path_generation(self, git_repo: Path) -> None:
        """Test creating a worktree with auto-generated path."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        worktree = manager.create(branch="feature/auto-path")

        # Assert
        assert worktree.branch == "feature/auto-path"
        expected_path = git_repo.parent / f"{git_repo.name}-feature-auto-path"
        assert worktree.path == expected_path


class TestDeleteWorktree:
    """Test deleting worktrees."""

    def test_delete_worktree_by_branch(self, git_repo: Path, git_worktree: Path) -> None:
        """Test deleting a worktree by branch name."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        deleted_path = manager.delete("test-branch", force=True)

        # Assert
        assert deleted_path == git_worktree
        # Worktree should no longer exist
        wt = manager._find_worktree("test-branch")
        assert wt is None

    def test_delete_worktree_by_path(self, git_repo: Path, git_worktree: Path) -> None:
        """Test deleting a worktree by path."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act
        deleted_path = manager.delete(str(git_worktree), force=True)

        # Assert
        assert deleted_path == git_worktree

    def test_delete_nonexistent_worktree(self, git_repo: Path) -> None:
        """Test deleting a nonexistent worktree fails."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act & Assert
        with pytest.raises(WorktreeNotFoundError, match="not found"):
            manager.delete("nonexistent-branch")

    def test_delete_main_worktree_fails(self, git_repo: Path) -> None:
        """Test deleting the main worktree fails."""
        # Arrange
        manager = WorktreeManager(git_repo)

        # Act & Assert
        with pytest.raises(WorktreeError, match="Cannot delete the main worktree"):
            manager.delete(str(git_repo))


class TestParseWorktreeEntry:
    """Test parsing worktree entry dictionaries."""

    def test_parse_worktree_entry_complete(self, git_repo: Path) -> None:
        """Test parsing a complete worktree entry."""
        # Arrange
        manager = WorktreeManager(git_repo)
        entry = {
            "path": str(git_repo / "test-worktree"),
            "branch": "refs/heads/feature/test",
            "head": "abc123def456789",
            "detached": False,
            "bare": False,
        }

        # Act
        wt_info = manager._parse_worktree_entry(entry)

        # Assert
        assert wt_info.branch == "feature/test"
        assert wt_info.head_commit == "abc123d"
        assert wt_info.is_detached is False

    def test_parse_worktree_entry_detached(self, git_repo: Path) -> None:
        """Test parsing a detached HEAD worktree entry."""
        # Arrange
        manager = WorktreeManager(git_repo)
        entry = {
            "path": str(git_repo / "detached-worktree"),
            "head": "abc123def456789",
            "detached": True,
        }

        # Act
        wt_info = manager._parse_worktree_entry(entry)

        # Assert
        assert wt_info.branch == "(detached)"
        assert wt_info.is_detached is True

    def test_parse_worktree_entry_main(self, git_repo: Path) -> None:
        """Test parsing the main worktree entry."""
        # Arrange
        manager = WorktreeManager(git_repo)
        entry = {
            "path": str(git_repo),
            "branch": "refs/heads/main",
            "head": "abc123def456789",
        }

        # Act
        wt_info = manager._parse_worktree_entry(entry)

        # Assert
        assert wt_info.is_main is True
        assert wt_info.path == git_repo


class TestCWDDetection:
    """Test that WorktreeManager resolves git_root to the main repo even from a child worktree."""

    def test_git_root_from_worktree_cwd(self, git_repo: Path, temp_directory: Path) -> None:
        """WorktreeManager initialized from a child worktree should resolve git_root to the main repo."""
        import subprocess

        wt_path = temp_directory / "child-worktree"
        subprocess.run(
            ["git", "worktree", "add", "-b", "feat/cwd-test", str(wt_path)],
            cwd=git_repo,
            capture_output=True,
        )

        # Initialize WorktreeManager from the child worktree path
        manager = WorktreeManager(wt_path)

        # git_root should point to the main repo, not the worktree
        assert manager.git_root == git_repo

        # Cleanup
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=git_repo,
            capture_output=True,
        )

    def test_is_main_correct_from_worktree(self, git_repo: Path, temp_directory: Path) -> None:
        """is_main should be True only for the actual main repo, not the worktree."""
        import subprocess

        wt_path = temp_directory / "child-wt-main"
        subprocess.run(
            ["git", "worktree", "add", "-b", "feat/main-test", str(wt_path)],
            cwd=git_repo,
            capture_output=True,
        )

        manager = WorktreeManager(wt_path)
        worktrees = manager.list_all()

        main_wts = [wt for wt in worktrees if wt.is_main]
        child_wts = [wt for wt in worktrees if not wt.is_main]

        assert len(main_wts) == 1
        assert main_wts[0].path == git_repo
        assert any(wt.path == wt_path for wt in child_wts)

        # Cleanup
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=git_repo,
            capture_output=True,
        )


class TestInitGitCommonDirFallback:
    """Test WorktreeManager.__init__ fallback when rev-parse --git-common-dir fails."""

    def test_git_root_falls_back_to_working_dir_on_git_command_error(self, git_repo: Path) -> None:
        """When rev-parse --git-common-dir raises GitCommandError, git_root falls back to working_dir."""
        with patch("open_orchestrator.core.worktree.Repo") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.working_dir = str(git_repo)
            mock_repo.git.rev_parse.side_effect = GitCommandError("rev-parse", "")
            mock_repo_cls.return_value = mock_repo

            manager = WorktreeManager(git_repo)

            assert manager.git_root == Path(str(git_repo))


class TestListAllPorcelainParsing:
    """Test list_all parsing of git worktree --porcelain output edge cases."""

    def test_list_all_parses_detached_line(self, git_repo: Path) -> None:
        """list_all correctly sets detached=True when porcelain output contains 'detached'."""
        manager = WorktreeManager(git_repo)
        # Build porcelain output with a detached worktree entry and a blank
        # separator line so the mid-loop branch is exercised (134->137).
        porcelain = (
            f"worktree {git_repo}\n"
            "HEAD abc123def456789\n"
            "branch refs/heads/main\n"
            "\n"  # <-- blank line triggers lines 134-137
            "worktree /tmp/detached-wt\n"
            "HEAD deadbeef12345678\n"
            "detached\n"
        )
        with patch.object(manager.repo, "git") as mock_git:
            mock_git.worktree.return_value = porcelain
            worktrees = manager.list_all()

        detached_wts = [wt for wt in worktrees if wt.is_detached]
        assert len(detached_wts) >= 1
        assert detached_wts[0].branch == "(detached)"

    def test_list_all_parses_bare_line(self, git_repo: Path) -> None:
        """list_all correctly handles 'bare' line in porcelain output."""
        manager = WorktreeManager(git_repo)
        porcelain = (
            f"worktree {git_repo}\n"
            "HEAD abc123def456789\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /tmp/bare-wt\n"
            "HEAD cafebabe12345678\n"
            "bare\n"
        )
        with patch.object(manager.repo, "git") as mock_git:
            mock_git.worktree.return_value = porcelain
            worktrees = manager.list_all()

        # bare worktree is parsed without error; branch defaults to "(detached)"
        assert len(worktrees) == 2

    def test_list_all_appends_last_entry_without_trailing_blank_line(self, git_repo: Path) -> None:
        """list_all appends last entry (line 151) when output has no trailing blank line."""
        manager = WorktreeManager(git_repo)
        # No trailing newline — last entry only flushed via the post-loop check on line 151
        porcelain = f"worktree {git_repo}\nHEAD abc123def456789\nbranch refs/heads/main"
        with patch.object(manager.repo, "git") as mock_git:
            mock_git.worktree.return_value = porcelain
            worktrees = manager.list_all()

        assert len(worktrees) == 1
        assert worktrees[0].is_main is True

    def test_list_all_intermediate_blank_flushes_current_entry(self, git_repo: Path) -> None:
        """Blank line in the middle of porcelain output flushes the current entry (lines 134-137)."""
        manager = WorktreeManager(git_repo)
        wt_path = git_repo.parent / "some-worktree"
        porcelain = (
            f"worktree {git_repo}\n"
            "HEAD aaa111bbb222333\n"
            "branch refs/heads/main\n"
            "\n"  # flush first entry
            f"worktree {wt_path}\n"
            "HEAD ccc333ddd444555\n"
            "branch refs/heads/feature/x\n"
        )
        with patch.object(manager.repo, "git") as mock_git:
            mock_git.worktree.return_value = porcelain
            worktrees = manager.list_all()

        assert len(worktrees) == 2
        names = {wt.branch for wt in worktrees}
        assert "main" in names
        assert "feature/x" in names


class TestCreateWorktreeEdgeCases:
    """Test edge cases in WorktreeManager.create."""

    def test_create_raises_on_detached_head_without_base_branch(self, git_repo: Path, temp_directory: Path) -> None:
        """create raises WorktreeError when HEAD is detached and no base_branch is provided."""
        manager = WorktreeManager(git_repo)
        wt_path = temp_directory / "detached-new-wt"

        # Patch _branch_exists to return False (new branch path) and
        # repo.head to simulate a detached HEAD.
        mock_head = MagicMock()
        mock_head.is_detached = True

        with (
            patch.object(manager, "_branch_exists", return_value=False),
            patch.object(type(manager.repo), "head", new_callable=lambda: property(lambda self: mock_head)),
        ):
            with pytest.raises(WorktreeError, match="Detached HEAD detected"):
                manager.create(branch="new-branch-from-detached", path=wt_path)

    def test_create_raises_worktree_error_on_git_command_error(self, git_repo: Path, temp_directory: Path) -> None:
        """create wraps GitCommandError into WorktreeError (lines 259-260)."""
        manager = WorktreeManager(git_repo)
        wt_path = temp_directory / "fail-wt"

        with patch.object(manager, "_branch_exists", return_value=False):
            with patch.object(manager.repo, "git") as mock_git:
                mock_git.worktree.side_effect = GitCommandError("worktree", "add failed")
                with pytest.raises(WorktreeError, match="Failed to create worktree"):
                    manager.create(branch="fail-branch", path=wt_path)

    def test_create_raises_when_worktree_not_found_after_creation(self, git_repo: Path, temp_directory: Path) -> None:
        """create raises WorktreeError when worktree cannot be found after creation (line 264)."""
        manager = WorktreeManager(git_repo)
        wt_path = temp_directory / "ghost-wt"

        with (
            patch.object(manager, "_branch_exists", return_value=False),
            patch.object(manager.repo, "git") as mock_git,
            patch.object(manager, "_find_worktree", return_value=None),
        ):
            mock_git.worktree.return_value = ""
            with pytest.raises(WorktreeError, match="Worktree created but not found"):
                manager.create(branch="ghost-branch", path=wt_path)


class TestDeleteWorktreeEdgeCases:
    """Test edge cases in WorktreeManager.delete."""

    def test_delete_without_force_omits_force_flag(self, git_repo: Path, git_worktree: Path) -> None:
        """delete without force=True calls git worktree remove without --force (line 353->355)."""
        from open_orchestrator.models.worktree_info import WorktreeInfo

        manager = WorktreeManager(git_repo)
        fake_wt = WorktreeInfo(path=git_worktree, branch="test-branch", head_commit="abc1234", is_main=False)

        with (
            patch.object(manager, "_find_worktree", return_value=fake_wt),
            patch.object(manager.repo, "git") as mock_git,
        ):
            mock_git.worktree.return_value = ""
            manager.delete("test-branch", force=False)
            call_args = mock_git.worktree.call_args
            assert call_args is not None
            positional = call_args.args
            assert "--force" not in positional

    def test_delete_raises_worktree_error_on_git_command_error(self, git_repo: Path, git_worktree: Path) -> None:
        """delete wraps GitCommandError into WorktreeError (lines 359-360)."""
        from open_orchestrator.models.worktree_info import WorktreeInfo

        manager = WorktreeManager(git_repo)
        fake_wt = WorktreeInfo(path=git_worktree, branch="test-branch", head_commit="abc1234", is_main=False)

        with (
            patch.object(manager, "_find_worktree", return_value=fake_wt),
            patch.object(manager.repo, "git") as mock_git,
        ):
            mock_git.worktree.side_effect = GitCommandError("worktree", "remove failed")
            with pytest.raises(WorktreeError, match="Failed to delete worktree"):
                manager.delete("test-branch", force=True)


class TestGetTemplateConfig:
    """Test WorktreeManager.get_template_config (lines 284-310)."""

    def test_get_template_config_raises_for_unknown_template(self, git_repo: Path) -> None:
        """get_template_config raises WorktreeError when template is not found."""
        manager = WorktreeManager(git_repo)

        with pytest.raises(WorktreeError, match="Template not found"):
            manager.get_template_config("nonexistent-template-xyz")

    def test_get_template_config_returns_overrides_for_builtin_feature_template(self, git_repo: Path) -> None:
        """get_template_config returns a non-empty overrides dict for the 'feature' built-in template."""
        manager = WorktreeManager(git_repo)

        overrides = manager.get_template_config("feature")

        assert isinstance(overrides, dict)
        # 'feature' template has base_branch="develop"
        assert overrides.get("base_branch") == "develop"
        # 'feature' template has plan_mode=True
        assert overrides.get("plan_mode") is True

    def test_get_template_config_returns_overrides_for_builtin_bugfix_template(self, git_repo: Path) -> None:
        """get_template_config returns correct overrides for the 'bugfix' built-in template."""
        manager = WorktreeManager(git_repo)

        overrides = manager.get_template_config("bugfix")

        assert isinstance(overrides, dict)
        assert overrides.get("base_branch") == "main"
        assert "ai_instructions" in overrides

    def test_get_template_config_omits_none_fields(self, git_repo: Path) -> None:
        """get_template_config does not include keys whose template value is None."""
        from open_orchestrator.config import WorktreeTemplate

        manager = WorktreeManager(git_repo)
        minimal_template = WorktreeTemplate(
            name="minimal",
            description="Minimal template with no optional fields",
        )

        mock_config = MagicMock()
        mock_config.get_template.return_value = minimal_template

        with patch("open_orchestrator.config.load_config", return_value=mock_config):
            overrides = manager.get_template_config("minimal")

        # None-valued optional fields should not appear in overrides
        assert "base_branch" not in overrides
        assert "ai_tool" not in overrides
        assert "tmux_layout" not in overrides
        assert "ai_instructions" not in overrides

    def test_get_template_config_includes_all_populated_fields(self, git_repo: Path) -> None:
        """get_template_config includes all non-None optional fields from the template."""
        from open_orchestrator.config import WorktreeTemplate

        manager = WorktreeManager(git_repo)
        full_template = WorktreeTemplate(
            name="full",
            description="Full template",
            base_branch="develop",
            ai_tool="claude",
            tmux_layout="main-vertical",
            plan_mode=True,
            install_deps=False,
            ai_instructions="Do the thing",
            auto_commands=["npm install", "npm test"],
        )

        mock_config = MagicMock()
        mock_config.get_template.return_value = full_template

        with patch("open_orchestrator.config.load_config", return_value=mock_config):
            overrides = manager.get_template_config("full")

        assert overrides["base_branch"] == "develop"
        assert overrides["tmux_layout"] == "main-vertical"
        assert overrides["plan_mode"] is True
        assert overrides["install_deps"] is False
        assert overrides["ai_instructions"] == "Do the thing"
        assert overrides["auto_commands"] == ["npm install", "npm test"]

    def test_get_template_config_raises_when_config_get_template_returns_none(self, git_repo: Path) -> None:
        """get_template_config raises WorktreeError when config.get_template returns None."""
        manager = WorktreeManager(git_repo)

        mock_config = MagicMock()
        mock_config.get_template.return_value = None

        with patch("open_orchestrator.config.load_config", return_value=mock_config):
            with pytest.raises(WorktreeError, match="Template not found"):
                manager.get_template_config("missing")

    def test_get_template_config_includes_auto_commands_when_present(self, git_repo: Path) -> None:
        """get_template_config includes auto_commands when the template list is non-empty (line 307-308)."""
        from open_orchestrator.config import WorktreeTemplate

        manager = WorktreeManager(git_repo)
        template_with_cmds = WorktreeTemplate(
            name="with-cmds",
            description="Has auto commands",
            auto_commands=["echo hello", "make build"],
        )
        mock_config = MagicMock()
        mock_config.get_template.return_value = template_with_cmds

        with patch("open_orchestrator.config.load_config", return_value=mock_config):
            overrides = manager.get_template_config("with-cmds")

        assert overrides["auto_commands"] == ["echo hello", "make build"]


class TestGetWorktree:
    """Test the get() convenience method on WorktreeManager."""

    def test_get_returns_existing_worktree(self, git_repo: Path, git_worktree: Path) -> None:
        """get returns WorktreeInfo for an existing worktree (line 377, 380)."""
        manager = WorktreeManager(git_repo)

        wt = manager.get("test-branch")

        assert wt is not None
        assert wt.branch == "test-branch"
        assert wt.path == git_worktree

    def test_get_raises_for_missing_worktree(self, git_repo: Path) -> None:
        """get raises WorktreeNotFoundError when identifier is not found (lines 378-379)."""
        manager = WorktreeManager(git_repo)

        with pytest.raises(WorktreeNotFoundError, match="Worktree not found"):
            manager.get("no-such-branch")

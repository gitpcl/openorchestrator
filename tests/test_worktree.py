"""
Tests for WorktreeManager class and git worktree operations.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from git.exc import GitCommandError, InvalidGitRepositoryError

from open_orchestrator.core.worktree import (
    NotAGitRepositoryError,
    WorktreeAlreadyExistsError,
    WorktreeError,
    WorktreeManager,
    WorktreeNotFoundError,
)
from open_orchestrator.models.worktree_info import WorktreeInfo


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
        assert manager._sanitize_branch_name("release/v1.0.0") == "releasev100"


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
        with patch.object(manager.repo.git, "worktree", side_effect=GitCommandError("worktree", "")):
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
        expected_name = f"{git_repo.name}-test-branch"

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
        wt_path = temp_directory / "existing-branch-worktree"

        # Act
        worktree = manager.create(branch="main", path=wt_path)

        # Assert
        assert worktree.branch == "main"
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

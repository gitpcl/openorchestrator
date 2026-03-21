"""Tests for the merge module using real git repo fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from open_orchestrator.core.merge import (
    MergeError,
    MergeManager,
    MergeResult,
    MergeStatus,
)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Repo:
    """Create a real git repo with a main branch and initial commit."""
    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()
    (tmp_path / "README.md").write_text("# Test\n")
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")
    return repo


@pytest.fixture()
def merge_manager(git_repo: Repo) -> MergeManager:
    """Create a MergeManager from the test repo."""
    return MergeManager(repo_path=Path(git_repo.working_dir))


class TestGetBaseBranch:
    def test_detects_main(self, merge_manager: MergeManager):
        # Default branch from init is "main" or "master"
        result = merge_manager.get_base_branch("feat/test")
        assert result in ("main", "master")

    def test_raises_when_no_base(self, git_repo: Repo, tmp_path: Path):
        # Rename the branch to something unusual
        git_repo.git.branch("-m", "unusual-branch-name")
        mm = MergeManager(repo_path=tmp_path)
        with pytest.raises(MergeError, match="Could not determine base branch"):
            mm.get_base_branch("feat/test")


class TestCountCommitsAhead:
    def test_zero_on_same_branch(self, merge_manager: MergeManager, git_repo: Repo):
        branch = git_repo.active_branch.name
        assert merge_manager.count_commits_ahead(branch, branch) == 0

    def test_counts_new_commits(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name
        git_repo.git.checkout("-b", "feat/test")
        (Path(git_repo.working_dir) / "new.txt").write_text("hello")
        git_repo.index.add(["new.txt"])
        git_repo.index.commit("Add new.txt")
        assert merge_manager.count_commits_ahead("feat/test", base) == 1


class TestGetModifiedFiles:
    def test_returns_modified_files(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name
        git_repo.git.checkout("-b", "feat/test")
        (Path(git_repo.working_dir) / "new.txt").write_text("hello")
        git_repo.index.add(["new.txt"])
        git_repo.index.commit("Add new.txt")
        files = merge_manager.get_modified_files("feat/test", base)
        assert "new.txt" in files

    def test_returns_empty_for_same_branch(self, merge_manager: MergeManager, git_repo: Repo):
        branch = git_repo.active_branch.name
        assert merge_manager.get_modified_files(branch, branch) == []


class TestCheckUncommittedChanges:
    def test_clean_repo(self, merge_manager: MergeManager, git_repo: Repo):
        # Create a worktree
        wt_path = Path(git_repo.working_dir).parent / "wt-test"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/test")
        result = merge_manager.check_uncommitted_changes("test")
        assert result == []
        # Cleanup
        git_repo.git.worktree("remove", str(wt_path), "--force")

    def test_dirty_repo(self, merge_manager: MergeManager, git_repo: Repo):
        wt_path = Path(git_repo.working_dir).parent / "wt-dirty"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/dirty")
        (wt_path / "dirty.txt").write_text("uncommitted")
        result = merge_manager.check_uncommitted_changes("dirty")
        assert "dirty.txt" in result
        # Cleanup
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMerge:
    def test_simple_merge(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-merge"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/merge-test")
        # Add a commit in the worktree
        wt_repo = Repo(wt_path)
        (wt_path / "feature.txt").write_text("feature code")
        wt_repo.index.add(["feature.txt"])
        wt_repo.index.commit("Add feature")

        result = merge_manager.merge(
            worktree_name="merge-test",
            base_branch=base,
            delete_worktree=True,
        )
        assert result.status == MergeStatus.SUCCESS
        assert result.commits_merged == 1

    def test_already_merged(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-noop"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/noop")
        # No new commits — already up to date
        result = merge_manager.merge(
            worktree_name="noop",
            base_branch=base,
            delete_worktree=False,
        )
        assert result.status == MergeStatus.ALREADY_MERGED
        # Cleanup
        git_repo.git.worktree("remove", str(wt_path), "--force")

    def test_merge_rejects_dirty_worktree(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-reject"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/reject")
        (wt_path / "dirty.txt").write_text("uncommitted")
        with pytest.raises(MergeError, match="uncommitted changes"):
            merge_manager.merge(worktree_name="reject", base_branch=base)
        # Cleanup
        git_repo.git.worktree("remove", str(wt_path), "--force")

    def test_merge_nonexistent_worktree(self, merge_manager: MergeManager):
        with pytest.raises(MergeError, match="Worktree not found"):
            merge_manager.merge(worktree_name="nonexistent")


class TestMergeResult:
    def test_to_dict(self):
        result = MergeResult(
            status=MergeStatus.SUCCESS,
            source_branch="feat/test",
            target_branch="main",
            message="Merged",
            commits_merged=3,
        )
        d = result.to_dict()
        assert d["status"] == "success"
        assert d["commits_merged"] == 3
        assert d["conflicts"] == []

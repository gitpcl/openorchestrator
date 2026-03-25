"""Tests for the merge module using real git repo fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from open_orchestrator.core.merge import (
    MergeConflictError,
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


class TestMergeAutoStash:
    """Tests for auto-stash of main repo during Phase 2."""

    def test_merge_succeeds_with_dirty_main(self, merge_manager: MergeManager, git_repo: Repo):
        """Phase 2 auto-stashes main repo when it has uncommitted changes."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-stash"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/stash-test")

        wt_repo = Repo(wt_path)
        (wt_path / "feature.txt").write_text("feature code")
        wt_repo.index.add(["feature.txt"])
        wt_repo.index.commit("Add feature")

        # Make main repo dirty
        (Path(git_repo.working_dir) / "dirty.txt").write_text("uncommitted")

        result = merge_manager.merge(
            worktree_name="stash-test",
            base_branch=base,
            delete_worktree=True,
        )
        assert result.status == MergeStatus.SUCCESS

        # Dirty file should still be there after stash pop
        assert (Path(git_repo.working_dir) / "dirty.txt").exists()

    def test_merge_pops_stash_on_phase2_failure(self, git_repo: Repo):
        """Stash is popped even when Phase 2 merge fails."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-stash-fail"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/stash-fail")

        wt_repo = Repo(wt_path)
        (wt_path / "conflict.txt").write_text("worktree version")
        wt_repo.index.add(["conflict.txt"])
        wt_repo.index.commit("Add conflict file")

        # Create conflicting change on main (same file, different content)
        (Path(git_repo.working_dir) / "conflict.txt").write_text("main version")
        git_repo.index.add(["conflict.txt"])
        git_repo.index.commit("Main conflict")

        # Also make main dirty with a different file
        (Path(git_repo.working_dir) / "dirty.txt").write_text("uncommitted")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))
        # Phase 2 will fail because of conflicting commits
        try:
            mm.merge(worktree_name="stash-fail", base_branch=base, delete_worktree=False)
        except (MergeError, MergeConflictError):
            pass

        # Dirty file should still exist after stash pop
        assert (Path(git_repo.working_dir) / "dirty.txt").exists()
        # Cleanup
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergeLeaveConflicts:
    """Tests for leave_conflicts parameter."""

    def test_leave_conflicts_does_not_abort(self, git_repo: Repo):
        """When leave_conflicts=True, merge stays in-progress."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-leave"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/leave-test")

        # Create diverging commits on both branches
        wt_repo = Repo(wt_path)
        (wt_path / "shared.txt").write_text("worktree version")
        wt_repo.index.add(["shared.txt"])
        wt_repo.index.commit("Worktree change")

        git_repo.git.checkout(base)
        (Path(git_repo.working_dir) / "shared.txt").write_text("main version")
        git_repo.index.add(["shared.txt"])
        git_repo.index.commit("Main change")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))
        with pytest.raises(MergeConflictError, match="left in-progress"):
            mm.merge(
                worktree_name="leave-test",
                base_branch=base,
                leave_conflicts=True,
                delete_worktree=False,
            )

        # Merge should be in-progress in the worktree (MERGE_HEAD exists)
        merge_head = wt_path / ".git"
        # For worktrees, .git is a file pointing to the actual git dir
        # Check if the merge is in-progress via git status
        status = wt_repo.git.status()
        assert "Unmerged" in status or "both modified" in status or "fix conflicts" in status.lower()

        # Cleanup
        wt_repo.git.merge("--abort")
        git_repo.git.worktree("remove", str(wt_path), "--force")

    def test_default_aborts_on_conflict(self, git_repo: Repo):
        """Default behavior aborts merge on conflict."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-abort"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/abort-test")

        wt_repo = Repo(wt_path)
        (wt_path / "shared.txt").write_text("worktree version")
        wt_repo.index.add(["shared.txt"])
        wt_repo.index.commit("Worktree change")

        git_repo.git.checkout(base)
        (Path(git_repo.working_dir) / "shared.txt").write_text("main version")
        git_repo.index.add(["shared.txt"])
        git_repo.index.commit("Main change")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))
        with pytest.raises(MergeConflictError):
            mm.merge(
                worktree_name="abort-test",
                base_branch=base,
                leave_conflicts=False,
                delete_worktree=False,
            )

        # Merge should NOT be in-progress (aborted)
        status = wt_repo.git.status()
        assert "fix conflicts" not in status.lower()
        assert "both modified" not in status

        # Cleanup
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergeStrategy:
    """Tests for merge strategy parameter."""

    def test_strategy_theirs_resolves_conflicts(self, git_repo: Repo):
        """Using strategy='theirs' auto-resolves in favor of base branch."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-theirs"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/theirs-test")

        wt_repo = Repo(wt_path)
        (wt_path / "shared.txt").write_text("worktree version")
        wt_repo.index.add(["shared.txt"])
        wt_repo.index.commit("Worktree change")

        git_repo.git.checkout(base)
        (Path(git_repo.working_dir) / "shared.txt").write_text("main version")
        git_repo.index.add(["shared.txt"])
        git_repo.index.commit("Main change")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))
        result = mm.merge(
            worktree_name="theirs-test",
            base_branch=base,
            strategy="theirs",
            delete_worktree=True,
        )
        assert result.status == MergeStatus.SUCCESS


class TestMergeRebase:
    """Tests for rebase parameter."""

    def test_rebase_creates_linear_history(self, git_repo: Repo):
        """Rebase produces linear commit history."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-rebase"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/rebase-test")

        wt_repo = Repo(wt_path)
        (wt_path / "feature.txt").write_text("feature code")
        wt_repo.index.add(["feature.txt"])
        wt_repo.index.commit("Add feature")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))
        result = mm.merge(
            worktree_name="rebase-test",
            base_branch=base,
            rebase=True,
            delete_worktree=True,
        )
        assert result.status == MergeStatus.SUCCESS

    def test_rebase_conflict_aborts_by_default(self, git_repo: Repo):
        """Rebase conflicts abort by default."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-rebase-fail"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/rebase-fail")

        wt_repo = Repo(wt_path)
        (wt_path / "shared.txt").write_text("worktree version")
        wt_repo.index.add(["shared.txt"])
        wt_repo.index.commit("Worktree change")

        git_repo.git.checkout(base)
        (Path(git_repo.working_dir) / "shared.txt").write_text("main version")
        git_repo.index.add(["shared.txt"])
        git_repo.index.commit("Main change")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))
        with pytest.raises(MergeConflictError):
            mm.merge(
                worktree_name="rebase-fail",
                base_branch=base,
                rebase=True,
                delete_worktree=False,
            )

        # Rebase should be aborted (not in-progress)
        wt_repo = Repo(wt_path)
        rebase_dir = Path(wt_repo.git_dir) / "rebase-merge"
        assert not rebase_dir.exists()

        # Cleanup
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestGetConflictFiles:
    """Tests for _get_conflict_files static method."""

    def test_returns_empty_for_clean_repo(self, git_repo: Repo):
        assert MergeManager._get_conflict_files(git_repo) == []


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

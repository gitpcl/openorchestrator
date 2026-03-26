"""Tests for the merge module using real git repo fixtures."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from git import Repo
from git.exc import GitCommandError

from open_orchestrator.core.merge import (
    MergeConflictError,
    MergeError,
    MergeManager,
    MergeResult,
    MergeStatus,
)
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus


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


class TestAutoCommitWorktree:
    def test_clean_worktree_returns_zero(self, merge_manager: MergeManager, git_repo: Repo):
        wt_path = Path(git_repo.working_dir).parent / "wt-clean"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/clean")
        assert merge_manager.auto_commit_worktree("clean") == 0
        git_repo.git.worktree("remove", str(wt_path), "--force")

    def test_dirty_worktree_commits_and_returns_count(self, merge_manager: MergeManager, git_repo: Repo):
        wt_path = Path(git_repo.working_dir).parent / "wt-autocommit"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/autocommit")
        # Create untracked files
        (wt_path / "new_file.py").write_text("print('hello')")
        (wt_path / "another.txt").write_text("data")
        count = merge_manager.auto_commit_worktree("autocommit")
        assert count == 2
        # Verify the commit was made
        wt_repo = Repo(wt_path)
        assert "feat(auto)" in wt_repo.head.commit.message
        # Verify worktree is now clean
        assert merge_manager.check_uncommitted_changes("autocommit") == []
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


class TestGetConflictFilesWithContent:
    """Tests for _get_conflict_files when diff returns conflicted files."""

    def test_returns_conflicted_files_from_diff_output(self, git_repo: Repo):
        mock_git = MagicMock()
        mock_git.diff.return_value = "file1.txt\nfile2.py\n"
        repo_mock = MagicMock()
        repo_mock.git = mock_git
        result = MergeManager._get_conflict_files(repo_mock)
        assert result == ["file1.txt", "file2.py"]
        mock_git.diff.assert_called_once_with("--name-only", "--diff-filter=U")

    def test_returns_empty_on_git_command_error(self, git_repo: Repo):
        mock_git = MagicMock()
        mock_git.diff.side_effect = GitCommandError("diff", 1)
        repo_mock = MagicMock()
        repo_mock.git = mock_git
        result = MergeManager._get_conflict_files(repo_mock)
        assert result == []

    def test_filters_empty_strings_from_diff_output(self, git_repo: Repo):
        mock_git = MagicMock()
        mock_git.diff.return_value = "file1.txt\n\n"
        repo_mock = MagicMock()
        repo_mock.git = mock_git
        result = MergeManager._get_conflict_files(repo_mock)
        assert result == ["file1.txt"]


class TestGetBaseBranchRemoteFallback:
    """Tests for get_base_branch remote symbolic-ref fallback (line 110)."""

    def test_uses_symbolic_ref_fallback_when_no_local_candidate(self, tmp_path: Path):
        """When main/master/develop don't exist, falls back to remote HEAD."""
        repo = Repo.init(tmp_path)
        repo.config_writer().set_value("user", "name", "Test").release()
        repo.config_writer().set_value("user", "email", "test@test.com").release()
        (tmp_path / "README.md").write_text("# Test\n")
        repo.index.add(["README.md"])
        repo.index.commit("Initial commit")
        repo.git.branch("-m", "unusual-branch")

        mm = MergeManager(repo_path=tmp_path)
        # Replace mm.repo.git with a mock that fails rev_parse for all candidates
        # but succeeds on symbolic_ref
        mock_git = MagicMock()
        mock_git.rev_parse.side_effect = GitCommandError("rev-parse", 128)
        mock_git.symbolic_ref.return_value = "refs/remotes/origin/develop"
        mm.repo.git = mock_git

        result = mm.get_base_branch("feat/test")
        assert result == "develop"


class TestCheckUncommittedChangesError:
    """Tests for check_uncommitted_changes WorktreeNotFoundError path (line 130-131)."""

    def test_raises_merge_error_when_worktree_not_found(self, merge_manager: MergeManager):
        from open_orchestrator.core.worktree import WorktreeNotFoundError
        with patch.object(merge_manager.wt_manager, "get", side_effect=WorktreeNotFoundError("ghost")):
            with pytest.raises(MergeError, match="Worktree not found"):
                merge_manager.check_uncommitted_changes("ghost")


class TestCheckUncommittedChangesDeduplicate:
    """Tests for deduplication in check_uncommitted_changes (line 141->140)."""

    def test_deduplicates_files_across_changed_staged_untracked(self, merge_manager: MergeManager, git_repo: Repo):
        wt_path = Path(git_repo.working_dir).parent / "wt-dedup"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/dedup")

        wt_repo = Repo(wt_path)
        # Create a tracked file, modify it, and also stage a copy so same file appears in both
        (wt_path / "dup.txt").write_text("original")
        wt_repo.index.add(["dup.txt"])
        wt_repo.index.commit("Add dup.txt")
        # Now modify it (shows in index.diff(None)) and stage a change to HEAD (index.diff("HEAD"))
        (wt_path / "dup.txt").write_text("modified unstaged")

        result = merge_manager.check_uncommitted_changes("dedup")
        # dup.txt should appear exactly once
        assert result.count("dup.txt") == 1

        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestGetModifiedFilesError:
    """Tests for get_modified_files GitCommandError path (line 176-177)."""

    def test_returns_empty_on_git_command_error(self, merge_manager: MergeManager):
        mock_git = MagicMock()
        mock_git.diff.side_effect = GitCommandError("diff", 128)
        merge_manager.repo.git = mock_git
        result = merge_manager.get_modified_files("feat/test", "main")
        assert result == []


class TestCountCommitsAheadError:
    """Tests for count_commits_ahead GitCommandError path (line 216-217)."""

    def test_returns_zero_on_git_command_error(self, merge_manager: MergeManager):
        mock_git = MagicMock()
        mock_git.rev_list.side_effect = GitCommandError("rev-list", 128)
        merge_manager.repo.git = mock_git
        result = merge_manager.count_commits_ahead("feat/test", "main")
        assert result == 0


class TestCheckFileOverlaps:
    """Tests for check_file_overlaps (lines 185-201)."""

    def test_returns_empty_when_no_modified_files(self, merge_manager: MergeManager, git_repo: Repo):
        wt_path = Path(git_repo.working_dir).parent / "wt-overlaps"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/overlaps")

        # No commits -> no modified files
        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker:
            mock_tracker.return_value.get_all_statuses.return_value = []
            result = merge_manager.check_file_overlaps("overlaps", base_branch=git_repo.active_branch.name)

        assert result == {}
        git_repo.git.worktree("remove", str(wt_path), "--force")

    def test_returns_overlap_when_other_worktree_shares_file(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-overlap-a"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/overlap-a")
        wt_repo = Repo(wt_path)
        (wt_path / "shared.txt").write_text("from a")
        wt_repo.index.add(["shared.txt"])
        wt_repo.index.commit("Add shared.txt in A")

        other_status = WorktreeAIStatus(
            worktree_name="other-wt",
            worktree_path="/some/path",
            branch="feat/other",
            activity_status=AIActivityStatus.WORKING,
            modified_files=["shared.txt"],
        )

        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker:
            mock_tracker.return_value.get_all_statuses.return_value = [other_status]
            result = merge_manager.check_file_overlaps("overlap-a", base_branch=base)

        assert "shared.txt" in result
        assert "other-wt" in result["shared.txt"]
        git_repo.git.worktree("remove", str(wt_path), "--force")

    def test_skips_self_in_status_list(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-self-skip"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/self-skip")
        wt_repo = Repo(wt_path)
        (wt_path / "myfile.txt").write_text("content")
        wt_repo.index.add(["myfile.txt"])
        wt_repo.index.commit("Add myfile.txt")

        self_status = WorktreeAIStatus(
            worktree_name="self-skip",
            worktree_path=str(wt_path),
            branch="feat/self-skip",
            activity_status=AIActivityStatus.WORKING,
            modified_files=["myfile.txt"],
        )

        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker:
            mock_tracker.return_value.get_all_statuses.return_value = [self_status]
            result = merge_manager.check_file_overlaps("self-skip", base_branch=base)

        # Should be empty — self is excluded
        assert result == {}
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestPlanMergeOrder:
    """Tests for plan_merge_order (lines 237-262)."""

    def test_returns_empty_when_no_eligible_statuses(self, merge_manager: MergeManager):
        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker:
            mock_tracker.return_value.get_all_statuses.return_value = []
            result = merge_manager.plan_merge_order()
        assert result == []

    def test_filters_non_completed_statuses(self, merge_manager: MergeManager, git_repo: Repo):
        working_status = WorktreeAIStatus(
            worktree_name="wt-working",
            worktree_path="/path",
            branch="feat/working",
            activity_status=AIActivityStatus.WORKING,
        )
        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker:
            mock_tracker.return_value.get_all_statuses.return_value = [working_status]
            result = merge_manager.plan_merge_order()
        assert result == []

    def test_sorts_by_commits_ahead_ascending(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name

        # Create two worktrees with different commit counts
        wt_a = Path(git_repo.working_dir).parent / "wt-plan-a"
        wt_b = Path(git_repo.working_dir).parent / "wt-plan-b"
        git_repo.git.worktree("add", str(wt_a), "-b", "feat/plan-a")
        git_repo.git.worktree("add", str(wt_b), "-b", "feat/plan-b")

        # wt_b gets 2 commits, wt_a gets 1 commit
        repo_a = Repo(wt_a)
        (wt_a / "a.txt").write_text("a")
        repo_a.index.add(["a.txt"])
        repo_a.index.commit("Commit A1")

        repo_b = Repo(wt_b)
        (wt_b / "b1.txt").write_text("b1")
        repo_b.index.add(["b1.txt"])
        repo_b.index.commit("Commit B1")
        (wt_b / "b2.txt").write_text("b2")
        repo_b.index.add(["b2.txt"])
        repo_b.index.commit("Commit B2")

        status_a = WorktreeAIStatus(
            worktree_name="plan-a",
            worktree_path=str(wt_a),
            branch="feat/plan-a",
            activity_status=AIActivityStatus.COMPLETED,
        )
        status_b = WorktreeAIStatus(
            worktree_name="plan-b",
            worktree_path=str(wt_b),
            branch="feat/plan-b",
            activity_status=AIActivityStatus.COMPLETED,
        )

        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker:
            mock_tracker.return_value.get_all_statuses.return_value = [status_b, status_a]
            with patch.object(merge_manager, "check_file_overlaps", return_value={}):
                result = merge_manager.plan_merge_order(base_branch=base)

        # plan-a has 1 commit, plan-b has 2 — plan-a should be first
        names = [r[0] for r in result]
        assert names.index("plan-a") < names.index("plan-b")

        git_repo.git.worktree("remove", str(wt_a), "--force")
        git_repo.git.worktree("remove", str(wt_b), "--force")

    def test_uses_dependency_order_when_provided(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name

        wt_c = Path(git_repo.working_dir).parent / "wt-dep-c"
        wt_d = Path(git_repo.working_dir).parent / "wt-dep-d"
        git_repo.git.worktree("add", str(wt_c), "-b", "feat/dep-c")
        git_repo.git.worktree("add", str(wt_d), "-b", "feat/dep-d")

        repo_c = Repo(wt_c)
        (wt_c / "c.txt").write_text("c")
        repo_c.index.add(["c.txt"])
        repo_c.index.commit("C commit 1")

        repo_d = Repo(wt_d)
        (wt_d / "d.txt").write_text("d")
        repo_d.index.add(["d.txt"])
        repo_d.index.commit("D commit 1")

        status_c = WorktreeAIStatus(
            worktree_name="dep-c",
            worktree_path=str(wt_c),
            branch="feat/dep-c",
            activity_status=AIActivityStatus.COMPLETED,
        )
        status_d = WorktreeAIStatus(
            worktree_name="dep-d",
            worktree_path=str(wt_d),
            branch="feat/dep-d",
            activity_status=AIActivityStatus.COMPLETED,
        )

        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker:
            mock_tracker.return_value.get_all_statuses.return_value = [status_c, status_d]
            with patch.object(merge_manager, "check_file_overlaps", return_value={}):
                # dep-d first in dependency order
                result = merge_manager.plan_merge_order(
                    base_branch=base,
                    dependency_order=["dep-d", "dep-c"],
                )

        names = [r[0] for r in result]
        assert names.index("dep-d") < names.index("dep-c")

        git_repo.git.worktree("remove", str(wt_c), "--force")
        git_repo.git.worktree("remove", str(wt_d), "--force")

    def test_skips_worktree_that_raises_exception(self, merge_manager: MergeManager):
        status_bad = WorktreeAIStatus(
            worktree_name="bad-wt",
            worktree_path="/nonexistent",
            branch="feat/bad",
            activity_status=AIActivityStatus.COMPLETED,
        )
        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker:
            mock_tracker.return_value.get_all_statuses.return_value = [status_bad]
            with patch.object(merge_manager, "get_base_branch", side_effect=Exception("no base")):
                result = merge_manager.plan_merge_order()
        # Bad worktree is skipped, no error raised
        assert result == []

    def test_includes_waiting_status(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-waiting"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/waiting")
        wt_repo = Repo(wt_path)
        (wt_path / "w.txt").write_text("w")
        wt_repo.index.add(["w.txt"])
        wt_repo.index.commit("Waiting commit")

        status_w = WorktreeAIStatus(
            worktree_name="waiting",
            worktree_path=str(wt_path),
            branch="feat/waiting",
            activity_status=AIActivityStatus.WAITING,
        )

        with patch("open_orchestrator.core.status.StatusTracker") as mock_tracker:
            mock_tracker.return_value.get_all_statuses.return_value = [status_w]
            with patch.object(merge_manager, "check_file_overlaps", return_value={}):
                result = merge_manager.plan_merge_order(base_branch=base)

        assert len(result) == 1
        assert result[0][0] == "waiting"
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergeDetachedWorktree:
    """Tests for detached HEAD worktree (line 301)."""

    def test_raises_when_worktree_branch_is_detached(self, merge_manager: MergeManager, git_repo: Repo):

        detached_worktree = MagicMock()
        detached_worktree.branch = "(detached)"
        with patch.object(merge_manager.wt_manager, "get", return_value=detached_worktree):
            with pytest.raises(MergeError, match="detached HEAD state"):
                merge_manager.merge(worktree_name="detached-wt", base_branch="main")

    def test_raises_when_worktree_branch_is_empty(self, merge_manager: MergeManager):
        detached_worktree = MagicMock()
        detached_worktree.branch = ""
        with patch.object(merge_manager.wt_manager, "get", return_value=detached_worktree):
            with pytest.raises(MergeError, match="detached HEAD state"):
                merge_manager.merge(worktree_name="empty-branch-wt", base_branch="main")


class TestMergePhase1NonConflictError:
    """Tests for Phase 1 non-conflict GitCommandError (line 413)."""

    def test_raises_merge_error_on_non_conflict_phase1_failure(self, merge_manager: MergeManager, git_repo: Repo):
        """Phase 1 merge raises GitCommandError with no conflict files -> MergeError."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-phase1-err"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/phase1-err")
        wt_repo = Repo(wt_path)
        (wt_path / "f.txt").write_text("content")
        wt_repo.index.add(["f.txt"])
        wt_repo.index.commit("Add f.txt")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))

        # Create a mock wt_repo whose git object raises on merge but returns empty diff
        mock_wt_git = MagicMock()
        mock_wt_git.fetch.side_effect = GitCommandError("fetch", 1)
        mock_wt_git.rev_parse.side_effect = GitCommandError("rev-parse", 128)
        mock_wt_git.merge.side_effect = GitCommandError("merge", 1)
        mock_wt_git.diff.return_value = ""  # No conflict files -> non-conflict error path

        mock_wt_repo = MagicMock()
        mock_wt_repo.git = mock_wt_git

        with patch("open_orchestrator.core.merge.Repo", return_value=mock_wt_repo):
            with pytest.raises(MergeError, match="Phase 1 merge failed"):
                mm.merge(worktree_name="phase1-err", base_branch=base, delete_worktree=False)

        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergeDetachedMainRepo:
    """Tests for detached HEAD in main repo during Phase 2 (lines 419-420)."""

    def test_raises_when_main_repo_is_detached(self, git_repo: Repo):
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-detached-main"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/detached-main")
        wt_repo = Repo(wt_path)
        (wt_path / "f.txt").write_text("content")
        wt_repo.index.add(["f.txt"])
        wt_repo.index.commit("Add f.txt")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))

        # active_branch is a property on the Repo class that raises TypeError when detached
        detached_property = property(lambda self: (_ for _ in ()).throw(TypeError("HEAD is detached")))
        with patch.object(type(mm.repo), "active_branch", detached_property):
            with pytest.raises(MergeError, match="detached HEAD state"):
                mm.merge(worktree_name="detached-main", base_branch=base, delete_worktree=False)

        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergePhase2Failure:
    """Tests for Phase 2 merge failure (lines 434-452)."""

    def test_raises_merge_error_when_phase2_checkout_fails(self, merge_manager: MergeManager, git_repo: Repo):
        """Phase 2 failure during checkout raises MergeError."""
        from open_orchestrator.models.worktree_info import WorktreeInfo

        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-phase2-fail"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/phase2-fail")
        wt_repo = Repo(wt_path)
        (wt_path / "f.txt").write_text("content")
        wt_repo.index.add(["f.txt"])
        wt_repo.index.commit("Add f.txt")

        worktree_info = WorktreeInfo(
            path=wt_path,
            branch="feat/phase2-fail",
            head_commit="abc1234",
        )

        # Mock wt_manager.get to return our worktree, and Phase 1 wt_repo is clean
        # Then make Phase 2 checkout fail on the main repo
        with patch.object(merge_manager.wt_manager, "get", return_value=worktree_info), \
             patch.object(merge_manager, "check_uncommitted_changes", return_value=[]), \
             patch.object(merge_manager, "count_commits_ahead", return_value=1), \
             patch("open_orchestrator.core.merge.Repo", return_value=MagicMock(
                 git=MagicMock(
                     fetch=MagicMock(),
                     rev_parse=MagicMock(side_effect=GitCommandError("rev-parse", 128)),
                     merge=MagicMock(),
                 )
             )):
            # Now make the main repo checkout fail
            original_git = merge_manager.repo.git
            mock_main_git = MagicMock()
            mock_main_git.checkout.side_effect = GitCommandError("checkout", 1)
            merge_manager.repo.git = mock_main_git
            try:
                with pytest.raises(MergeError, match="Phase 2 merge failed"):
                    merge_manager.merge(worktree_name="phase2-fail", base_branch=base, delete_worktree=False)
            finally:
                merge_manager.repo.git = original_git

        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergeWorktreeCleanupFailure:
    """Tests for worktree cleanup failure after successful merge (lines 469-474)."""

    def test_result_message_notes_cleanup_failure(self, merge_manager: MergeManager, git_repo: Repo):
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-cleanup-fail"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/cleanup-fail")
        wt_repo = Repo(wt_path)
        (wt_path / "f.txt").write_text("content")
        wt_repo.index.add(["f.txt"])
        wt_repo.index.commit("Add f.txt")

        with patch.object(merge_manager.wt_manager, "delete", side_effect=Exception("delete failed")):
            result = merge_manager.merge(
                worktree_name="cleanup-fail",
                base_branch=base,
                delete_worktree=True,
            )

        assert result.status == MergeStatus.SUCCESS
        assert result.worktree_cleaned is False
        assert "worktree cleanup failed" in result.message


class TestRebaseLeaveConflicts:
    """Tests for rebase with leave_conflicts=True (lines 345-349)."""

    def test_rebase_conflict_with_leave_conflicts_true(self, git_repo: Repo):
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-rebase-leave"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/rebase-leave")

        wt_repo = Repo(wt_path)
        (wt_path / "shared.txt").write_text("worktree version")
        wt_repo.index.add(["shared.txt"])
        wt_repo.index.commit("Worktree change")

        git_repo.git.checkout(base)
        (Path(git_repo.working_dir) / "shared.txt").write_text("main version")
        git_repo.index.add(["shared.txt"])
        git_repo.index.commit("Main change")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))
        with pytest.raises(MergeConflictError) as exc_info:
            mm.merge(
                worktree_name="rebase-leave",
                base_branch=base,
                rebase=True,
                leave_conflicts=True,
                delete_worktree=False,
            )

        assert "in-progress" in str(exc_info.value)
        # Abort rebase so cleanup works
        try:
            wt_repo.git.rebase("--abort")
        except GitCommandError:
            pass
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergeLocalRefFallback:
    """Tests for merge_ref fallback to local branch (line 335)."""

    def test_uses_local_branch_ref_when_origin_ref_not_found(self, git_repo: Repo):
        """When origin/<target> doesn't exist, merge uses local branch ref."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-local-ref"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/local-ref")
        wt_repo = Repo(wt_path)
        (wt_path / "f.txt").write_text("content")
        wt_repo.index.add(["f.txt"])
        wt_repo.index.commit("Add f.txt")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))
        # No remote configured, so origin/<base> won't exist -> falls back to local ref
        # This is the default behavior in a repo without a remote — just run the merge
        result = mm.merge(
            worktree_name="local-ref",
            base_branch=base,
            delete_worktree=True,
        )
        assert result.status == MergeStatus.SUCCESS


class TestMergeLeaveConflictsPhase1:
    """Tests for leave_conflicts path with unresolved conflicts (lines 404-405)."""

    def test_leave_conflicts_raises_with_in_progress_message(self, git_repo: Repo):
        """leave_conflicts=True should include 'in-progress' in MergeConflictError message."""
        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-leave2"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/leave2")

        wt_repo = Repo(wt_path)
        (wt_path / "conflict.txt").write_text("wt version")
        wt_repo.index.add(["conflict.txt"])
        wt_repo.index.commit("WT commit")

        git_repo.git.checkout(base)
        (Path(git_repo.working_dir) / "conflict.txt").write_text("base version")
        git_repo.index.add(["conflict.txt"])
        git_repo.index.commit("Base commit")

        mm = MergeManager(repo_path=Path(git_repo.working_dir))
        with pytest.raises(MergeConflictError) as exc_info:
            mm.merge(
                worktree_name="leave2",
                base_branch=base,
                leave_conflicts=True,
                delete_worktree=False,
            )

        assert "in-progress" in str(exc_info.value)
        # Clean up merge state
        try:
            wt_repo.git.merge("--abort")
        except GitCommandError:
            pass
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergeOriginRefUsed:
    """Tests for merge_ref = origin/<target> path (line 335)."""

    def test_uses_origin_ref_when_available(self, merge_manager: MergeManager, git_repo: Repo):
        """When origin/<target> ref exists in the worktree, it is used as merge_ref."""
        from open_orchestrator.models.worktree_info import WorktreeInfo

        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-origin-ref"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/origin-ref")
        wt_repo = Repo(wt_path)
        (wt_path / "f.txt").write_text("content")
        wt_repo.index.add(["f.txt"])
        wt_repo.index.commit("Add f.txt")

        worktree_info = WorktreeInfo(
            path=wt_path,
            branch="feat/origin-ref",
            head_commit="abc1234",
        )

        mock_wt_git = MagicMock()
        mock_wt_git.fetch.return_value = None
        # rev_parse succeeds → merge_ref = f"origin/{target_branch}"
        mock_wt_git.rev_parse.return_value = "abc123"
        mock_wt_git.merge.return_value = None  # Phase 1 succeeds

        with patch.object(merge_manager.wt_manager, "get", return_value=worktree_info), \
             patch.object(merge_manager, "check_uncommitted_changes", return_value=[]), \
             patch.object(merge_manager, "count_commits_ahead", return_value=1), \
             patch("open_orchestrator.core.merge.Repo", return_value=MagicMock(git=mock_wt_git)):
            result = merge_manager.merge(
                worktree_name="origin-ref",
                base_branch=base,
                delete_worktree=False,
            )

        assert result.status == MergeStatus.SUCCESS
        # Verify merge was called with origin/<base>
        merge_calls = mock_wt_git.merge.call_args_list
        assert any(f"origin/{base}" in str(call) for call in merge_calls)
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergeCheckUncommittedDeduplicateEmpty:
    """Tests that empty-string filenames are skipped in dedup loop (line 141->140)."""

    def test_empty_string_files_are_skipped(self, merge_manager: MergeManager, git_repo: Repo):
        """The loop skips falsy file names (empty strings from git output)."""
        wt_path = Path(git_repo.working_dir).parent / "wt-empty-files"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/empty-files")

        wt_repo_mock = MagicMock()
        # Return diff items where one has an empty a_path
        empty_item = MagicMock()
        empty_item.a_path = ""
        real_item = MagicMock()
        real_item.a_path = "real.txt"
        wt_repo_mock.index.diff.return_value = [empty_item, real_item]
        wt_repo_mock.untracked_files = []

        from open_orchestrator.models.worktree_info import WorktreeInfo
        worktree_info = WorktreeInfo(
            path=wt_path,
            branch="feat/empty-files",
            head_commit="abc1234",
        )

        with patch.object(merge_manager.wt_manager, "get", return_value=worktree_info), \
             patch("open_orchestrator.core.merge.Repo", return_value=wt_repo_mock):
            result = merge_manager.check_uncommitted_changes("empty-files")

        assert "" not in result
        assert "real.txt" in result
        git_repo.git.worktree("remove", str(wt_path), "--force")


class TestMergeFinallyDetachedHead:
    """Tests for finally block when active_branch raises TypeError (lines 446-447)."""

    def test_finally_handles_detached_head_gracefully(self, merge_manager: MergeManager, git_repo: Repo):
        """If main repo is in detached HEAD in finally, no exception is raised."""
        from open_orchestrator.models.worktree_info import WorktreeInfo

        base = git_repo.active_branch.name
        wt_path = Path(git_repo.working_dir).parent / "wt-finally-detach"
        git_repo.git.worktree("add", str(wt_path), "-b", "feat/finally-detach")
        wt_repo = Repo(wt_path)
        (wt_path / "f.txt").write_text("content")
        wt_repo.index.add(["f.txt"])
        wt_repo.index.commit("Add f.txt")

        worktree_info = WorktreeInfo(
            path=wt_path,
            branch="feat/finally-detach",
            head_commit="abc1234",
        )

        mock_wt_git = MagicMock()
        mock_wt_git.fetch.return_value = None
        mock_wt_git.rev_parse.side_effect = GitCommandError("rev-parse", 128)
        mock_wt_git.merge.return_value = None

        # Make active_branch raise TypeError during the finally block
        call_count = [0]

        def active_branch_side_effect(self):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: return actual branch name (pre-Phase 2)
                return MagicMock(name=base)
            # Subsequent calls: raise TypeError (detached HEAD in finally)
            raise TypeError("HEAD is detached")

        detached_prop = property(active_branch_side_effect)
        with patch.object(merge_manager.wt_manager, "get", return_value=worktree_info), \
             patch.object(merge_manager, "check_uncommitted_changes", return_value=[]), \
             patch.object(merge_manager, "count_commits_ahead", return_value=1), \
             patch("open_orchestrator.core.merge.Repo", return_value=MagicMock(git=mock_wt_git)), \
             patch.object(type(merge_manager.repo), "active_branch", detached_prop):
            # Should not raise — finally block handles TypeError gracefully
            result = merge_manager.merge(
                worktree_name="finally-detach",
                base_branch=base,
                delete_worktree=False,
            )

        assert result.status == MergeStatus.SUCCESS
        git_repo.git.worktree("remove", str(wt_path), "--force")

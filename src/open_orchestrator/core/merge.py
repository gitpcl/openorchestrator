"""Two-phase merge logic for completing worktree lifecycle.

Handles merging a worktree branch back into its base branch with
conflict detection and optional auto-cleanup.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from git import Repo
from git.exc import GitCommandError

from open_orchestrator.core.status import runtime_status_config
from open_orchestrator.core.worktree import WorktreeManager, WorktreeNotFoundError
from open_orchestrator.models.status import AIActivityStatus

logger = logging.getLogger(__name__)


class MergeStatus(str, Enum):
    """Result status of a merge operation."""

    SUCCESS = "success"
    CONFLICTS = "conflicts"
    ALREADY_MERGED = "already_merged"
    ERROR = "error"


@dataclass
class MergeResult:
    """Result of a merge operation."""

    status: MergeStatus
    source_branch: str
    target_branch: str
    message: str
    conflicts: list[str] = field(default_factory=list)
    commits_merged: int = 0
    worktree_cleaned: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "status": self.status.value,
            "source_branch": self.source_branch,
            "target_branch": self.target_branch,
            "message": self.message,
            "conflicts": self.conflicts,
            "commits_merged": self.commits_merged,
            "worktree_cleaned": self.worktree_cleaned,
        }


class MergeError(Exception):
    """Error during merge operation."""


class MergeConflictError(MergeError):
    """Merge resulted in conflicts that need manual resolution."""

    def __init__(self, message: str, conflicts: list[str]):
        super().__init__(message)
        self.conflicts = conflicts


class MergeManager:
    """Handles two-phase merge operations for worktree branches."""

    def __init__(self, repo_path: Path | None = None):
        self.wt_manager = WorktreeManager(repo_path)
        self.repo = self.wt_manager.repo

    @staticmethod
    def _get_conflict_files(repo: "Repo") -> list[str]:
        """Get list of conflicted files from a repo."""
        try:
            output = repo.git.diff("--name-only", "--diff-filter=U").strip()
            return [f for f in output.split("\n") if f]
        except GitCommandError:
            return []

    def get_base_branch(self, worktree_branch: str) -> str:
        """Detect the base branch for a worktree branch.

        Checks merge-base against common base branches (main, master, develop).

        Args:
            worktree_branch: The worktree's branch name.

        Returns:
            The detected base branch name.

        Raises:
            MergeError: If no base branch can be determined.
        """
        candidates = ["main", "master", "develop"]

        for candidate in candidates:
            try:
                self.repo.git.rev_parse("--verify", candidate)
                return candidate
            except GitCommandError:
                continue

        # Fallback: try the default branch from remote
        try:
            result = self.repo.git.symbolic_ref("refs/remotes/origin/HEAD")
            return str(result).replace("refs/remotes/origin/", "")
        except GitCommandError:
            pass

        raise MergeError(f"Could not determine base branch for '{worktree_branch}'. Use --base to specify explicitly.")

    def check_uncommitted_changes(self, worktree_name: str) -> list[str]:
        """Check if a worktree has uncommitted changes.

        Args:
            worktree_name: The worktree identifier.

        Returns:
            List of modified file paths, empty if clean.
        """
        try:
            worktree = self.wt_manager.get(worktree_name)
        except WorktreeNotFoundError as e:
            raise MergeError(f"Worktree not found: {worktree_name}") from e

        wt_repo = Repo(worktree.path)
        # Use b_path with a_path fallback: for newly added files (in index
        # but not in HEAD), a_path is None — only b_path is set.
        changed = [item.b_path or item.a_path for item in wt_repo.index.diff(None)]
        staged = [item.b_path or item.a_path for item in wt_repo.index.diff("HEAD")]
        untracked = wt_repo.untracked_files

        seen: set[str] = set()
        result: list[str] = []
        for f in changed + staged + list(untracked):
            if f and f not in seen:
                seen.add(f)
                result.append(f)
        return result

    def auto_commit_worktree(self, worktree_name: str) -> int:
        """Auto-commit any uncommitted work in a worktree.

        Safety net for orchestrator/batch: ensures agent-created files
        are committed before merge so they are not lost on cleanup.
        Always runs ``git add -A`` to catch untracked files that
        ``check_uncommitted_changes`` might miss.

        Returns:
            Number of files committed (0 if worktree is clean).
        """
        worktree = self.wt_manager.get(worktree_name)
        wt_repo = Repo(worktree.path)

        # Always stage everything — git add -A catches untracked files,
        # new directories, and unstaged modifications in one pass.
        wt_repo.git.add("-A")

        # Check if there's actually anything staged to commit
        staged = [item.b_path or item.a_path for item in wt_repo.index.diff("HEAD")]
        if not staged:
            return 0

        branch_desc = worktree.branch.split("/")[-1].replace("-", " ")
        wt_repo.git.commit("-m", f"feat(auto): {branch_desc}")
        logger.info(
            "Auto-committed %d file(s) in '%s': %s",
            len(staged),
            worktree_name,
            ", ".join(str(f) for f in staged[:5]) + ("..." if len(staged) > 5 else ""),
        )
        return len(staged)

    def get_modified_files(self, branch: str, base: str) -> list[str]:
        """Get files modified on branch vs base."""
        try:
            output = self.repo.git.diff("--name-only", f"{base}...{branch}")
            return [f for f in output.strip().split("\n") if f]
        except GitCommandError:
            return []

    def check_file_overlaps(self, worktree_name: str, base_branch: str | None = None) -> dict[str, list[str]]:
        """Check if this worktree's modified files overlap with other worktrees.

        Returns:
            Dict mapping overlapping file paths to list of other worktree names.
        """
        worktree = self.wt_manager.get(worktree_name)
        target = base_branch or self.get_base_branch(worktree.branch)
        my_files = set(self.get_modified_files(worktree.branch, target))
        if not my_files:
            return {}

        from open_orchestrator.core.status import StatusTracker

        tracker = StatusTracker(runtime_status_config(self.wt_manager.git_root))
        overlaps: dict[str, list[str]] = {}
        for s in tracker.get_all_statuses():
            if s.worktree_name == worktree_name:
                continue
            other_files = set(s.modified_files)
            for f in my_files & other_files:
                overlaps.setdefault(f, []).append(s.worktree_name)
        return overlaps

    def count_commits_ahead(self, branch: str, base: str) -> int:
        """Count commits on branch that are not on base.

        Args:
            branch: The feature branch.
            base: The base branch.

        Returns:
            Number of commits ahead.
        """
        try:
            output = self.repo.git.rev_list("--count", f"{base}..{branch}")
            return int(output.strip())
        except GitCommandError:
            return 0

    def plan_merge_order(
        self,
        base_branch: str | None = None,
        dependency_order: list[str] | None = None,
    ) -> list[tuple[str, int, int]]:
        """Plan optimal merge order for all completed/waiting worktrees.

        Strategy: if dependency_order is provided (from DAG), use that order.
        Otherwise, merge smallest changes first to minimize rebase churn.

        Args:
            base_branch: Target branch for merge.
            dependency_order: Optional topological order from DAG execution.

        Returns:
            List of (worktree_name, commits_ahead, overlap_count)
            sorted by dependency_order or commits_ahead ascending.
        """
        from open_orchestrator.core.status import StatusTracker

        tracker = StatusTracker(runtime_status_config(self.wt_manager.git_root))
        statuses = tracker.get_all_statuses()

        candidates: list[tuple[str, int, int]] = []
        for s in statuses:
            if s.activity_status not in (AIActivityStatus.COMPLETED, AIActivityStatus.WAITING):
                continue
            try:
                target = base_branch or self.get_base_branch(s.branch)
                ahead = self.count_commits_ahead(s.branch, target)
                overlaps = len(self.check_file_overlaps(s.worktree_name, target))
                candidates.append((s.worktree_name, ahead, overlaps))
            except Exception:
                logger.debug("Skipping %s in merge queue", s.worktree_name)
                continue

        if dependency_order:
            # Use DAG topological order
            order_map = {name: i for i, name in enumerate(dependency_order)}
            candidates.sort(key=lambda x: order_map.get(x[0], len(dependency_order)))
        else:
            # Sort: fewest commits first, then fewest overlaps
            candidates.sort(key=lambda x: (x[1], x[2]))
        return candidates

    def merge(
        self,
        worktree_name: str,
        base_branch: str | None = None,
        delete_worktree: bool = True,
        leave_conflicts: bool = False,
        strategy: str | None = None,
        rebase: bool = False,
    ) -> MergeResult:
        """Execute a two-phase merge for a worktree branch.

        Phase 1: Merge base into worktree branch (catch conflicts early in the feature branch).
        Phase 2: Merge worktree branch into base (should be fast-forward after phase 1).

        Args:
            worktree_name: The worktree identifier.
            base_branch: Target branch to merge into. Auto-detected if None.
            delete_worktree: Whether to delete the worktree after successful merge.
            leave_conflicts: If True, leave merge in-progress for manual resolution.
            strategy: Merge strategy option (e.g. "ours", "theirs") passed as -X.
            rebase: If True, rebase feature onto base instead of merging (Phase 1).

        Returns:
            MergeResult with the outcome.

        Raises:
            MergeError: If the merge cannot proceed.
            MergeConflictError: If conflicts are detected during phase 1.
        """
        # Resolve worktree
        try:
            worktree = self.wt_manager.get(worktree_name)
        except WorktreeNotFoundError as e:
            raise MergeError(f"Worktree not found: {worktree_name}") from e

        source_branch = worktree.branch
        if not source_branch or source_branch == "(detached)":
            raise MergeError(f"Worktree '{worktree_name}' is in detached HEAD state, cannot merge")

        # Resolve base branch
        target_branch = base_branch or self.get_base_branch(source_branch)

        # Check for uncommitted changes
        dirty_files = self.check_uncommitted_changes(worktree_name)
        if dirty_files:
            raise MergeError(
                f"Worktree '{worktree_name}' has uncommitted changes:\n"
                + "\n".join(f"  {f}" for f in dirty_files[:10])
                + (f"\n  ... and {len(dirty_files) - 10} more" if len(dirty_files) > 10 else "")
            )

        # Count commits to merge
        commits_ahead = self.count_commits_ahead(source_branch, target_branch)
        if commits_ahead == 0:
            return MergeResult(
                status=MergeStatus.ALREADY_MERGED,
                source_branch=source_branch,
                target_branch=target_branch,
                message=f"Branch '{source_branch}' is already up to date with '{target_branch}'",
            )

        # Phase 1: Merge base into feature branch (in the worktree)
        wt_repo = Repo(worktree.path)
        try:
            wt_repo.git.fetch("origin", target_branch, kill_after_timeout=30)
        except GitCommandError:
            pass  # Fetch failure is non-fatal; we'll try with local refs

        # Use remote ref if available, fall back to local branch
        try:
            wt_repo.git.rev_parse("--verify", f"origin/{target_branch}")
            merge_ref = f"origin/{target_branch}"
        except GitCommandError:
            merge_ref = target_branch

        if rebase:
            # Phase 1 (rebase): Rebase feature onto base for linear history
            try:
                wt_repo.git.rebase(merge_ref)
            except GitCommandError:
                conflicts = self._get_conflict_files(wt_repo)
                if not leave_conflicts:
                    try:
                        wt_repo.git.rebase("--abort")
                    except GitCommandError:
                        pass
                raise MergeConflictError(
                    f"Rebase conflicts rebasing '{source_branch}' onto '{target_branch}'"
                    + (" (rebase left in-progress for manual resolution)" if leave_conflicts else ""),
                    conflicts=conflicts,
                )
        else:
            # Phase 1 (merge): Merge base into feature branch
            try:
                merge_args = [merge_ref, "--no-edit"]
                if strategy:
                    merge_args.extend(["-X", strategy])
                wt_repo.git.merge(*merge_args)
            except GitCommandError as e:
                conflicts = self._get_conflict_files(wt_repo)

                if conflicts:
                    # Try AI-powered conflict resolution before aborting
                    resolved = False
                    try:
                        from open_orchestrator.config import load_config

                        config = load_config()
                        if config.agno.enabled and config.agno.auto_resolve_conflicts:
                            from open_orchestrator.core.intelligence import AgnoConflictResolver

                            conflicted_contents: dict[str, str] = {}
                            for cf in conflicts:
                                cf_path = Path(worktree.path) / cf
                                if cf_path.exists():
                                    conflicted_contents[cf] = cf_path.read_text(encoding="utf-8", errors="replace")

                            if conflicted_contents:
                                resolution = AgnoConflictResolver(config.agno, repo_path=str(worktree.path)).resolve(
                                    conflicted_files=conflicted_contents,
                                    source_branch=source_branch,
                                    target_branch=target_branch,
                                )
                                if resolution.confidence > 0.8 and not resolution.requires_human and resolution.resolutions:
                                    for file_path, content in resolution.resolutions.items():
                                        resolved_path = Path(worktree.path) / file_path
                                        resolved_path.write_text(content, encoding="utf-8")
                                        wt_repo.git.add(file_path)
                                    wt_repo.git.commit("-m", f"fix: auto-resolve merge conflicts ({resolution.explanation[:60]})")
                                    resolved = True
                                    logger.info("AI resolved %d conflict(s): %s", len(conflicts), resolution.explanation)
                    except ImportError:
                        pass
                    except Exception as exc:
                        logger.debug("AI conflict resolution failed: %s", exc)

                    if not resolved:
                        if not leave_conflicts:
                            try:
                                wt_repo.git.merge("--abort")
                            except GitCommandError:
                                pass

                        raise MergeConflictError(
                            f"Merge conflicts detected when merging '{target_branch}' into '{source_branch}'"
                            + (" (merge left in-progress for manual resolution)" if leave_conflicts else ""),
                            conflicts=conflicts,
                        )

                raise MergeError(f"Phase 1 merge failed: {e}") from e

        # Phase 2: Merge feature branch into base (from main repo)
        # Switch to base branch in the main repo
        try:
            original_branch = self.repo.active_branch.name
        except TypeError:
            raise MergeError("Main repository is in detached HEAD state. Checkout a branch before running merge.")

        # Auto-stash main repo if dirty (prevents checkout failure)
        # -u includes untracked files so .harness/ etc. don't block checkout
        main_stashed = False
        if self.repo.is_dirty(untracked_files=True):
            self.repo.git.stash("push", "-u", "-m", "owt-merge-autostash")
            main_stashed = True

        # Remove gitignored untracked files that would block checkout/merge
        # -X (uppercase) only removes gitignored files — safe for user's work
        try:
            cleaned = self.repo.git.clean("-fdX")
            if cleaned:
                logger.debug("Cleaned gitignored files before Phase 2: %s", cleaned)
        except GitCommandError as e:
            logger.debug("git clean before Phase 2 skipped: %s", e)

        try:
            self.repo.git.checkout(target_branch)
            self.repo.git.merge(source_branch, "--no-edit")
        except GitCommandError as e:
            # Restore original branch
            try:
                self.repo.git.checkout(original_branch)
            except GitCommandError as restore_err:
                logger.warning("Could not restore branch '%s' after failed merge: %s", original_branch, restore_err)
            raise MergeError(f"Phase 2 merge failed: {e}") from e
        finally:
            # Restore original branch if different
            current: str | None = None
            try:
                current = self.repo.active_branch.name
            except TypeError:
                pass
            if current != original_branch:
                try:
                    self.repo.git.checkout(original_branch)
                except GitCommandError as restore_err:
                    logger.warning("Could not restore branch '%s' in finally block: %s", original_branch, restore_err)
            # Pop stash after restoring branch (only if stash was created)
            if main_stashed:
                try:
                    stash_list = self.repo.git.stash("list")
                    if "owt-merge-autostash" in stash_list:
                        self.repo.git.stash("pop")
                except GitCommandError as stash_err:
                    logger.warning("Could not pop stash: %s. Run 'git stash pop' manually.", stash_err)

        result = MergeResult(
            status=MergeStatus.SUCCESS,
            source_branch=source_branch,
            target_branch=target_branch,
            message=f"Successfully merged '{source_branch}' into '{target_branch}'",
            commits_merged=commits_ahead,
        )

        # Auto-cleanup worktree
        if delete_worktree:
            try:
                self.wt_manager.delete(worktree_name, force=True)
                result.worktree_cleaned = True
            except Exception:
                logger.debug("Worktree cleanup failed for %s", worktree_name, exc_info=True)
                result.message += " (worktree cleanup failed — run 'owt delete' manually)"

        return result

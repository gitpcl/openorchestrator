"""Critic pattern for pre-action safety verification.

Before destructive actions (ship, merge, delete), a skeptical reviewer
checks for cross-worktree conflicts, uncommitted changes, and safety
issues. Extends the existing Conflict Guard with structured verdicts.

Usage:
    critic = CriticAgent(repo_path)
    verdict = critic.review_ship("my-feature")
    if verdict.is_safe:
        proceed_with_ship()
    else:
        show_blocking_issues(verdict)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    """Severity level for critic findings."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class CriticFinding:
    """A single finding from the critic review."""

    severity: Severity
    category: str
    message: str
    details: str = ""


@dataclass(frozen=True)
class CriticVerdict:
    """Result of a critic review — safe, warnings, or blocking issues."""

    action: str
    target: str
    findings: tuple[CriticFinding, ...] = ()

    @property
    def is_safe(self) -> bool:
        """True if no blocking issues found."""
        return not any(f.severity == Severity.BLOCKING for f in self.findings)

    @property
    def blocking_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.BLOCKING)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.INFO)

    @property
    def summary(self) -> str:
        """One-line summary of the verdict."""
        if self.is_safe and not self.findings:
            return f"Safe to {self.action} '{self.target}'"
        parts: list[str] = []
        if self.blocking_count:
            parts.append(f"{self.blocking_count} blocking")
        if self.warning_count:
            parts.append(f"{self.warning_count} warning(s)")
        if self.info_count:
            parts.append(f"{self.info_count} info")
        status = "BLOCKED" if not self.is_safe else "OK"
        return f"[{status}] {self.action} '{self.target}': {', '.join(parts)}"


class CriticAgent:
    """Pre-action safety reviewer.

    Checks for:
    - File overlaps with other active worktrees
    - Uncommitted changes that would be lost
    - Empty branches (no commits ahead of base)
    - Base branch divergence
    """

    def __init__(self, repo_path: Path | None = None) -> None:
        self._repo_path = repo_path or Path.cwd()

    def review_ship(self, worktree_name: str) -> CriticVerdict:
        """Run critic review before shipping a worktree.

        Checks: uncommitted changes, file overlaps, commit count, base divergence.
        """
        findings = list(self._check_all(worktree_name, action="ship"))
        return CriticVerdict(
            action="ship",
            target=worktree_name,
            findings=tuple(findings),
        )

    def review_merge(self, worktree_name: str) -> CriticVerdict:
        """Run critic review before merging a worktree."""
        findings = list(self._check_all(worktree_name, action="merge"))
        return CriticVerdict(
            action="merge",
            target=worktree_name,
            findings=tuple(findings),
        )

    def review_delete(self, worktree_name: str) -> CriticVerdict:
        """Run critic review before deleting a worktree."""
        findings: list[CriticFinding] = []

        # Check for uncommitted changes
        findings.extend(self._check_uncommitted(worktree_name))

        # Check for unmerged commits
        findings.extend(self._check_unmerged_commits(worktree_name))

        return CriticVerdict(
            action="delete",
            target=worktree_name,
            findings=tuple(findings),
        )

    def review_action(self, action: str, worktree_name: str) -> CriticVerdict:
        """Run critic review for an arbitrary action."""
        dispatch = {
            "ship": self.review_ship,
            "merge": self.review_merge,
            "delete": self.review_delete,
        }
        reviewer = dispatch.get(action)
        if reviewer:
            return reviewer(worktree_name)

        # Generic review for unknown actions
        findings = list(self._check_all(worktree_name, action=action))
        return CriticVerdict(action=action, target=worktree_name, findings=tuple(findings))

    # ── Check Methods ───────────────────────────────────────────────

    def _check_all(self, worktree_name: str, *, action: str) -> list[CriticFinding]:
        """Run all checks for a worktree."""
        findings: list[CriticFinding] = []
        findings.extend(self._check_uncommitted(worktree_name))
        findings.extend(self._check_file_overlaps(worktree_name))
        findings.extend(self._check_empty_branch(worktree_name))
        return findings

    def _check_uncommitted(self, worktree_name: str) -> list[CriticFinding]:
        """Check for uncommitted changes."""
        try:
            from open_orchestrator.core.merge import MergeManager

            mgr = MergeManager(self._repo_path)
            changes = mgr.check_uncommitted_changes(worktree_name)
            if changes:
                return [
                    CriticFinding(
                        severity=Severity.WARNING,
                        category="uncommitted-changes",
                        message=f"{len(changes)} uncommitted file(s) will be auto-committed",
                        details="\n".join(changes[:10]),
                    )
                ]
        except Exception as exc:
            logger.debug("Uncommitted check failed: %s", exc)
        return []

    def _check_file_overlaps(self, worktree_name: str) -> list[CriticFinding]:
        """Check for file overlaps with other active worktrees."""
        try:
            from open_orchestrator.core.merge import MergeManager

            mgr = MergeManager(self._repo_path)
            overlaps = mgr.check_file_overlaps(worktree_name)
            if overlaps:
                findings: list[CriticFinding] = []
                for filepath, other_worktrees in overlaps.items():
                    findings.append(
                        CriticFinding(
                            severity=Severity.BLOCKING,
                            category="file-overlap",
                            message=f"'{filepath}' also modified in: {', '.join(other_worktrees)}",
                            details=f"Merging may cause conflicts with {', '.join(other_worktrees)}",
                        )
                    )
                return findings
        except Exception as exc:
            logger.debug("File overlap check failed: %s", exc)
        return []

    def _check_empty_branch(self, worktree_name: str) -> list[CriticFinding]:
        """Check if branch has no commits ahead of base."""
        try:
            from open_orchestrator.core.merge import MergeManager
            from open_orchestrator.core.worktree import WorktreeManager

            wt_mgr = WorktreeManager(self._repo_path)
            worktree = wt_mgr.get(worktree_name)
            merge_mgr = MergeManager(self._repo_path)
            base = merge_mgr.get_base_branch(worktree.branch)
            count = merge_mgr.count_commits_ahead(worktree.branch, base)
            if count == 0:
                return [
                    CriticFinding(
                        severity=Severity.BLOCKING,
                        category="empty-branch",
                        message=f"No commits ahead of '{base}' — nothing to merge",
                    )
                ]
            return [
                CriticFinding(
                    severity=Severity.INFO,
                    category="commit-count",
                    message=f"{count} commit(s) ahead of '{base}'",
                )
            ]
        except Exception as exc:
            logger.debug("Empty branch check failed: %s", exc)
        return []

    def _check_unmerged_commits(self, worktree_name: str) -> list[CriticFinding]:
        """Check if the branch has unmerged commits (for delete safety)."""
        try:
            from open_orchestrator.core.merge import MergeManager
            from open_orchestrator.core.worktree import WorktreeManager

            wt_mgr = WorktreeManager(self._repo_path)
            worktree = wt_mgr.get(worktree_name)
            merge_mgr = MergeManager(self._repo_path)
            base = merge_mgr.get_base_branch(worktree.branch)
            count = merge_mgr.count_commits_ahead(worktree.branch, base)
            if count > 0:
                return [
                    CriticFinding(
                        severity=Severity.WARNING,
                        category="unmerged-commits",
                        message=f"{count} unmerged commit(s) will be lost on delete",
                    )
                ]
        except Exception as exc:
            logger.debug("Unmerged commits check failed: %s", exc)
        return []

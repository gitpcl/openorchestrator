"""
PR linking service for GitHub integration.

This module provides functionality to:
- Link worktrees to GitHub Pull Requests
- Auto-detect PRs from branch names
- Check PR status using gh CLI or GitHub API
- Support cleanup based on merged PRs
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from open_orchestrator.models.pr_info import (
    PRInfo,
    PRLinkResult,
    PRStatus,
    PRStore,
)
from open_orchestrator.utils.io import atomic_write_text, shared_file_lock

logger = logging.getLogger(__name__)


class PRLinkError(Exception):
    """Base exception for PR linking operations."""


class PRNotFoundError(PRLinkError):
    """Raised when a PR cannot be found."""


class GitHubAPIError(PRLinkError):
    """Raised when GitHub API call fails."""


@dataclass
class PRLinkerConfig:
    """Configuration for PR linking."""

    storage_path: Path | None = None
    github_token: str | None = None
    auto_link_prs: bool = True
    branch_pr_pattern: str = r".*#(\d+).*"
    default_remote: str = "origin"
    use_gh_cli: bool = True
    cache_duration_seconds: int = 300


class PRLinker:
    """
    Manages PR associations with worktrees.

    This service links worktrees to GitHub PRs, allowing for
    PR-centric workflows like cleanup based on merged PRs.
    """

    DEFAULT_STORAGE_FILENAME = "pr_links.json"

    def __init__(self, config: PRLinkerConfig | None = None):
        self.config = config or PRLinkerConfig()
        self._storage_path = self.config.storage_path or self._get_default_path()
        self._store: PRStore = PRStore()
        self._load_store()

    def _get_default_path(self) -> Path:
        """Get default path for PR link storage."""
        return Path.home() / ".open-orchestrator" / self.DEFAULT_STORAGE_FILENAME

    def _load_store(self) -> None:
        """Load PR store from persistent storage."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path) as f:
                    with shared_file_lock(f):
                        data = json.load(f)
                        self._store = PRStore.model_validate(data)
            except (OSError, json.JSONDecodeError, ValueError):
                self._store = PRStore()
        else:
            self._store = PRStore()

    def _save_store(self) -> None:
        """Persist PR store to storage."""
        data = json.dumps(
            self._store.model_dump(mode="json"),
            indent=2,
            default=str,
        )
        atomic_write_text(self._storage_path, data, perms=0o600)

    def _get_remote_url(self, worktree_path: str) -> tuple[str, str] | None:
        """
        Get the GitHub owner and repo from the git remote.

        Returns:
            Tuple of (owner, repo) or None if not a GitHub repo
        """
        try:
            result = subprocess.run(
                ["git", "-C", worktree_path, "remote", "get-url", self.config.default_remote],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            url = result.stdout.strip()

            # Parse GitHub URL patterns
            patterns = [
                r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
                r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$",
            ]

            for pattern in patterns:
                match = re.match(pattern, url)
                if match:
                    return match.group(1), match.group(2)

            return None

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def _extract_pr_from_branch(self, branch: str) -> int | None:
        """
        Extract PR number from branch name using configured pattern.

        Args:
            branch: Git branch name

        Returns:
            PR number if found, None otherwise
        """
        try:
            match = re.search(self.config.branch_pr_pattern, branch)
            if match:
                return int(match.group(1))
        except (re.error, ValueError, IndexError):
            pass

        return None

    def _check_pr_status_gh(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> tuple[PRStatus, str | None] | None:
        """
        Check PR status using gh CLI.

        Returns:
            Tuple of (status, title) or None if failed
        """
        try:
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "view",
                    str(pr_number),
                    "--repo",
                    f"{owner}/{repo}",
                    "--json",
                    "state,title,isDraft",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )

            data = json.loads(result.stdout)
            state = data.get("state", "").upper()
            title = data.get("title")
            is_draft = data.get("isDraft", False)

            if state == "MERGED":
                status = PRStatus.MERGED
            elif state == "CLOSED":
                status = PRStatus.CLOSED
            elif is_draft:
                status = PRStatus.DRAFT
            elif state == "OPEN":
                status = PRStatus.OPEN
            else:
                status = PRStatus.UNKNOWN

            return status, title

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

    def link_pr(
        self,
        worktree_name: str,
        worktree_path: str,
        branch: str,
        pr_number: int | None = None,
        check_status: bool = True,
    ) -> PRLinkResult:
        """
        Link a worktree to a GitHub PR.

        Args:
            worktree_name: Name of the worktree
            worktree_path: Path to the worktree
            branch: Git branch name
            pr_number: PR number (None to auto-detect from branch)
            check_status: Whether to fetch current PR status

        Returns:
            PRLinkResult with details of the operation
        """
        # Get repo info
        repo_info = self._get_remote_url(worktree_path)

        if not repo_info:
            return PRLinkResult(
                success=False,
                worktree_name=worktree_name,
                message="Could not determine GitHub repository from remote",
            )

        owner, repo = repo_info

        # Auto-detect PR number from branch if not provided
        auto_detected = False
        if pr_number is None:
            pr_number = self._extract_pr_from_branch(branch)
            auto_detected = True

            if pr_number is None:
                return PRLinkResult(
                    success=False,
                    worktree_name=worktree_name,
                    message=f"Could not detect PR number from branch '{branch}'",
                )

        # Build PR URL
        pr_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}"

        # Check PR status if requested
        status = PRStatus.UNKNOWN
        title = None

        if check_status and self.config.use_gh_cli:
            status_result = self._check_pr_status_gh(owner, repo, pr_number)
            if status_result:
                status, title = status_result

        # Create PR info
        pr_info = PRInfo(
            worktree_name=worktree_name,
            repo_owner=owner,
            repo_name=repo,
            pr_number=pr_number,
            pr_url=pr_url,
            branch=branch,
            title=title,
            status=status,
            auto_detected=auto_detected,
            last_checked=datetime.now() if check_status else None,
        )

        self._store.set_pr(pr_info)
        self._save_store()

        return PRLinkResult(
            success=True,
            worktree_name=worktree_name,
            pr_number=pr_number,
            pr_url=pr_url,
            auto_detected=auto_detected,
            message=f"Linked to PR #{pr_number}",
        )

    def unlink_pr(self, worktree_name: str) -> bool:
        """
        Remove PR link for a worktree.

        Returns:
            True if removed, False if not found
        """
        removed = self._store.remove_pr(worktree_name)
        if removed:
            self._save_store()
        return removed

    def get_pr(self, worktree_name: str) -> PRInfo | None:
        """Get PR info for a worktree."""
        return self._store.get_pr(worktree_name)

    def get_all_prs(self) -> list[PRInfo]:
        """Get all PR links."""
        return self._store.get_all_prs()

    def refresh_pr_status(self, worktree_name: str) -> PRInfo | None:
        """
        Refresh the PR status for a worktree.

        Args:
            worktree_name: Name of the worktree

        Returns:
            Updated PRInfo or None if not found
        """
        pr_info = self._store.get_pr(worktree_name)

        if not pr_info:
            return None

        if self.config.use_gh_cli:
            status_result = self._check_pr_status_gh(
                pr_info.repo_owner,
                pr_info.repo_name,
                pr_info.pr_number,
            )

            if status_result:
                status, title = status_result
                pr_info.status = status
                if title:
                    pr_info.title = title
                pr_info.last_checked = datetime.now()
                pr_info.updated_at = datetime.now()

                self._store.set_pr(pr_info)
                self._save_store()

        return pr_info

    def refresh_all_statuses(self) -> list[PRInfo]:
        """
        Refresh status for all linked PRs.

        Returns:
            List of updated PRInfo objects
        """
        updated = []

        for pr_info in self._store.get_all_prs():
            result = self.refresh_pr_status(pr_info.worktree_name)
            if result:
                updated.append(result)

        return updated

    def get_merged_prs(self) -> list[PRInfo]:
        """Get all worktrees with merged PRs."""
        return self._store.get_merged_prs()

    def get_open_prs(self) -> list[PRInfo]:
        """Get all worktrees with open PRs."""
        return self._store.get_open_prs()

    def cleanup_orphans(self, valid_worktree_names: list[str]) -> list[str]:
        """
        Remove PR links for worktrees that no longer exist.

        Args:
            valid_worktree_names: List of currently valid worktree names

        Returns:
            List of removed worktree names
        """
        removed = []
        current_names = [p.worktree_name for p in self._store.get_all_prs()]

        for name in current_names:
            if name not in valid_worktree_names:
                self._store.remove_pr(name)
                removed.append(name)

        if removed:
            self._save_store()

        return removed

    def detect_and_link_pr(
        self,
        worktree_name: str,
        worktree_path: str,
        branch: str,
    ) -> PRLinkResult | None:
        """
        Auto-detect and link a PR from branch name if pattern matches.

        Only links if auto_link_prs is enabled and pattern matches.

        Returns:
            PRLinkResult if linked, None if not attempted
        """
        if not self.config.auto_link_prs:
            return None

        pr_number = self._extract_pr_from_branch(branch)

        if pr_number is None:
            return None

        return self.link_pr(
            worktree_name=worktree_name,
            worktree_path=worktree_path,
            branch=branch,
            pr_number=pr_number,
        )

    def open_pr_in_browser(self, worktree_name: str) -> bool:
        """
        Open the PR in a web browser.

        Returns:
            True if opened successfully
        """
        pr_info = self._store.get_pr(worktree_name)

        if not pr_info:
            return False

        try:
            import webbrowser

            webbrowser.open(pr_info.pr_url)
            return True
        except Exception:
            return False

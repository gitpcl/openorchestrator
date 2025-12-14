"""Git worktree management operations."""

import re
from pathlib import Path
from typing import Optional

from git import Repo
from git.exc import GitCommandError, InvalidGitRepositoryError

from claude_orchestrator.models.worktree_info import WorktreeInfo


class WorktreeError(Exception):
    """Base exception for worktree operations."""


class WorktreeNotFoundError(WorktreeError):
    """Raised when a worktree cannot be found."""


class WorktreeAlreadyExistsError(WorktreeError):
    """Raised when trying to create a worktree that already exists."""


class NotAGitRepositoryError(WorktreeError):
    """Raised when the path is not a git repository."""


class WorktreeManager:
    """Manages git worktree operations for a repository."""

    def __init__(self, repo_path: Optional[Path] = None):
        """
        Initialize the WorktreeManager.

        Args:
            repo_path: Path to the git repository. Defaults to current directory.

        Raises:
            NotAGitRepositoryError: If the path is not a git repository.
        """
        self.repo_path = repo_path or Path.cwd()
        try:
            self.repo = Repo(self.repo_path, search_parent_directories=True)
            self.git_root = Path(self.repo.working_dir)
        except InvalidGitRepositoryError as e:
            raise NotAGitRepositoryError(
                f"Not a git repository: {self.repo_path}"
            ) from e

    @property
    def project_name(self) -> str:
        """Get the project name from the repository root directory."""
        return self.git_root.name

    def _sanitize_branch_name(self, branch: str) -> str:
        """
        Sanitize branch name for use in directory names.

        Args:
            branch: The branch name to sanitize.

        Returns:
            Sanitized string safe for directory names.
        """
        sanitized = branch.replace("/", "-")
        sanitized = re.sub(r"[^\w\-]", "", sanitized)
        return sanitized

    def _generate_worktree_path(self, branch: str) -> Path:
        """
        Generate the path for a new worktree.

        Pattern: {project}-{branch-name} in parent directory.

        Args:
            branch: The branch name for the worktree.

        Returns:
            Path where the worktree should be created.
        """
        sanitized_branch = self._sanitize_branch_name(branch)
        worktree_name = f"{self.project_name}-{sanitized_branch}"
        return self.git_root.parent / worktree_name

    def _find_worktree(self, identifier: str) -> Optional[WorktreeInfo]:
        """
        Find a worktree by name, branch, or path.

        Args:
            identifier: Worktree name, branch name, or path.

        Returns:
            WorktreeInfo if found, None otherwise.
        """
        worktrees = self.list_all()

        for wt in worktrees:
            if wt.name == identifier:
                return wt
            if wt.branch == identifier:
                return wt
            if str(wt.path) == identifier:
                return wt
            if wt.branch.endswith(f"/{identifier}"):
                return wt

        return None

    def list_all(self) -> list[WorktreeInfo]:
        """
        List all worktrees for the repository.

        Returns:
            List of WorktreeInfo objects for each worktree.
        """
        worktrees = []

        try:
            output = self.repo.git.worktree("list", "--porcelain")
        except GitCommandError:
            return worktrees

        current_wt: dict = {}
        for line in output.split("\n"):
            line = line.strip()

            if not line:
                if current_wt:
                    worktrees.append(self._parse_worktree_entry(current_wt))
                    current_wt = {}
                continue

            if line.startswith("worktree "):
                current_wt["path"] = line[9:]
            elif line.startswith("HEAD "):
                current_wt["head"] = line[5:]
            elif line.startswith("branch "):
                current_wt["branch"] = line[7:]
            elif line == "detached":
                current_wt["detached"] = True
            elif line == "bare":
                current_wt["bare"] = True

        if current_wt:
            worktrees.append(self._parse_worktree_entry(current_wt))

        return worktrees

    def _parse_worktree_entry(self, entry: dict) -> WorktreeInfo:
        """
        Parse a worktree entry dictionary into a WorktreeInfo object.

        Args:
            entry: Dictionary with worktree information from git.

        Returns:
            WorktreeInfo object.
        """
        path = Path(entry.get("path", ""))
        branch_ref = entry.get("branch", "")

        if branch_ref.startswith("refs/heads/"):
            branch = branch_ref[11:]
        else:
            branch = branch_ref or "(detached)"

        head_commit = entry.get("head", "")[:7]
        is_main = path == self.git_root
        is_detached = entry.get("detached", False)

        return WorktreeInfo(
            path=path,
            branch=branch,
            head_commit=head_commit,
            is_main=is_main,
            is_detached=is_detached,
        )

    def create(
        self,
        branch: str,
        base_branch: Optional[str] = None,
        path: Optional[Path] = None,
        force: bool = False,
    ) -> WorktreeInfo:
        """
        Create a new worktree for the given branch.

        Args:
            branch: Name of the branch to check out or create.
            base_branch: Base branch for creating new branches. Defaults to current branch.
            path: Custom path for the worktree. Defaults to auto-generated path.
            force: Force creation even if branch already exists elsewhere.

        Returns:
            WorktreeInfo for the created worktree.

        Raises:
            WorktreeAlreadyExistsError: If a worktree already exists at the target path.
            WorktreeError: If worktree creation fails.
        """
        worktree_path = path or self._generate_worktree_path(branch)

        if worktree_path.exists():
            raise WorktreeAlreadyExistsError(
                f"Directory already exists: {worktree_path}"
            )

        existing = self._find_worktree(branch)
        if existing and not force:
            raise WorktreeAlreadyExistsError(
                f"Worktree for branch '{branch}' already exists at: {existing.path}"
            )

        branch_exists = self._branch_exists(branch)

        try:
            if branch_exists:
                self.repo.git.worktree("add", str(worktree_path), branch)
            else:
                base = base_branch or self.repo.active_branch.name
                self.repo.git.worktree("add", "-b", branch, str(worktree_path), base)

        except GitCommandError as e:
            raise WorktreeError(f"Failed to create worktree: {e.stderr}") from e

        worktree = self._find_worktree(branch)
        if not worktree:
            raise WorktreeError(
                f"Worktree created but not found. Path: {worktree_path}"
            )

        return worktree

    def _branch_exists(self, branch: str) -> bool:
        """
        Check if a branch exists in the repository.

        Args:
            branch: Name of the branch to check.

        Returns:
            True if branch exists, False otherwise.
        """
        try:
            self.repo.git.rev_parse("--verify", f"refs/heads/{branch}")
            return True
        except GitCommandError:
            return False

    def delete(self, identifier: str, force: bool = False) -> Path:
        """
        Delete a worktree.

        Args:
            identifier: Worktree name, branch, or path to delete.
            force: Force deletion even with uncommitted changes.

        Returns:
            Path of the deleted worktree.

        Raises:
            WorktreeNotFoundError: If the worktree cannot be found.
            WorktreeError: If the worktree cannot be deleted.
        """
        worktree = self._find_worktree(identifier)

        if not worktree:
            raise WorktreeNotFoundError(f"Worktree not found: {identifier}")

        if worktree.is_main:
            raise WorktreeError("Cannot delete the main worktree")

        try:
            args = ["remove"]
            if force:
                args.append("--force")
            args.append(str(worktree.path))

            self.repo.git.worktree(*args)

        except GitCommandError as e:
            raise WorktreeError(f"Failed to delete worktree: {e.stderr}") from e

        return worktree.path

    def get(self, identifier: str) -> WorktreeInfo:
        """
        Get information about a specific worktree.

        Args:
            identifier: Worktree name, branch, or path.

        Returns:
            WorktreeInfo for the worktree.

        Raises:
            WorktreeNotFoundError: If the worktree cannot be found.
        """
        worktree = self._find_worktree(identifier)
        if not worktree:
            raise WorktreeNotFoundError(f"Worktree not found: {identifier}")
        return worktree

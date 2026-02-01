"""Skill installer for Claude Code skill management."""

import filecmp
import logging
import shutil
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)


class SkillInstallError(Exception):
    """Error during skill installation."""


class SkillNotFoundError(Exception):
    """Skill not found in package."""


class SkillInstaller:
    """Manages Claude Code skill installation.

    Handles installing, uninstalling, and checking the status of the
    Open Orchestrator skill for Claude Code.
    """

    SKILL_NAME = "open-orchestrator"
    SKILL_FILE = "SKILL.md"

    def __init__(self) -> None:
        """Initialize the skill installer."""
        self.target_dir = Path.home() / ".claude" / "skills" / self.SKILL_NAME
        self.target_file = self.target_dir / self.SKILL_FILE

    def _get_source_path(self) -> Path:
        """Get path to skill file in package.

        Returns:
            Path to the source SKILL.md file.

        Raises:
            SkillNotFoundError: If skill file not found in package.
        """
        try:
            skill_module = resources.files("open_orchestrator") / "skills" / self.SKILL_NAME / self.SKILL_FILE
            source_path = Path(str(skill_module))

            if not source_path.exists():
                raise SkillNotFoundError(f"Skill file not found in package: {source_path}")

            return source_path
        except (TypeError, AttributeError):
            # Fallback for older Python versions or edge cases
            import open_orchestrator

            package_dir = Path(open_orchestrator.__file__).parent
            source_path = package_dir / "skills" / self.SKILL_NAME / self.SKILL_FILE

            if not source_path.exists():
                raise SkillNotFoundError(f"Skill file not found in package: {source_path}")

            return source_path

    def install(self, symlink: bool = True, force: bool = False) -> Path:
        """Install skill to ~/.claude/skills/.

        Args:
            symlink: If True, create symlink. If False, copy file.
            force: If True, overwrite existing installation.

        Returns:
            Path to installed skill file.

        Raises:
            SkillInstallError: If installation fails.
        """
        source_path = self._get_source_path()

        if self.target_file.exists() and not force:
            if self.target_file.is_symlink():
                raise SkillInstallError(
                    "Skill already installed (symlink). Use --force to overwrite."
                )
            raise SkillInstallError(
                f"Skill already installed at {self.target_file}. Use --force to overwrite."
            )

        try:
            self.target_dir.mkdir(parents=True, exist_ok=True)

            if self.target_file.exists() or self.target_file.is_symlink():
                self.target_file.unlink()

            if symlink:
                self.target_file.symlink_to(source_path)
                logger.info(f"Created symlink: {self.target_file} -> {source_path}")
            else:
                shutil.copy2(source_path, self.target_file)
                logger.info(f"Copied skill to: {self.target_file}")

            return self.target_file

        except OSError as e:
            raise SkillInstallError(f"Failed to install skill: {e}") from e

    def uninstall(self) -> bool:
        """Remove skill from ~/.claude/skills/.

        Returns:
            True if uninstalled, False if skill was not installed.

        Raises:
            SkillInstallError: If uninstall fails.
        """
        if not self.is_installed():
            return False

        try:
            self.target_file.unlink()

            if self.target_dir.exists() and not any(self.target_dir.iterdir()):
                self.target_dir.rmdir()
                logger.info(f"Removed empty directory: {self.target_dir}")

            logger.info(f"Uninstalled skill from: {self.target_file}")
            return True

        except OSError as e:
            raise SkillInstallError(f"Failed to uninstall skill: {e}") from e

    def is_installed(self) -> bool:
        """Check if skill is installed.

        Returns:
            True if skill file exists at target location.
        """
        return self.target_file.exists() or self.target_file.is_symlink()

    def is_symlink(self) -> bool:
        """Check if installed skill is a symlink.

        Returns:
            True if skill is installed as symlink.
        """
        return self.target_file.is_symlink()

    def is_up_to_date(self) -> bool:
        """Check if installed skill matches source.

        Returns:
            True if installed skill matches source file.
            Always True for symlinks pointing to correct source.
        """
        if not self.is_installed():
            return False

        source_path = self._get_source_path()

        if self.target_file.is_symlink():
            resolved = self.target_file.resolve()
            return resolved == source_path.resolve()

        return filecmp.cmp(str(source_path), str(self.target_file), shallow=False)

    def get_source_path(self) -> Path:
        """Get path to source skill file.

        Returns:
            Path to source SKILL.md in package.
        """
        return self._get_source_path()

    def get_target_path(self) -> Path:
        """Get path to target skill file.

        Returns:
            Path to target SKILL.md in ~/.claude/skills/.
        """
        return self.target_file

    def get_symlink_target(self) -> Path | None:
        """Get the target of the symlink if installed as symlink.

        Returns:
            Path the symlink points to, or None if not a symlink.
        """
        if not self.is_symlink():
            return None
        return self.target_file.resolve()

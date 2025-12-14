"""Environment setup for Open Orchestrator worktrees.

This module handles dependency installation and environment file configuration
for newly created worktrees.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from ..models.project_config import PackageManager, ProjectConfig

logger = logging.getLogger(__name__)


class EnvironmentSetupError(Exception):
    """Raised when environment setup fails."""

    pass


class DependencyInstallError(EnvironmentSetupError):
    """Raised when dependency installation fails."""

    pass


class EnvFileError(EnvironmentSetupError):
    """Raised when .env file operations fail."""

    pass


class EnvironmentSetup:
    """Manages environment setup for worktrees.

    This class handles the installation of dependencies and configuration
    of environment files for new worktrees, ensuring they have all the
    necessary dependencies and configuration to run.

    Example:
        >>> from open_orchestrator.core import ProjectDetector, EnvironmentSetup
        >>> detector = ProjectDetector()
        >>> config = detector.detect("/path/to/source")
        >>> setup = EnvironmentSetup(config)
        >>> setup.setup_worktree("/path/to/worktree", "/path/to/source")
    """

    # Path patterns to adjust in .env files
    ENV_PATH_PATTERNS = [
        r"(DATABASE_URL\s*=\s*)([^\n]+)",
        r"(SQLITE_PATH\s*=\s*)([^\n]+)",
        r"(LOG_PATH\s*=\s*)([^\n]+)",
        r"(CACHE_DIR\s*=\s*)([^\n]+)",
        r"(STORAGE_PATH\s*=\s*)([^\n]+)",
        r"(UPLOAD_DIR\s*=\s*)([^\n]+)",
    ]

    def __init__(self, project_config: ProjectConfig) -> None:
        """Initialize environment setup with project configuration.

        Args:
            project_config: Configuration from ProjectDetector.
        """
        self.config = project_config
        self._install_commands = self._build_install_commands()

    def _build_install_commands(self) -> dict[PackageManager, list[str]]:
        """Build the install command mappings.

        Returns:
            Dictionary mapping package managers to their install commands.
        """
        return {
            # Python package managers
            PackageManager.UV: ["uv", "sync"],
            PackageManager.PIP: ["pip", "install", "-r", "requirements.txt"],
            PackageManager.POETRY: ["poetry", "install"],
            PackageManager.PIPENV: ["pipenv", "install"],
            # Node.js package managers
            PackageManager.NPM: ["npm", "install"],
            PackageManager.YARN: ["yarn", "install"],
            PackageManager.PNPM: ["pnpm", "install"],
            PackageManager.BUN: ["bun", "install"],
            # Other package managers
            PackageManager.COMPOSER: ["composer", "install"],
            PackageManager.CARGO: ["cargo", "build"],
            PackageManager.GO: ["go", "mod", "download"],
        }

    def setup_worktree(
        self,
        worktree_path: str | Path,
        source_path: Optional[str | Path] = None,
        install_deps: bool = True,
        copy_env: bool = True,
    ) -> None:
        """Set up a complete environment for a worktree.

        This method performs all necessary setup steps for a new worktree:
        1. Copy .env file from source (if exists and copy_env is True)
        2. Install dependencies (if install_deps is True)

        Args:
            worktree_path: Path to the new worktree directory.
            source_path: Path to the source project (for copying .env).
                        Defaults to project_config.project_root.
            install_deps: Whether to install dependencies.
            copy_env: Whether to copy and adjust .env file.

        Raises:
            EnvironmentSetupError: If setup fails.
        """
        worktree_path = Path(worktree_path).resolve()
        source_path = Path(source_path or self.config.project_root).resolve()

        logger.info(f"Setting up environment for worktree: {worktree_path}")

        if copy_env:
            try:
                self.setup_env_file(worktree_path, source_path)
            except EnvFileError as e:
                logger.warning(f"Could not set up .env file: {e}")
                # Continue with setup, .env is optional

        if install_deps:
            self.install_dependencies(worktree_path)

        logger.info(f"Environment setup complete for: {worktree_path}")

    def install_dependencies(
        self,
        worktree_path: str | Path,
        timeout: int = 300,
    ) -> subprocess.CompletedProcess:
        """Install project dependencies in the worktree.

        Args:
            worktree_path: Path to the worktree directory.
            timeout: Maximum time in seconds to wait for installation.

        Returns:
            CompletedProcess with result of the install command.

        Raises:
            DependencyInstallError: If installation fails.
        """
        worktree_path = Path(worktree_path).resolve()

        if not worktree_path.is_dir():
            raise DependencyInstallError(
                f"Worktree path does not exist: {worktree_path}"
            )

        install_cmd = self._install_commands.get(self.config.package_manager)

        if not install_cmd:
            logger.warning(
                f"No install command for package manager: {self.config.package_manager}"
            )
            raise DependencyInstallError(
                f"Unknown package manager: {self.config.package_manager}"
            )

        # Check if the command is available
        executable = install_cmd[0]
        if not self._command_exists(executable):
            raise DependencyInstallError(
                f"Command not found: {executable}. "
                f"Please ensure {self.config.package_manager.value} is installed."
            )

        logger.info(
            f"Installing dependencies with {self.config.package_manager.value}: "
            f"{' '.join(install_cmd)}"
        )

        try:
            result = subprocess.run(
                install_cmd,
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._get_install_environment(),
            )

            if result.returncode != 0:
                logger.error(f"Dependency installation failed: {result.stderr}")
                raise DependencyInstallError(
                    f"Installation failed with exit code {result.returncode}: "
                    f"{result.stderr}"
                )

            logger.info("Dependencies installed successfully")
            return result

        except subprocess.TimeoutExpired as e:
            raise DependencyInstallError(
                f"Installation timed out after {timeout} seconds"
            ) from e
        except OSError as e:
            raise DependencyInstallError(
                f"Failed to run install command: {e}"
            ) from e

    def setup_env_file(
        self,
        worktree_path: str | Path,
        source_path: Optional[str | Path] = None,
        adjust_paths: bool = True,
    ) -> Optional[Path]:
        """Copy and optionally adjust .env file for the worktree.

        This method copies the .env file from the source project to the
        worktree, optionally adjusting paths to be relative to the new
        worktree location.

        Args:
            worktree_path: Path to the worktree directory.
            source_path: Path to the source project. Defaults to config.project_root.
            adjust_paths: Whether to adjust path variables in the .env file.

        Returns:
            Path to the new .env file, or None if source .env doesn't exist.

        Raises:
            EnvFileError: If copying or adjusting the .env file fails.
        """
        worktree_path = Path(worktree_path).resolve()
        source_path = Path(source_path or self.config.project_root).resolve()

        source_env = source_path / ".env"
        target_env = worktree_path / ".env"

        if not source_env.exists():
            logger.debug(f"No .env file found at: {source_env}")
            return None

        logger.info(f"Copying .env file from {source_env} to {target_env}")

        try:
            if adjust_paths:
                content = source_env.read_text()
                adjusted_content = self._adjust_env_paths(
                    content, source_path, worktree_path
                )
                target_env.write_text(adjusted_content)
            else:
                shutil.copy2(source_env, target_env)

            logger.info(f".env file set up at: {target_env}")
            return target_env

        except OSError as e:
            raise EnvFileError(f"Failed to copy .env file: {e}") from e

    def _adjust_env_paths(
        self,
        content: str,
        source_path: Path,
        worktree_path: Path,
    ) -> str:
        """Adjust path variables in .env content for the new worktree.

        This method replaces absolute paths that reference the source
        project directory with corresponding paths in the worktree.

        Args:
            content: Original .env file content.
            source_path: Original source project path.
            worktree_path: New worktree path.

        Returns:
            Adjusted .env content.
        """
        source_str = str(source_path)
        worktree_str = str(worktree_path)

        # Simple path replacement for absolute paths
        adjusted = content.replace(source_str, worktree_str)

        # Log if any paths were adjusted
        if adjusted != content:
            logger.debug(
                f"Adjusted paths in .env: {source_str} -> {worktree_str}"
            )

        return adjusted

    def copy_additional_config_files(
        self,
        worktree_path: str | Path,
        source_path: Optional[str | Path] = None,
        files: Optional[list[str]] = None,
    ) -> list[Path]:
        """Copy additional configuration files to the worktree.

        This method copies configuration files that might not be tracked
        in git but are needed for the project to run (e.g., local config
        overrides, secrets files).

        Args:
            worktree_path: Path to the worktree directory.
            source_path: Path to the source project.
            files: List of file names to copy. Defaults to common config files.

        Returns:
            List of paths to copied files.
        """
        worktree_path = Path(worktree_path).resolve()
        source_path = Path(source_path or self.config.project_root).resolve()

        if files is None:
            files = [
                ".env.local",
                ".env.development",
                ".env.development.local",
                "config.local.yaml",
                "config.local.json",
                "settings.local.py",
                ".secrets",
                ".secrets.yaml",
            ]

        copied_files = []

        for filename in files:
            source_file = source_path / filename
            target_file = worktree_path / filename

            if source_file.exists() and not target_file.exists():
                try:
                    shutil.copy2(source_file, target_file)
                    copied_files.append(target_file)
                    logger.debug(f"Copied config file: {filename}")
                except OSError as e:
                    logger.warning(f"Could not copy {filename}: {e}")

        if copied_files:
            logger.info(f"Copied {len(copied_files)} additional config files")

        return copied_files

    def _command_exists(self, command: str) -> bool:
        """Check if a command exists in the system PATH.

        Args:
            command: Command name to check.

        Returns:
            True if command exists, False otherwise.
        """
        return shutil.which(command) is not None

    def _get_install_environment(self) -> dict[str, str]:
        """Get environment variables for the install process.

        Returns:
            Dictionary of environment variables.
        """
        env = os.environ.copy()

        # Ensure we're not in a virtual environment that might conflict
        env.pop("VIRTUAL_ENV", None)

        # Add common CI/automation flags
        env["CI"] = "false"  # Ensure interactive mode where appropriate

        return env

    def verify_installation(
        self,
        worktree_path: str | Path,
    ) -> bool:
        """Verify that dependencies were installed correctly.

        This method performs basic verification that the installation
        completed successfully by checking for expected files/directories.

        Args:
            worktree_path: Path to the worktree directory.

        Returns:
            True if installation appears successful, False otherwise.
        """
        worktree_path = Path(worktree_path).resolve()

        verification_markers = {
            PackageManager.UV: [".venv", "__pypackages__"],
            PackageManager.PIP: [".venv", "venv", "site-packages"],
            PackageManager.POETRY: [".venv", "poetry.lock"],
            PackageManager.PIPENV: [".venv", "Pipfile.lock"],
            PackageManager.NPM: ["node_modules"],
            PackageManager.YARN: ["node_modules", ".yarn"],
            PackageManager.PNPM: ["node_modules", ".pnpm-store"],
            PackageManager.BUN: ["node_modules"],
            PackageManager.COMPOSER: ["vendor"],
            PackageManager.CARGO: ["target"],
            PackageManager.GO: ["go.sum"],
        }

        markers = verification_markers.get(self.config.package_manager, [])

        for marker in markers:
            marker_path = worktree_path / marker
            if marker_path.exists():
                logger.debug(f"Found installation marker: {marker}")
                return True

        logger.warning(
            f"Could not verify installation for {self.config.package_manager.value}"
        )
        return False


__all__ = [
    "EnvironmentSetup",
    "EnvironmentSetupError",
    "DependencyInstallError",
    "EnvFileError",
]

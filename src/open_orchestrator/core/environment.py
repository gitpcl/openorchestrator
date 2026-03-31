"""Environment setup for Open Orchestrator worktrees.

This module handles dependency installation and environment file configuration
for newly created worktrees.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from subprocess import CompletedProcess

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
        source_path: str | Path | None = None,
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

        logger.info("Setting up environment for worktree: %s", worktree_path)

        if copy_env:
            try:
                self.setup_env_file(worktree_path, source_path)
            except EnvFileError as e:
                logger.warning("Could not set up .env file: %s", e)
                # Continue with setup, .env is optional

        if install_deps:
            self.install_dependencies(worktree_path)

        logger.info("Environment setup complete for: %s", worktree_path)

    def install_dependencies(
        self,
        worktree_path: str | Path,
        timeout: int = 300,
    ) -> CompletedProcess[str]:
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
            raise DependencyInstallError(f"Worktree path does not exist: {worktree_path}")

        install_cmd = self._install_commands.get(self.config.package_manager)

        if not install_cmd:
            logger.warning("No install command for package manager: %s", self.config.package_manager)
            raise DependencyInstallError(f"Unknown package manager: {self.config.package_manager}")

        # Check if the command is available
        executable = install_cmd[0]
        if not self._command_exists(executable):
            raise DependencyInstallError(
                f"Command not found: {executable}. Please ensure {self.config.package_manager.value} is installed."
            )

        logger.info("Installing dependencies with %s: %s", self.config.package_manager.value, " ".join(install_cmd))

        try:
            # Stream output to temporary file instead of memory to prevent 3GB+ spikes
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output_file:
                result = subprocess.run(
                    install_cmd,
                    cwd=worktree_path,
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout,
                    env=self._get_install_environment(),
                )

                # Only read output if needed (on error)
                output_text = ""
                if result.returncode != 0:
                    output_file.seek(0)
                    output_text = output_file.read()
                    logger.error("Dependency installation failed: %s", output_text)
                    raise DependencyInstallError(f"Installation failed with exit code {result.returncode}: {output_text}")

            logger.info("Dependencies installed successfully")
            # Return a CompletedProcess with empty stdout/stderr since we streamed to file
            return CompletedProcess(
                args=result.args,
                returncode=result.returncode,
                stdout="",
                stderr="",
            )

        except subprocess.TimeoutExpired as e:
            raise DependencyInstallError(f"Installation timed out after {timeout} seconds") from e
        except OSError as e:
            raise DependencyInstallError(f"Failed to run install command: {e}") from e

    def setup_env_file(
        self,
        worktree_path: str | Path,
        source_path: str | Path | None = None,
        adjust_paths: bool = True,
    ) -> Path | None:
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
            logger.debug("No .env file found at: %s", source_env)
            return None

        logger.info("Copying .env file from %s to %s", source_env, target_env)

        try:
            if adjust_paths:
                content = source_env.read_text()
                adjusted_content = self._adjust_env_paths(content, source_path, worktree_path)
                # Write atomically with restrictive permissions to avoid exposing secrets
                import tempfile

                fd, tmp_path = tempfile.mkstemp(dir=worktree_path, prefix=".env.tmp")
                fd_closed = False
                try:
                    os.fchmod(fd, 0o600)
                    os.write(fd, adjusted_content.encode())
                    os.close(fd)
                    fd_closed = True
                    os.replace(tmp_path, target_env)
                except Exception:
                    if not fd_closed:
                        os.close(fd)
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    raise
            else:
                shutil.copy2(source_env, target_env)
                try:
                    os.chmod(target_env, 0o600)
                except PermissionError:
                    logger.warning(
                        f"Could not set restrictive permissions on {target_env}. Manual chmod may be required for security."
                    )

            logger.info(".env file set up at: %s", target_env)
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

        # First, rewrite known path-like keys conservatively
        adjusted = content
        for pat in self.ENV_PATH_PATTERNS:
            adjusted = re.sub(
                pat,
                lambda m: m.group(1) + m.group(2).replace(source_str, worktree_str),
                adjusted,
            )

        # Fallback: simple absolute-path replacement for any remaining references
        if source_str in adjusted:
            adjusted = adjusted.replace(source_str, worktree_str)

        if adjusted != content:
            logger.debug("Adjusted paths in .env: %s -> %s", source_str, worktree_str)

        return adjusted

    def copy_additional_config_files(
        self,
        worktree_path: str | Path,
        source_path: str | Path | None = None,
        files: list[str] | None = None,
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
                    try:
                        os.chmod(target_file, 0o600)
                    except PermissionError:
                        logger.warning("Could not set restrictive permissions on %s", target_file)
                    copied_files.append(target_file)
                    logger.debug("Copied config file: %s", filename)
                except OSError as e:
                    logger.warning("Could not copy %s: %s", filename, e)

        if copied_files:
            logger.info("Copied %s additional config files", len(copied_files))

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
                logger.debug("Found installation marker: %s", marker)
                return True

        logger.warning("Could not verify installation for %s", self.config.package_manager.value)
        return False


def sync_claude_md(
    worktree_path: str | Path,
    source_path: str | Path,
) -> list[Path]:
    """Sync CLAUDE.md files from source repository to worktree.

    Copies CLAUDE.md files from common locations in the source repository
    to the new worktree. This preserves Claude Code context and instructions
    across worktrees.

    Locations checked (in order of priority):
    - .claude/CLAUDE.md (project-level Claude config)
    - CLAUDE.md (root-level Claude config)

    Args:
        worktree_path: Path to the new worktree directory.
        source_path: Path to the source repository (main worktree).

    Returns:
        List of paths to copied CLAUDE.md files.
    """
    worktree_path = Path(worktree_path).resolve()
    source_path = Path(source_path).resolve()

    copied_files: list[Path] = []

    # Locations to check for CLAUDE.md files
    claude_md_locations = [
        ".claude/CLAUDE.md",
        "CLAUDE.md",
    ]

    for location in claude_md_locations:
        source_file = source_path / location
        target_file = worktree_path / location

        if source_file.exists():
            try:
                # Ensure parent directory exists
                target_file.parent.mkdir(parents=True, exist_ok=True)

                # Copy the file
                shutil.copy2(source_file, target_file)
                copied_files.append(target_file)
                logger.info("Copied CLAUDE.md from %s", location)
            except OSError as e:
                logger.warning("Could not copy %s: %s", location, e)

    if copied_files:
        logger.info("Synced %s CLAUDE.md file(s) to worktree", len(copied_files))
    else:
        logger.debug("No CLAUDE.md files found to sync")

    return copied_files


def _sanitize_injection(text: str) -> str:
    """Strip HTML comment markers from externally-sourced content.

    Prevents injected notes/coordination from manipulating CLAUDE.md
    section boundaries via marker injection.
    """
    return text.replace("<!--", "").replace("-->", "")


def _inject_claude_md_section(
    worktree_path: str | Path,
    marker_id: str,
    section_title: str,
    body: str,
) -> None:
    """Inject or replace a marked section in a worktree's CLAUDE.md.

    Uses HTML comment markers to identify the section boundaries,
    allowing idempotent updates.

    Args:
        worktree_path: Path to the worktree directory.
        marker_id: Unique identifier for markers (e.g., "SHARED-NOTES").
        section_title: Markdown heading for the section.
        body: Pre-formatted body content (empty string to remove section).
    """
    body = _sanitize_injection(body)
    worktree_path = Path(worktree_path).resolve()
    claude_md = worktree_path / ".claude" / "CLAUDE.md"

    if not claude_md.exists():
        return

    content = claude_md.read_text()
    marker_start = f"<!-- OWT-{marker_id}-START -->"
    marker_end = f"<!-- OWT-{marker_id}-END -->"

    if body:
        block = f"\n{marker_start}\n## {section_title}\n\n{body}\n{marker_end}\n"
    else:
        block = ""

    if marker_start in content:
        content = re.sub(
            f"\n?{re.escape(marker_start)}.*?{re.escape(marker_end)}\n?",
            block,
            content,
            flags=re.DOTALL,
        )
    elif block:
        content = content.rstrip() + "\n" + block

    claude_md.write_text(content)
    logger.info("Injected section '%s' into %s", section_title, claude_md)


def inject_shared_notes(
    worktree_path: str | Path,
    notes: list[str],
) -> None:
    """Inject shared notes into a worktree's CLAUDE.md."""
    body = "".join(f"- {note}\n" for note in notes) if notes else ""
    _inject_claude_md_section(
        worktree_path,
        "SHARED-NOTES",
        "Shared Notes (OWT)",
        body,
    )


def inject_project_context(
    worktree_path: str | Path,
    project_config: "ProjectConfig",
) -> None:
    """Inject detected project commands into a worktree's CLAUDE.md.

    Gives agents immediate knowledge of how to build and test the project,
    eliminating wasted tokens on discovery (init.sh pattern).
    """
    lines = [f"- Type: {project_config.project_type.value}"]
    lines.append(f"- Package manager: {project_config.package_manager.value}")
    if project_config.test_command:
        lines.append(f"- Test: `{project_config.test_command}`")
    if project_config.dev_command:
        lines.append(f"- Dev: `{project_config.dev_command}`")
    _inject_claude_md_section(
        worktree_path,
        "PROJECT-CONTEXT",
        "Project Commands (OWT)",
        "\n".join(lines),
    )


def inject_dag_context(
    worktree_path: str | Path,
    parent_summaries: list[str],
) -> None:
    """Inject parent task context into a worktree's CLAUDE.md."""
    if parent_summaries:
        body = "These tasks completed before yours. Use their output:\n\n"
        body += "\n".join(f"{s}\n" for s in parent_summaries)
    else:
        body = ""
    _inject_claude_md_section(
        worktree_path,
        "DAG-CONTEXT",
        "Parent Tasks (OWT DAG)",
        body,
    )


def inject_coordination_context(
    worktree_path: str | Path,
    messages: list[str],
) -> None:
    """Inject coordinator alerts into a worktree's CLAUDE.md."""
    body = "\n".join(f"- {msg}" for msg in messages) if messages else ""
    _inject_claude_md_section(
        worktree_path,
        "COORDINATION",
        "Coordinator Alerts (OWT)",
        body,
    )


__all__ = [
    "EnvironmentSetup",
    "EnvironmentSetupError",
    "DependencyInstallError",
    "EnvFileError",
    "sync_claude_md",
    "inject_shared_notes",
    "inject_dag_context",
    "inject_coordination_context",
]

"""Project type detection for Claude Orchestrator.

This module provides functionality to detect project types and their
associated package managers by analyzing project files and directory structure.
"""

import logging
from pathlib import Path
from typing import Optional

from ..models.project_config import PackageManager, ProjectConfig, ProjectType

logger = logging.getLogger(__name__)


class ProjectDetector:
    """Detects project type and package manager from directory structure.

    This class analyzes a directory to determine the project type (Python, Node,
    PHP, Rust, Go) and the appropriate package manager to use for dependency
    installation.

    Example:
        >>> detector = ProjectDetector()
        >>> config = detector.detect("/path/to/project")
        >>> print(config.project_type, config.package_manager)
        python uv
    """

    # Python detection files in order of preference
    PYTHON_MARKERS = {
        "pyproject.toml": None,  # Could be uv, poetry, or pip
        "uv.lock": PackageManager.UV,
        "poetry.lock": PackageManager.POETRY,
        "Pipfile": PackageManager.PIPENV,
        "Pipfile.lock": PackageManager.PIPENV,
        "requirements.txt": PackageManager.PIP,
        "setup.py": PackageManager.PIP,
        "setup.cfg": PackageManager.PIP,
    }

    # Node.js detection files in order of preference
    NODE_MARKERS = {
        "package.json": None,  # Could be npm, yarn, pnpm, or bun
        "bun.lockb": PackageManager.BUN,
        "pnpm-lock.yaml": PackageManager.PNPM,
        "yarn.lock": PackageManager.YARN,
        "package-lock.json": PackageManager.NPM,
    }

    # PHP detection files
    PHP_MARKERS = {
        "composer.json": PackageManager.COMPOSER,
        "composer.lock": PackageManager.COMPOSER,
    }

    # Rust detection files
    RUST_MARKERS = {
        "Cargo.toml": PackageManager.CARGO,
        "Cargo.lock": PackageManager.CARGO,
    }

    # Go detection files
    GO_MARKERS = {
        "go.mod": PackageManager.GO,
        "go.sum": PackageManager.GO,
    }

    def __init__(self) -> None:
        """Initialize the project detector."""
        self._detection_order = [
            (ProjectType.PYTHON, self.PYTHON_MARKERS, self._detect_python_manager),
            (ProjectType.NODE, self.NODE_MARKERS, self._detect_node_manager),
            (ProjectType.RUST, self.RUST_MARKERS, None),
            (ProjectType.GO, self.GO_MARKERS, None),
            (ProjectType.PHP, self.PHP_MARKERS, None),
        ]

    def detect(self, project_path: str | Path) -> ProjectConfig:
        """Detect project type and configuration from a directory.

        Args:
            project_path: Path to the project root directory.

        Returns:
            ProjectConfig with detected settings.

        Raises:
            ValueError: If the path doesn't exist or isn't a directory.
        """
        project_root = Path(project_path).resolve()

        if not project_root.exists():
            raise ValueError(f"Project path does not exist: {project_root}")

        if not project_root.is_dir():
            raise ValueError(f"Project path is not a directory: {project_root}")

        logger.info(f"Detecting project type in: {project_root}")

        # Try each project type in order
        for project_type, markers, manager_detector in self._detection_order:
            detected_marker = self._find_marker(project_root, markers)

            if detected_marker:
                marker_file, default_manager = detected_marker
                logger.debug(f"Found marker file: {marker_file}")

                # Use custom manager detector if available
                if manager_detector:
                    package_manager = manager_detector(project_root, markers)
                else:
                    package_manager = default_manager or PackageManager.UNKNOWN

                return self._build_config(
                    project_root=project_root,
                    project_type=project_type,
                    package_manager=package_manager,
                    marker_file=marker_file,
                    markers=markers,
                )

        # No project type detected
        logger.warning(f"Could not detect project type in: {project_root}")
        return ProjectConfig(
            project_root=project_root,
            project_type=ProjectType.UNKNOWN,
            package_manager=PackageManager.UNKNOWN,
        )

    def _find_marker(
        self,
        project_root: Path,
        markers: dict[str, Optional[PackageManager]],
    ) -> Optional[tuple[str, Optional[PackageManager]]]:
        """Find the first matching marker file in the project.

        Args:
            project_root: Project root directory.
            markers: Dictionary of marker files to package managers.

        Returns:
            Tuple of (marker_file, package_manager) if found, None otherwise.
        """
        for marker_file, package_manager in markers.items():
            if (project_root / marker_file).exists():
                return (marker_file, package_manager)
        return None

    def _detect_python_manager(
        self,
        project_root: Path,
        markers: dict[str, Optional[PackageManager]],
    ) -> PackageManager:
        """Detect the Python package manager from project files.

        Priority order: uv > poetry > pipenv > pip

        Args:
            project_root: Project root directory.
            markers: Dictionary of marker files to package managers.

        Returns:
            Detected PackageManager.
        """
        # Check for lock files first (more specific)
        if (project_root / "uv.lock").exists():
            return PackageManager.UV

        if (project_root / "poetry.lock").exists():
            return PackageManager.POETRY

        if (project_root / "Pipfile.lock").exists() or (project_root / "Pipfile").exists():
            return PackageManager.PIPENV

        # Check pyproject.toml for build system hints
        pyproject_path = project_root / "pyproject.toml"
        if pyproject_path.exists():
            try:
                content = pyproject_path.read_text()

                # Check for uv-specific markers
                if "[tool.uv]" in content or "uv" in content.lower():
                    return PackageManager.UV

                # Check for poetry
                if "[tool.poetry]" in content:
                    return PackageManager.POETRY

                # Default to uv for modern pyproject.toml projects
                return PackageManager.UV

            except OSError as e:
                logger.warning(f"Could not read pyproject.toml: {e}")

        # Fallback to pip if requirements.txt exists
        if (project_root / "requirements.txt").exists():
            return PackageManager.PIP

        # Default to uv as modern Python standard
        return PackageManager.UV

    def _detect_node_manager(
        self,
        project_root: Path,
        markers: dict[str, Optional[PackageManager]],
    ) -> PackageManager:
        """Detect the Node.js package manager from project files.

        Priority order: bun > pnpm > yarn > npm

        Args:
            project_root: Project root directory.
            markers: Dictionary of marker files to package managers.

        Returns:
            Detected PackageManager.
        """
        # Check for lock files (most specific)
        if (project_root / "bun.lockb").exists():
            return PackageManager.BUN

        if (project_root / "pnpm-lock.yaml").exists():
            return PackageManager.PNPM

        if (project_root / "yarn.lock").exists():
            return PackageManager.YARN

        if (project_root / "package-lock.json").exists():
            return PackageManager.NPM

        # Check package.json for packageManager field
        package_json_path = project_root / "package.json"
        if package_json_path.exists():
            try:
                import json

                content = json.loads(package_json_path.read_text())
                package_manager = content.get("packageManager", "")

                if package_manager.startswith("bun"):
                    return PackageManager.BUN

                if package_manager.startswith("pnpm"):
                    return PackageManager.PNPM

                if package_manager.startswith("yarn"):
                    return PackageManager.YARN

            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Could not read package.json: {e}")

        # Default to npm
        return PackageManager.NPM

    def _build_config(
        self,
        project_root: Path,
        project_type: ProjectType,
        package_manager: PackageManager,
        marker_file: str,
        markers: dict[str, Optional[PackageManager]],
    ) -> ProjectConfig:
        """Build a ProjectConfig from detected information.

        Args:
            project_root: Project root directory.
            project_type: Detected project type.
            package_manager: Detected package manager.
            marker_file: The marker file that triggered detection.
            markers: All markers for this project type.

        Returns:
            Configured ProjectConfig instance.
        """
        # Determine manifest file
        manifest_files = {
            ProjectType.PYTHON: ["pyproject.toml", "setup.py", "requirements.txt"],
            ProjectType.NODE: ["package.json"],
            ProjectType.PHP: ["composer.json"],
            ProjectType.RUST: ["Cargo.toml"],
            ProjectType.GO: ["go.mod"],
        }

        manifest_path = None
        for manifest in manifest_files.get(project_type, []):
            candidate = project_root / manifest
            if candidate.exists():
                manifest_path = candidate
                break

        # Determine lock file
        lock_files = {
            PackageManager.UV: "uv.lock",
            PackageManager.POETRY: "poetry.lock",
            PackageManager.PIPENV: "Pipfile.lock",
            PackageManager.NPM: "package-lock.json",
            PackageManager.YARN: "yarn.lock",
            PackageManager.PNPM: "pnpm-lock.yaml",
            PackageManager.BUN: "bun.lockb",
            PackageManager.COMPOSER: "composer.lock",
            PackageManager.CARGO: "Cargo.lock",
            PackageManager.GO: "go.sum",
        }

        lock_file = lock_files.get(package_manager)
        lock_file_path = None
        has_lock_file = False

        if lock_file:
            candidate = project_root / lock_file
            if candidate.exists():
                lock_file_path = candidate
                has_lock_file = True

        # Check for .env file
        env_file_path = None
        env_candidate = project_root / ".env"
        if env_candidate.exists():
            env_file_path = env_candidate

        config = ProjectConfig(
            project_root=project_root,
            project_type=project_type,
            package_manager=package_manager,
            has_lock_file=has_lock_file,
            lock_file_path=lock_file_path,
            manifest_file_path=manifest_path,
            env_file_path=env_file_path,
        )

        logger.info(
            f"Detected {project_type.value} project with {package_manager.value} "
            f"package manager"
        )

        return config

    def detect_all(self, project_path: str | Path) -> list[ProjectConfig]:
        """Detect all project types in a directory (for monorepos).

        Some directories may contain multiple project types (e.g., a Python
        backend and Node.js frontend). This method returns all detected types.

        Args:
            project_path: Path to the project root directory.

        Returns:
            List of ProjectConfig for each detected project type.
        """
        project_root = Path(project_path).resolve()
        configs = []

        for project_type, markers, manager_detector in self._detection_order:
            detected_marker = self._find_marker(project_root, markers)

            if detected_marker:
                marker_file, default_manager = detected_marker

                if manager_detector:
                    package_manager = manager_detector(project_root, markers)
                else:
                    package_manager = default_manager or PackageManager.UNKNOWN

                config = self._build_config(
                    project_root=project_root,
                    project_type=project_type,
                    package_manager=package_manager,
                    marker_file=marker_file,
                    markers=markers,
                )
                configs.append(config)

        return configs


# Re-export ProjectType for convenience
__all__ = ["ProjectDetector", "ProjectType"]

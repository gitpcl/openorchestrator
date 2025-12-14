"""Tests for project type detection."""

from pathlib import Path

import pytest

from claude_orchestrator.core.project_detector import ProjectDetector
from claude_orchestrator.models.project_config import PackageManager, ProjectType


def resolve_path(path: Path) -> Path:
    """Resolve symlinks for consistent path comparison (macOS /var -> /private/var)."""
    return path.resolve()


class TestProjectDetector:
    """Test suite for ProjectDetector class."""

    @pytest.fixture
    def detector(self) -> ProjectDetector:
        """Create a ProjectDetector instance."""
        return ProjectDetector()

    def test_detect_python_uv_project(self, detector: ProjectDetector, python_project_dir: Path):
        """Test detection of Python project with uv."""
        config = detector.detect(python_project_dir)
        expected_root = resolve_path(python_project_dir)

        assert config.project_type == ProjectType.PYTHON
        assert config.package_manager == PackageManager.UV
        assert config.project_root == expected_root
        assert config.has_lock_file is True
        assert config.lock_file_path == expected_root / "uv.lock"
        assert config.manifest_file_path == expected_root / "pyproject.toml"

    def test_detect_python_poetry_project(
        self, detector: ProjectDetector, poetry_project_dir: Path
    ):
        """Test detection of Python project with Poetry."""
        config = detector.detect(poetry_project_dir)
        expected_root = resolve_path(poetry_project_dir)

        assert config.project_type == ProjectType.PYTHON
        assert config.package_manager == PackageManager.POETRY
        assert config.project_root == expected_root
        assert config.has_lock_file is True
        assert config.lock_file_path == expected_root / "poetry.lock"

    def test_detect_python_pip_project(self, detector: ProjectDetector, pip_project_dir: Path):
        """Test detection of Python project with pip/requirements.txt."""
        config = detector.detect(pip_project_dir)
        expected_root = resolve_path(pip_project_dir)

        assert config.project_type == ProjectType.PYTHON
        assert config.package_manager == PackageManager.PIP
        assert config.project_root == expected_root
        assert config.manifest_file_path == expected_root / "requirements.txt"

    def test_detect_node_npm_project(self, detector: ProjectDetector, node_npm_project_dir: Path):
        """Test detection of Node.js project with npm."""
        config = detector.detect(node_npm_project_dir)
        expected_root = resolve_path(node_npm_project_dir)

        assert config.project_type == ProjectType.NODE
        assert config.package_manager == PackageManager.NPM
        assert config.project_root == expected_root
        assert config.has_lock_file is True
        assert config.lock_file_path == expected_root / "package-lock.json"
        assert config.manifest_file_path == expected_root / "package.json"

    def test_detect_node_yarn_project(self, detector: ProjectDetector, node_yarn_project_dir: Path):
        """Test detection of Node.js project with yarn."""
        config = detector.detect(node_yarn_project_dir)
        expected_root = resolve_path(node_yarn_project_dir)

        assert config.project_type == ProjectType.NODE
        assert config.package_manager == PackageManager.YARN
        assert config.project_root == expected_root
        assert config.has_lock_file is True
        assert config.lock_file_path == expected_root / "yarn.lock"

    def test_detect_node_pnpm_project(self, detector: ProjectDetector, node_pnpm_project_dir: Path):
        """Test detection of Node.js project with pnpm."""
        config = detector.detect(node_pnpm_project_dir)
        expected_root = resolve_path(node_pnpm_project_dir)

        assert config.project_type == ProjectType.NODE
        assert config.package_manager == PackageManager.PNPM
        assert config.project_root == expected_root
        assert config.has_lock_file is True
        assert config.lock_file_path == expected_root / "pnpm-lock.yaml"

    def test_detect_node_bun_project(self, detector: ProjectDetector, node_bun_project_dir: Path):
        """Test detection of Node.js project with bun."""
        config = detector.detect(node_bun_project_dir)
        expected_root = resolve_path(node_bun_project_dir)

        assert config.project_type == ProjectType.NODE
        assert config.package_manager == PackageManager.BUN
        assert config.project_root == expected_root
        assert config.has_lock_file is True
        assert config.lock_file_path == expected_root / "bun.lockb"

    def test_detect_rust_project(self, detector: ProjectDetector, rust_project_dir: Path):
        """Test detection of Rust/Cargo project."""
        config = detector.detect(rust_project_dir)
        expected_root = resolve_path(rust_project_dir)

        assert config.project_type == ProjectType.RUST
        assert config.package_manager == PackageManager.CARGO
        assert config.project_root == expected_root
        assert config.has_lock_file is True
        assert config.lock_file_path == expected_root / "Cargo.lock"
        assert config.manifest_file_path == expected_root / "Cargo.toml"

    def test_detect_go_project(self, detector: ProjectDetector, go_project_dir: Path):
        """Test detection of Go project."""
        config = detector.detect(go_project_dir)
        expected_root = resolve_path(go_project_dir)

        assert config.project_type == ProjectType.GO
        assert config.package_manager == PackageManager.GO
        assert config.project_root == expected_root
        assert config.has_lock_file is True
        assert config.lock_file_path == expected_root / "go.sum"
        assert config.manifest_file_path == expected_root / "go.mod"

    def test_detect_php_project(self, detector: ProjectDetector, php_project_dir: Path):
        """Test detection of PHP/Composer project."""
        config = detector.detect(php_project_dir)
        expected_root = resolve_path(php_project_dir)

        assert config.project_type == ProjectType.PHP
        assert config.package_manager == PackageManager.COMPOSER
        assert config.project_root == expected_root
        assert config.has_lock_file is True
        assert config.lock_file_path == expected_root / "composer.lock"
        assert config.manifest_file_path == expected_root / "composer.json"

    def test_detect_unknown_project(self, detector: ProjectDetector, empty_project_dir: Path):
        """Test detection returns unknown for empty directory."""
        config = detector.detect(empty_project_dir)
        expected_root = resolve_path(empty_project_dir)

        assert config.project_type == ProjectType.UNKNOWN
        assert config.package_manager == PackageManager.UNKNOWN
        assert config.project_root == expected_root
        assert config.has_lock_file is False
        assert config.lock_file_path is None

    def test_detect_with_env_file(self, detector: ProjectDetector, project_with_env: Path):
        """Test detection includes .env file when present."""
        config = detector.detect(project_with_env)
        expected_root = resolve_path(project_with_env)

        assert config.env_file_path == expected_root / ".env"

    def test_detect_raises_for_nonexistent_path(self, detector: ProjectDetector):
        """Test detection raises ValueError for non-existent path."""
        with pytest.raises(ValueError, match="does not exist"):
            detector.detect("/path/that/does/not/exist")

    def test_detect_raises_for_file_path(self, detector: ProjectDetector, temp_dir: Path):
        """Test detection raises ValueError when path is a file, not directory."""
        file_path = temp_dir / "somefile.txt"
        file_path.write_text("test content")

        with pytest.raises(ValueError, match="is not a directory"):
            detector.detect(file_path)

    def test_detect_all_monorepo(self, detector: ProjectDetector, monorepo_project_dir: Path):
        """Test detect_all finds multiple project types in monorepo."""
        configs = detector.detect_all(monorepo_project_dir)

        assert len(configs) == 2

        project_types = {config.project_type for config in configs}
        assert ProjectType.PYTHON in project_types
        assert ProjectType.NODE in project_types

    def test_detect_all_single_project(
        self, detector: ProjectDetector, python_project_dir: Path
    ):
        """Test detect_all returns single config for single project type."""
        configs = detector.detect_all(python_project_dir)

        assert len(configs) == 1
        assert configs[0].project_type == ProjectType.PYTHON

    def test_detect_all_empty_project(self, detector: ProjectDetector, empty_project_dir: Path):
        """Test detect_all returns empty list for unknown project."""
        configs = detector.detect_all(empty_project_dir)

        assert len(configs) == 0

    def test_python_detection_priority_uv_over_poetry(self, detector: ProjectDetector, temp_dir: Path):
        """Test that uv.lock takes priority over poetry.lock."""
        project_dir = temp_dir / "mixed-python"
        project_dir.mkdir()

        pyproject = """
[project]
name = "test"

[tool.poetry]
name = "test"
"""
        (project_dir / "pyproject.toml").write_text(pyproject)
        (project_dir / "uv.lock").write_text("# uv lock")
        (project_dir / "poetry.lock").write_text("# poetry lock")

        config = detector.detect(project_dir)

        assert config.package_manager == PackageManager.UV

    def test_node_detection_priority_bun_over_npm(self, detector: ProjectDetector, temp_dir: Path):
        """Test that bun.lockb takes priority over package-lock.json."""
        project_dir = temp_dir / "mixed-node"
        project_dir.mkdir()

        (project_dir / "package.json").write_text('{"name": "test"}')
        (project_dir / "bun.lockb").write_bytes(b"bun lock")
        (project_dir / "package-lock.json").write_text("{}")

        config = detector.detect(project_dir)

        assert config.package_manager == PackageManager.BUN

    def test_detect_accepts_string_path(self, detector: ProjectDetector, python_project_dir: Path):
        """Test detect accepts string path in addition to Path object."""
        config = detector.detect(str(python_project_dir))
        expected_root = resolve_path(python_project_dir)

        assert config.project_type == ProjectType.PYTHON
        assert config.project_root == expected_root


class TestProjectDetectorMarkers:
    """Test suite for ProjectDetector marker files."""

    def test_python_markers_defined(self):
        """Test that Python markers include expected files."""
        markers = ProjectDetector.PYTHON_MARKERS

        assert "pyproject.toml" in markers
        assert "uv.lock" in markers
        assert "poetry.lock" in markers
        assert "requirements.txt" in markers

    def test_node_markers_defined(self):
        """Test that Node.js markers include expected files."""
        markers = ProjectDetector.NODE_MARKERS

        assert "package.json" in markers
        assert "bun.lockb" in markers
        assert "pnpm-lock.yaml" in markers
        assert "yarn.lock" in markers
        assert "package-lock.json" in markers

    def test_rust_markers_defined(self):
        """Test that Rust markers include expected files."""
        markers = ProjectDetector.RUST_MARKERS

        assert "Cargo.toml" in markers
        assert "Cargo.lock" in markers

    def test_go_markers_defined(self):
        """Test that Go markers include expected files."""
        markers = ProjectDetector.GO_MARKERS

        assert "go.mod" in markers
        assert "go.sum" in markers

    def test_php_markers_defined(self):
        """Test that PHP markers include expected files."""
        markers = ProjectDetector.PHP_MARKERS

        assert "composer.json" in markers
        assert "composer.lock" in markers

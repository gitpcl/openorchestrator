"""Tests for Pydantic models."""

from datetime import datetime
from pathlib import Path

import pytest

from open_orchestrator.models.project_config import (
    PackageManager,
    ProjectConfig,
    ProjectType,
)
from open_orchestrator.models.worktree_info import WorktreeCreateResult, WorktreeInfo


class TestPackageManager:
    """Test suite for PackageManager enum."""

    def test_python_package_managers(self):
        """Test Python package manager values."""
        assert PackageManager.UV.value == "uv"
        assert PackageManager.PIP.value == "pip"
        assert PackageManager.POETRY.value == "poetry"
        assert PackageManager.PIPENV.value == "pipenv"

    def test_node_package_managers(self):
        """Test Node.js package manager values."""
        assert PackageManager.NPM.value == "npm"
        assert PackageManager.YARN.value == "yarn"
        assert PackageManager.PNPM.value == "pnpm"
        assert PackageManager.BUN.value == "bun"

    def test_other_package_managers(self):
        """Test other language package manager values."""
        assert PackageManager.COMPOSER.value == "composer"
        assert PackageManager.CARGO.value == "cargo"
        assert PackageManager.GO.value == "go"
        assert PackageManager.UNKNOWN.value == "unknown"

    def test_package_manager_is_string_enum(self):
        """Test that PackageManager values are strings."""
        for pm in PackageManager:
            assert isinstance(pm.value, str)


class TestProjectType:
    """Test suite for ProjectType enum."""

    def test_project_type_values(self):
        """Test all project type values."""
        assert ProjectType.PYTHON.value == "python"
        assert ProjectType.NODE.value == "node"
        assert ProjectType.PHP.value == "php"
        assert ProjectType.RUST.value == "rust"
        assert ProjectType.GO.value == "go"
        assert ProjectType.UNKNOWN.value == "unknown"

    def test_project_type_is_string_enum(self):
        """Test that ProjectType values are strings."""
        for pt in ProjectType:
            assert isinstance(pt.value, str)


class TestProjectConfig:
    """Test suite for ProjectConfig model."""

    @pytest.fixture
    def sample_project_config(self, temp_dir: Path) -> ProjectConfig:
        """Create a sample ProjectConfig."""
        return ProjectConfig(
            project_root=temp_dir,
            project_type=ProjectType.PYTHON,
            package_manager=PackageManager.UV,
            has_lock_file=True,
            lock_file_path=temp_dir / "uv.lock",
            manifest_file_path=temp_dir / "pyproject.toml",
        )

    def test_project_config_creation(self, sample_project_config: ProjectConfig):
        """Test ProjectConfig can be created with valid data."""
        assert sample_project_config.project_type == ProjectType.PYTHON
        assert sample_project_config.package_manager == PackageManager.UV
        assert sample_project_config.has_lock_file is True

    def test_project_config_defaults(self, temp_dir: Path):
        """Test ProjectConfig uses correct defaults."""
        config = ProjectConfig(project_root=temp_dir)

        assert config.project_type == ProjectType.UNKNOWN
        assert config.package_manager == PackageManager.UNKNOWN
        assert config.has_lock_file is False
        assert config.lock_file_path is None
        assert config.manifest_file_path is None
        assert config.env_file_path is None
        assert config.install_command == ""

    def test_get_install_command_uv(self, temp_dir: Path):
        """Test install command for uv."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.UV
        )
        assert config.get_install_command() == "uv sync"

    def test_get_install_command_pip(self, temp_dir: Path):
        """Test install command for pip."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.PIP
        )
        assert config.get_install_command() == "pip install -r requirements.txt"

    def test_get_install_command_poetry(self, temp_dir: Path):
        """Test install command for poetry."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.POETRY
        )
        assert config.get_install_command() == "poetry install"

    def test_get_install_command_pipenv(self, temp_dir: Path):
        """Test install command for pipenv."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.PIPENV
        )
        assert config.get_install_command() == "pipenv install"

    def test_get_install_command_npm(self, temp_dir: Path):
        """Test install command for npm."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.NPM
        )
        assert config.get_install_command() == "npm install"

    def test_get_install_command_yarn(self, temp_dir: Path):
        """Test install command for yarn."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.YARN
        )
        assert config.get_install_command() == "yarn install"

    def test_get_install_command_pnpm(self, temp_dir: Path):
        """Test install command for pnpm."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.PNPM
        )
        assert config.get_install_command() == "pnpm install"

    def test_get_install_command_bun(self, temp_dir: Path):
        """Test install command for bun."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.BUN
        )
        assert config.get_install_command() == "bun install"

    def test_get_install_command_composer(self, temp_dir: Path):
        """Test install command for composer."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.COMPOSER
        )
        assert config.get_install_command() == "composer install"

    def test_get_install_command_cargo(self, temp_dir: Path):
        """Test install command for cargo."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.CARGO
        )
        assert config.get_install_command() == "cargo build"

    def test_get_install_command_go(self, temp_dir: Path):
        """Test install command for go."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.GO
        )
        assert config.get_install_command() == "go mod download"

    def test_get_install_command_unknown(self, temp_dir: Path):
        """Test install command for unknown package manager."""
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.UNKNOWN
        )
        assert config.get_install_command() == ""

    def test_get_install_command_custom_override(self, temp_dir: Path):
        """Test custom install_command takes precedence."""
        custom_command = "pip install -e ."
        config = ProjectConfig(
            project_root=temp_dir,
            package_manager=PackageManager.PIP,
            install_command=custom_command
        )
        assert config.get_install_command() == custom_command

    def test_config_serialization(self, sample_project_config: ProjectConfig):
        """Test ProjectConfig can be serialized to dict."""
        data = sample_project_config.model_dump()

        assert "project_type" in data
        assert "package_manager" in data
        assert "project_root" in data


class TestWorktreeInfo:
    """Test suite for WorktreeInfo model."""

    @pytest.fixture
    def sample_worktree_info(self) -> WorktreeInfo:
        """Create a sample WorktreeInfo."""
        return WorktreeInfo(
            path=Path("/home/user/projects/my-project-feature-login"),
            branch="feature/login",
            head_commit="abc1234",
            is_main=False,
            is_detached=False,
            created_at=datetime.now()
        )

    def test_worktree_info_creation(self, sample_worktree_info: WorktreeInfo):
        """Test WorktreeInfo can be created with valid data."""
        assert sample_worktree_info.branch == "feature/login"
        assert sample_worktree_info.head_commit == "abc1234"
        assert sample_worktree_info.is_main is False
        assert sample_worktree_info.is_detached is False

    def test_worktree_info_defaults(self):
        """Test WorktreeInfo uses correct defaults."""
        info = WorktreeInfo(
            path=Path("/tmp/test"),
            branch="main",
            head_commit="def5678"
        )

        assert info.is_main is False
        assert info.is_detached is False
        assert info.created_at is None

    def test_worktree_name_property(self, sample_worktree_info: WorktreeInfo):
        """Test the name property returns directory name."""
        assert sample_worktree_info.name == "my-project-feature-login"

    def test_worktree_short_path_in_home(self):
        """Test short_path returns ~ for home directory paths."""
        home = Path.home()
        worktree_path = home / "projects" / "test-worktree"

        info = WorktreeInfo(
            path=worktree_path,
            branch="main",
            head_commit="abc1234"
        )

        assert info.short_path == "~/projects/test-worktree"

    def test_worktree_short_path_outside_home(self):
        """Test short_path returns full path for non-home paths."""
        info = WorktreeInfo(
            path=Path("/tmp/test-worktree"),
            branch="main",
            head_commit="abc1234"
        )

        assert info.short_path == "/tmp/test-worktree"

    def test_worktree_info_with_detached_head(self):
        """Test WorktreeInfo with detached HEAD."""
        info = WorktreeInfo(
            path=Path("/tmp/test"),
            branch="HEAD",
            head_commit="abc1234",
            is_detached=True
        )

        assert info.is_detached is True

    def test_worktree_info_main_worktree(self):
        """Test WorktreeInfo for main worktree."""
        info = WorktreeInfo(
            path=Path("/tmp/main-repo"),
            branch="main",
            head_commit="abc1234",
            is_main=True
        )

        assert info.is_main is True


class TestWorktreeCreateResult:
    """Test suite for WorktreeCreateResult model."""

    @pytest.fixture
    def sample_worktree(self) -> WorktreeInfo:
        """Create a sample WorktreeInfo."""
        return WorktreeInfo(
            path=Path("/tmp/test-worktree"),
            branch="feature/test",
            head_commit="abc1234"
        )

    def test_create_result_creation(self, sample_worktree: WorktreeInfo):
        """Test WorktreeCreateResult can be created."""
        result = WorktreeCreateResult(
            worktree=sample_worktree,
            created_branch=True,
            deps_installed=True,
            tmux_session="owt-feature-test"
        )

        assert result.worktree == sample_worktree
        assert result.created_branch is True
        assert result.deps_installed is True
        assert result.tmux_session == "owt-feature-test"

    def test_create_result_defaults(self, sample_worktree: WorktreeInfo):
        """Test WorktreeCreateResult uses correct defaults."""
        result = WorktreeCreateResult(worktree=sample_worktree)

        assert result.created_branch is False
        assert result.deps_installed is False
        assert result.tmux_session is None

    def test_create_result_no_tmux(self, sample_worktree: WorktreeInfo):
        """Test WorktreeCreateResult without tmux session."""
        result = WorktreeCreateResult(
            worktree=sample_worktree,
            created_branch=True,
            deps_installed=False
        )

        assert result.tmux_session is None

    def test_create_result_serialization(self, sample_worktree: WorktreeInfo):
        """Test WorktreeCreateResult can be serialized."""
        result = WorktreeCreateResult(
            worktree=sample_worktree,
            created_branch=True,
            deps_installed=True,
            tmux_session="owt-test"
        )

        data = result.model_dump()

        assert "worktree" in data
        assert "created_branch" in data
        assert "deps_installed" in data
        assert "tmux_session" in data
        assert data["created_branch"] is True

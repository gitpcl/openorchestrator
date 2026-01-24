"""
Tests for EnvironmentSetup class and dependency installation.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from open_orchestrator.core.environment import (
    DependencyInstallError,
    EnvFileError,
    EnvironmentSetup,
    EnvironmentSetupError,
)
from open_orchestrator.models.project_config import PackageManager, ProjectConfig, ProjectType


@pytest.fixture
def python_project_config(python_project_dir: Path) -> ProjectConfig:
    """Create a Python project configuration for testing."""
    return ProjectConfig(
        project_type=ProjectType.PYTHON,
        package_manager=PackageManager.UV,
        project_root=python_project_dir,
    )


@pytest.fixture
def node_project_config(node_npm_project_dir: Path) -> ProjectConfig:
    """Create a Node.js project configuration for testing."""
    return ProjectConfig(
        project_type=ProjectType.NODE,
        package_manager=PackageManager.NPM,
        project_root=node_npm_project_dir,
    )


class TestEnvironmentSetupInit:
    """Test EnvironmentSetup initialization."""

    def test_init_with_project_config(self, python_project_config: ProjectConfig) -> None:
        """Test EnvironmentSetup initialization with project config."""
        # Act
        env_setup = EnvironmentSetup(python_project_config)

        # Assert
        assert env_setup.config == python_project_config
        assert env_setup._install_commands is not None
        assert PackageManager.UV in env_setup._install_commands

    def test_build_install_commands_python(self, python_project_config: ProjectConfig) -> None:
        """Test install commands are built for Python package managers."""
        # Arrange
        env_setup = EnvironmentSetup(python_project_config)

        # Act & Assert
        assert PackageManager.UV in env_setup._install_commands
        assert env_setup._install_commands[PackageManager.UV] == ["uv", "sync"]
        assert env_setup._install_commands[PackageManager.PIP] == ["pip", "install", "-r", "requirements.txt"]
        assert env_setup._install_commands[PackageManager.POETRY] == ["poetry", "install"]

    def test_build_install_commands_node(self, node_project_config: ProjectConfig) -> None:
        """Test install commands are built for Node.js package managers."""
        # Arrange
        env_setup = EnvironmentSetup(node_project_config)

        # Act & Assert
        assert PackageManager.NPM in env_setup._install_commands
        assert env_setup._install_commands[PackageManager.NPM] == ["npm", "install"]
        assert env_setup._install_commands[PackageManager.YARN] == ["yarn", "install"]
        assert env_setup._install_commands[PackageManager.PNPM] == ["pnpm", "install"]
        assert env_setup._install_commands[PackageManager.BUN] == ["bun", "install"]


class TestInstallDependencies:
    """Test dependency installation."""

    @patch("subprocess.run")
    @patch("open_orchestrator.core.environment.EnvironmentSetup._command_exists")
    def test_install_dependencies_success(
        self,
        mock_command_exists: MagicMock,
        mock_run: MagicMock,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test successful dependency installation."""
        # Arrange
        env_setup = EnvironmentSetup(python_project_config)
        mock_command_exists.return_value = True
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Dependencies installed"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        # Act
        result = env_setup.install_dependencies(temp_directory)

        # Assert
        assert result.returncode == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args.kwargs["cwd"] == temp_directory
        assert call_args.args[0] == ["uv", "sync"]

    @patch("subprocess.run")
    @patch("open_orchestrator.core.environment.EnvironmentSetup._command_exists")
    def test_install_dependencies_command_failure(
        self,
        mock_command_exists: MagicMock,
        mock_run: MagicMock,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test dependency installation failure due to command error."""
        # Arrange
        env_setup = EnvironmentSetup(python_project_config)
        mock_command_exists.return_value = True
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Installation failed: package not found"
        mock_run.return_value = mock_result

        # Act & Assert
        with pytest.raises(DependencyInstallError, match="Installation failed"):
            env_setup.install_dependencies(temp_directory)

    @patch("open_orchestrator.core.environment.EnvironmentSetup._command_exists")
    def test_install_dependencies_command_not_found(
        self,
        mock_command_exists: MagicMock,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test dependency installation failure when package manager not installed."""
        # Arrange
        env_setup = EnvironmentSetup(python_project_config)
        mock_command_exists.return_value = False

        # Act & Assert
        with pytest.raises(DependencyInstallError, match="Command not found: uv"):
            env_setup.install_dependencies(temp_directory)

    def test_install_dependencies_nonexistent_worktree(
        self,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test dependency installation failure when worktree path doesn't exist."""
        # Arrange
        env_setup = EnvironmentSetup(python_project_config)
        nonexistent_path = temp_directory / "nonexistent"

        # Act & Assert
        with pytest.raises(DependencyInstallError, match="does not exist"):
            env_setup.install_dependencies(nonexistent_path)

    @patch("subprocess.run")
    @patch("open_orchestrator.core.environment.EnvironmentSetup._command_exists")
    def test_install_dependencies_timeout(
        self,
        mock_command_exists: MagicMock,
        mock_run: MagicMock,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test dependency installation failure due to timeout."""
        # Arrange
        env_setup = EnvironmentSetup(python_project_config)
        mock_command_exists.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv sync", timeout=1)

        # Act & Assert
        with pytest.raises(DependencyInstallError, match="timed out"):
            env_setup.install_dependencies(temp_directory, timeout=1)

    @patch("subprocess.run")
    @patch("open_orchestrator.core.environment.EnvironmentSetup._command_exists")
    def test_install_dependencies_with_npm(
        self,
        mock_command_exists: MagicMock,
        mock_run: MagicMock,
        node_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test dependency installation with npm package manager."""
        # Arrange
        env_setup = EnvironmentSetup(node_project_config)
        mock_command_exists.return_value = True
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # Act
        env_setup.install_dependencies(temp_directory)

        # Assert
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args.args[0] == ["npm", "install"]


class TestSetupEnvFile:
    """Test .env file setup."""

    def test_setup_env_file_success(
        self,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test successful .env file copy."""
        # Arrange
        source_dir = temp_directory / "source"
        source_dir.mkdir()
        worktree_dir = temp_directory / "worktree"
        worktree_dir.mkdir()

        source_env = source_dir / ".env"
        source_env.write_text("SECRET_KEY=test123\nDATABASE_URL=postgres://localhost/db\n")

        python_project_config.project_root = source_dir
        env_setup = EnvironmentSetup(python_project_config)

        # Act
        target_env = env_setup.setup_env_file(worktree_dir, source_dir, adjust_paths=False)

        # Assert
        assert target_env is not None
        assert target_env == worktree_dir / ".env"
        assert target_env.exists()
        assert "SECRET_KEY=test123" in target_env.read_text()

    def test_setup_env_file_missing_source(
        self,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test .env file setup when source .env doesn't exist."""
        # Arrange
        source_dir = temp_directory / "source"
        source_dir.mkdir()
        worktree_dir = temp_directory / "worktree"
        worktree_dir.mkdir()

        python_project_config.project_root = source_dir
        env_setup = EnvironmentSetup(python_project_config)

        # Act
        result = env_setup.setup_env_file(worktree_dir, source_dir)

        # Assert
        assert result is None

    def test_setup_env_file_with_path_adjustment(
        self,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test .env file copy with path adjustment."""
        # Arrange
        source_dir = temp_directory / "source"
        source_dir.mkdir()
        worktree_dir = temp_directory / "worktree"
        worktree_dir.mkdir()

        source_env_content = f"DATABASE_URL=sqlite:///{source_dir}/db.sqlite\nSECRET_KEY=test\n"
        source_env = source_dir / ".env"
        source_env.write_text(source_env_content)

        python_project_config.project_root = source_dir
        env_setup = EnvironmentSetup(python_project_config)

        # Act
        target_env = env_setup.setup_env_file(worktree_dir, source_dir, adjust_paths=True)

        # Assert
        assert target_env is not None
        content = target_env.read_text()
        assert str(worktree_dir) in content
        assert str(source_dir) not in content
        assert "SECRET_KEY=test" in content

    def test_adjust_env_paths(
        self,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test path adjustment in .env content."""
        # Arrange
        source_dir = temp_directory / "source"
        worktree_dir = temp_directory / "worktree"
        env_setup = EnvironmentSetup(python_project_config)

        content = f"""
DATABASE_URL=sqlite:///{source_dir}/db.sqlite
LOG_PATH={source_dir}/logs
CACHE_DIR={source_dir}/cache
SECRET_KEY=unchanged
"""

        # Act
        adjusted = env_setup._adjust_env_paths(content, source_dir, worktree_dir)

        # Assert
        assert str(worktree_dir) in adjusted
        assert str(source_dir) not in adjusted
        assert "SECRET_KEY=unchanged" in adjusted

    def test_setup_env_file_permissions(
        self,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test .env file is created with secure permissions."""
        # Arrange
        source_dir = temp_directory / "source"
        source_dir.mkdir()
        worktree_dir = temp_directory / "worktree"
        worktree_dir.mkdir()

        source_env = source_dir / ".env"
        source_env.write_text("SECRET_KEY=test123\n")

        python_project_config.project_root = source_dir
        env_setup = EnvironmentSetup(python_project_config)

        # Act
        target_env = env_setup.setup_env_file(worktree_dir, source_dir)

        # Assert
        assert target_env is not None
        assert target_env.exists()
        # Check permissions are restrictive (0o600 = owner read/write only)
        import stat
        mode = target_env.stat().st_mode
        permissions = stat.S_IMODE(mode)
        assert permissions == 0o600


class TestSetupWorktree:
    """Test complete worktree setup."""

    @patch("open_orchestrator.core.environment.EnvironmentSetup.install_dependencies")
    @patch("open_orchestrator.core.environment.EnvironmentSetup.setup_env_file")
    def test_setup_worktree_complete(
        self,
        mock_setup_env: MagicMock,
        mock_install_deps: MagicMock,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test complete worktree setup with dependencies and .env."""
        # Arrange
        worktree_dir = temp_directory / "worktree"
        worktree_dir.mkdir()
        source_dir = temp_directory / "source"
        source_dir.mkdir()

        env_setup = EnvironmentSetup(python_project_config)
        mock_setup_env.return_value = worktree_dir / ".env"
        mock_install_deps.return_value = MagicMock()

        # Act
        env_setup.setup_worktree(worktree_dir, source_dir, install_deps=True, copy_env=True)

        # Assert
        mock_setup_env.assert_called_once()
        mock_install_deps.assert_called_once()

    @patch("open_orchestrator.core.environment.EnvironmentSetup.install_dependencies")
    @patch("open_orchestrator.core.environment.EnvironmentSetup.setup_env_file")
    def test_setup_worktree_no_deps(
        self,
        mock_setup_env: MagicMock,
        mock_install_deps: MagicMock,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test worktree setup without dependency installation."""
        # Arrange
        worktree_dir = temp_directory / "worktree"
        worktree_dir.mkdir()
        source_dir = temp_directory / "source"
        source_dir.mkdir()

        env_setup = EnvironmentSetup(python_project_config)
        mock_setup_env.return_value = worktree_dir / ".env"

        # Act
        env_setup.setup_worktree(worktree_dir, source_dir, install_deps=False, copy_env=True)

        # Assert
        mock_setup_env.assert_called_once()
        mock_install_deps.assert_not_called()

    @patch("open_orchestrator.core.environment.EnvironmentSetup.install_dependencies")
    @patch("open_orchestrator.core.environment.EnvironmentSetup.setup_env_file")
    def test_setup_worktree_no_env(
        self,
        mock_setup_env: MagicMock,
        mock_install_deps: MagicMock,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test worktree setup without .env file copy."""
        # Arrange
        worktree_dir = temp_directory / "worktree"
        worktree_dir.mkdir()
        source_dir = temp_directory / "source"
        source_dir.mkdir()

        env_setup = EnvironmentSetup(python_project_config)
        mock_install_deps.return_value = MagicMock()

        # Act
        env_setup.setup_worktree(worktree_dir, source_dir, install_deps=True, copy_env=False)

        # Assert
        mock_setup_env.assert_not_called()
        mock_install_deps.assert_called_once()


class TestCommandExists:
    """Test command existence checking."""

    @patch("shutil.which")
    def test_command_exists_found(
        self,
        mock_which: MagicMock,
        python_project_config: ProjectConfig,
    ) -> None:
        """Test command existence check when command is found."""
        # Arrange
        env_setup = EnvironmentSetup(python_project_config)
        mock_which.return_value = "/usr/bin/uv"

        # Act
        result = env_setup._command_exists("uv")

        # Assert
        assert result is True
        mock_which.assert_called_once_with("uv")

    @patch("shutil.which")
    def test_command_exists_not_found(
        self,
        mock_which: MagicMock,
        python_project_config: ProjectConfig,
    ) -> None:
        """Test command existence check when command is not found."""
        # Arrange
        env_setup = EnvironmentSetup(python_project_config)
        mock_which.return_value = None

        # Act
        result = env_setup._command_exists("nonexistent-command")

        # Assert
        assert result is False


class TestCopyAdditionalConfigFiles:
    """Test copying additional configuration files."""

    def test_copy_additional_config_files_default(
        self,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test copying additional config files with default file list."""
        # Arrange
        source_dir = temp_directory / "source"
        source_dir.mkdir()
        worktree_dir = temp_directory / "worktree"
        worktree_dir.mkdir()

        # Create some config files
        (source_dir / ".env.local").write_text("LOCAL_VAR=value\n")
        (source_dir / "config.local.yaml").write_text("key: value\n")

        python_project_config.project_root = source_dir
        env_setup = EnvironmentSetup(python_project_config)

        # Act
        copied = env_setup.copy_additional_config_files(worktree_dir, source_dir)

        # Assert
        assert len(copied) >= 1
        assert any(f.name == ".env.local" for f in copied)
        assert (worktree_dir / ".env.local").exists()

    def test_copy_additional_config_files_custom_list(
        self,
        python_project_config: ProjectConfig,
        temp_directory: Path,
    ) -> None:
        """Test copying additional config files with custom file list."""
        # Arrange
        source_dir = temp_directory / "source"
        source_dir.mkdir()
        worktree_dir = temp_directory / "worktree"
        worktree_dir.mkdir()

        (source_dir / "custom.config").write_text("custom config\n")

        python_project_config.project_root = source_dir
        env_setup = EnvironmentSetup(python_project_config)

        # Act
        copied = env_setup.copy_additional_config_files(worktree_dir, source_dir, files=["custom.config"])

        # Assert
        assert len(copied) == 1
        assert copied[0].name == "custom.config"
        assert (worktree_dir / "custom.config").exists()

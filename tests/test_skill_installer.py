"""
Tests for SkillInstaller class and CLI skill commands.
"""

import filecmp
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from open_orchestrator.cli import main
from open_orchestrator.core.skill_installer import (
    SkillInstaller,
    SkillInstallError,
    SkillNotFoundError,
)


@pytest.fixture
def skill_installer(mock_skills_dir: Path) -> SkillInstaller:
    """
    Create a SkillInstaller instance with mocked target directory.

    Args:
        mock_skills_dir: Fixture providing temporary skills directory

    Returns:
        SkillInstaller instance configured for testing
    """
    installer = SkillInstaller()
    installer.target_dir = mock_skills_dir / "open-orchestrator"
    installer.target_file = installer.target_dir / "SKILL.md"
    return installer


class TestSkillInstaller:
    """Test SkillInstaller class methods."""

    def test_install_creates_symlink_by_default(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that install() creates symlink by default."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            result = skill_installer.install(symlink=True, force=False)

            # Assert
            assert result == skill_installer.target_file
            assert skill_installer.target_file.is_symlink()
            assert skill_installer.target_file.resolve() == source_file.resolve()

    def test_install_with_symlink_false_creates_copy(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that install() with symlink=False creates file copy."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            result = skill_installer.install(symlink=False, force=False)

            # Assert
            assert result == skill_installer.target_file
            assert not skill_installer.target_file.is_symlink()
            assert skill_installer.target_file.exists()
            assert filecmp.cmp(str(source_file), str(skill_installer.target_file), shallow=False)

    def test_install_with_copy_true_creates_file_copy(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that install() with symlink=False copies file content."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        original_content = source_file.read_text()

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            skill_installer.install(symlink=False, force=False)

            # Assert
            assert skill_installer.target_file.read_text() == original_content
            assert not skill_installer.target_file.is_symlink()

    def test_install_with_force_overwrites_existing_symlink(
        self, skill_installer: SkillInstaller, skills_source_dir: Path, temp_directory: Path
    ) -> None:
        """Test that install() with force=True overwrites existing symlink."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)

        # Create existing symlink to different location
        old_target = temp_directory / "old_skill.md"
        old_target.write_text("old content")
        skill_installer.target_file.symlink_to(old_target)

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            skill_installer.install(symlink=True, force=True)

            # Assert
            assert skill_installer.target_file.is_symlink()
            assert skill_installer.target_file.resolve() == source_file.resolve()

    def test_install_with_force_overwrites_existing_copy(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that install() with force=True overwrites existing file copy."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)

        # Create existing file with different content
        skill_installer.target_file.write_text("old content")

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            skill_installer.install(symlink=False, force=True)

            # Assert
            assert not skill_installer.target_file.is_symlink()
            assert filecmp.cmp(str(source_file), str(skill_installer.target_file), shallow=False)

    def test_install_without_force_raises_error_if_symlink_exists(
        self, skill_installer: SkillInstaller, skills_source_dir: Path, temp_directory: Path
    ) -> None:
        """Test that install() raises SkillInstallError if symlink already installed."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)

        # Create existing symlink
        old_target = temp_directory / "existing.md"
        old_target.write_text("content")
        skill_installer.target_file.symlink_to(old_target)

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act & Assert
            with pytest.raises(SkillInstallError, match="already installed.*symlink"):
                skill_installer.install(symlink=True, force=False)

    def test_install_without_force_raises_error_if_copy_exists(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that install() raises SkillInstallError if copy already installed."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.write_text("existing content")

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act & Assert
            with pytest.raises(SkillInstallError, match="already installed"):
                skill_installer.install(symlink=False, force=False)

    def test_install_creates_parent_directories(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that install() creates parent directories if they don't exist."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        assert not skill_installer.target_dir.exists()

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            skill_installer.install(symlink=True, force=False)

            # Assert
            assert skill_installer.target_dir.exists()
            assert skill_installer.target_file.exists()

    def test_install_raises_error_on_permission_denied(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that install() raises SkillInstallError on permission errors."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            with patch("pathlib.Path.mkdir", side_effect=OSError("Permission denied")):
                # Act & Assert
                with pytest.raises(SkillInstallError, match="Failed to install skill"):
                    skill_installer.install(symlink=True, force=False)

    def test_uninstall_removes_skill_file(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that uninstall() removes skill file."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.symlink_to(source_file)

        # Act
        result = skill_installer.uninstall()

        # Assert
        assert result is True
        assert not skill_installer.target_file.exists()

    def test_uninstall_removes_empty_directory(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that uninstall() removes empty parent directory."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.symlink_to(source_file)

        # Act
        skill_installer.uninstall()

        # Assert
        assert not skill_installer.target_dir.exists()

    def test_uninstall_keeps_directory_with_other_files(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that uninstall() keeps directory if it contains other files."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.symlink_to(source_file)

        # Add another file to the directory
        other_file = skill_installer.target_dir / "other.txt"
        other_file.write_text("keep this")

        # Act
        skill_installer.uninstall()

        # Assert
        assert skill_installer.target_dir.exists()
        assert other_file.exists()

    def test_uninstall_returns_false_if_not_installed(
        self, skill_installer: SkillInstaller
    ) -> None:
        """Test that uninstall() returns False if skill not installed."""
        # Arrange
        assert not skill_installer.is_installed()

        # Act
        result = skill_installer.uninstall()

        # Assert
        assert result is False

    def test_uninstall_raises_error_on_permission_denied(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that uninstall() raises SkillInstallError on permission errors."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.symlink_to(source_file)

        with patch("pathlib.Path.unlink", side_effect=OSError("Permission denied")):
            # Act & Assert
            with pytest.raises(SkillInstallError, match="Failed to uninstall skill"):
                skill_installer.uninstall()

    def test_is_installed_returns_true_when_skill_exists(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that is_installed() returns True when skill file exists."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.symlink_to(source_file)

        # Act
        result = skill_installer.is_installed()

        # Assert
        assert result is True

    def test_is_installed_returns_false_when_skill_missing(
        self, skill_installer: SkillInstaller
    ) -> None:
        """Test that is_installed() returns False when skill not installed."""
        # Arrange
        assert not skill_installer.target_file.exists()

        # Act
        result = skill_installer.is_installed()

        # Assert
        assert result is False

    def test_is_installed_returns_true_for_broken_symlink(
        self, skill_installer: SkillInstaller, temp_directory: Path
    ) -> None:
        """Test that is_installed() returns True for broken symlinks."""
        # Arrange
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)

        # Create symlink to non-existent file
        nonexistent = temp_directory / "nonexistent.md"
        skill_installer.target_file.symlink_to(nonexistent)

        # Verify it's a broken symlink
        assert skill_installer.target_file.is_symlink()
        assert not skill_installer.target_file.exists()

        # Act
        result = skill_installer.is_installed()

        # Assert
        assert result is True

    def test_is_symlink_correctly_identifies_symlinks(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that is_symlink() correctly identifies symlinks."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.symlink_to(source_file)

        # Act
        result = skill_installer.is_symlink()

        # Assert
        assert result is True

    def test_is_symlink_returns_false_for_copied_files(
        self, skill_installer: SkillInstaller
    ) -> None:
        """Test that is_symlink() returns False for copied files."""
        # Arrange
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.write_text("content")

        # Act
        result = skill_installer.is_symlink()

        # Assert
        assert result is False

    def test_is_symlink_returns_false_when_not_installed(
        self, skill_installer: SkillInstaller
    ) -> None:
        """Test that is_symlink() returns False when skill not installed."""
        # Arrange
        assert not skill_installer.target_file.exists()

        # Act
        result = skill_installer.is_symlink()

        # Assert
        assert result is False

    def test_is_up_to_date_returns_true_for_valid_symlinks(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that is_up_to_date() returns True for symlinks to correct source."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.symlink_to(source_file)

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            result = skill_installer.is_up_to_date()

            # Assert
            assert result is True

    def test_is_up_to_date_returns_true_for_matching_copies(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that is_up_to_date() returns True for file copies matching source."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)

        # Copy file content
        skill_installer.target_file.write_text(source_file.read_text())

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            result = skill_installer.is_up_to_date()

            # Assert
            assert result is True

    def test_is_up_to_date_returns_false_for_modified_copies(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that is_up_to_date() returns False for modified file copies."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)

        # Write different content
        skill_installer.target_file.write_text("modified content")

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            result = skill_installer.is_up_to_date()

            # Assert
            assert result is False

    def test_is_up_to_date_returns_false_when_not_installed(
        self, skill_installer: SkillInstaller
    ) -> None:
        """Test that is_up_to_date() returns False when skill not installed."""
        # Arrange
        assert not skill_installer.is_installed()

        # Act
        result = skill_installer.is_up_to_date()

        # Assert
        assert result is False

    def test_is_up_to_date_returns_false_for_symlink_to_wrong_source(
        self, skill_installer: SkillInstaller, skills_source_dir: Path, temp_directory: Path
    ) -> None:
        """Test that is_up_to_date() returns False for symlinks to wrong source."""
        # Arrange
        correct_source = skills_source_dir / "SKILL.md"
        wrong_source = temp_directory / "wrong.md"
        wrong_source.write_text("wrong content")

        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.symlink_to(wrong_source)

        with patch.object(skill_installer, "_get_source_path", return_value=correct_source):
            # Act
            result = skill_installer.is_up_to_date()

            # Assert
            assert result is False

    def test_get_source_path_finds_skill_in_package(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that get_source_path() finds SKILL.md in package."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"

        with patch.object(skill_installer, "_get_source_path", return_value=source_file):
            # Act
            result = skill_installer.get_source_path()

            # Assert
            assert result == source_file
            assert result.exists()

    def test_get_source_path_raises_error_if_skill_missing(
        self, skill_installer: SkillInstaller
    ) -> None:
        """Test that _get_source_path() raises SkillNotFoundError if file missing."""
        # Arrange
        with patch("pathlib.Path.exists", return_value=False):
            # Act & Assert
            with pytest.raises(SkillNotFoundError, match="Skill file not found"):
                skill_installer._get_source_path()

    def test_get_target_path_returns_correct_path(
        self, skill_installer: SkillInstaller
    ) -> None:
        """Test that get_target_path() returns correct target path."""
        # Act
        result = skill_installer.get_target_path()

        # Assert
        assert result == skill_installer.target_file
        assert result.name == "SKILL.md"

    def test_get_symlink_target_returns_path_for_symlinks(
        self, skill_installer: SkillInstaller, skills_source_dir: Path
    ) -> None:
        """Test that get_symlink_target() returns path for symlinks."""
        # Arrange
        source_file = skills_source_dir / "SKILL.md"
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.symlink_to(source_file)

        # Act
        result = skill_installer.get_symlink_target()

        # Assert
        assert result is not None
        assert result.resolve() == source_file.resolve()

    def test_get_symlink_target_returns_none_for_copies(
        self, skill_installer: SkillInstaller
    ) -> None:
        """Test that get_symlink_target() returns None for copied files."""
        # Arrange
        skill_installer.target_dir.mkdir(parents=True, exist_ok=True)
        skill_installer.target_file.write_text("content")

        # Act
        result = skill_installer.get_symlink_target()

        # Assert
        assert result is None

    def test_get_symlink_target_returns_none_when_not_installed(
        self, skill_installer: SkillInstaller
    ) -> None:
        """Test that get_symlink_target() returns None when not installed."""
        # Arrange
        assert not skill_installer.target_file.exists()

        # Act
        result = skill_installer.get_symlink_target()

        # Assert
        assert result is None


class TestSkillCLI:
    """Test CLI skill commands."""

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_install_creates_symlink_by_default(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that 'owt skill install' creates symlink by default."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.target_dir = temp_directory / "skills" / "open-orchestrator"
        mock_installer.target_file = mock_installer.target_dir / "SKILL.md"
        mock_installer.SKILL_FILE = "SKILL.md"
        mock_installer.install.return_value = mock_installer.target_file
        mock_installer.get_source_path.return_value = Path("/source/SKILL.md")

        # Act
        result = cli_runner.invoke(main, ["skill", "install"])

        # Assert
        assert result.exit_code == 0
        mock_installer.install.assert_called_once_with(symlink=True, force=False)
        assert "Skill installed successfully" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_install_with_copy_flag(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that 'owt skill install --copy' creates file copy."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.target_dir = temp_directory / "skills" / "open-orchestrator"
        mock_installer.target_file = mock_installer.target_dir / "SKILL.md"
        mock_installer.SKILL_FILE = "SKILL.md"
        mock_installer.install.return_value = mock_installer.target_file

        # Act
        result = cli_runner.invoke(main, ["skill", "install", "--copy"])

        # Assert
        assert result.exit_code == 0
        mock_installer.install.assert_called_once_with(symlink=False, force=False)
        assert "Copied SKILL.md" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_install_with_force_flag(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that 'owt skill install --force' overwrites existing installation."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.target_dir = temp_directory / "skills" / "open-orchestrator"
        mock_installer.target_file = mock_installer.target_dir / "SKILL.md"
        mock_installer.SKILL_FILE = "SKILL.md"
        mock_installer.install.return_value = mock_installer.target_file
        mock_installer.get_source_path.return_value = Path("/source/SKILL.md")

        # Act
        result = cli_runner.invoke(main, ["skill", "install", "--force"])

        # Assert
        assert result.exit_code == 0
        mock_installer.install.assert_called_once_with(symlink=True, force=True)

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_install_fails_without_force_if_already_installed(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test that install fails without --force if skill already exists."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.install.side_effect = SkillInstallError("Skill already installed")

        # Act
        result = cli_runner.invoke(main, ["skill", "install"])

        # Assert
        assert result.exit_code != 0
        assert "Skill already installed" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_install_handles_skill_not_found_error(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test that install handles SkillNotFoundError gracefully."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.install.side_effect = SkillNotFoundError("Skill file not found in package")

        # Act
        result = cli_runner.invoke(main, ["skill", "install"])

        # Assert
        assert result.exit_code != 0
        assert "Skill file not found" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_uninstall_removes_skill_after_confirmation(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that 'owt skill uninstall' removes skill after user confirms."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.target_dir = temp_directory / "skills" / "open-orchestrator"
        mock_installer.target_file = mock_installer.target_dir / "SKILL.md"
        mock_installer.is_installed.return_value = True
        mock_installer.uninstall.return_value = True

        # Act
        result = cli_runner.invoke(main, ["skill", "uninstall"], input="y\n")

        # Assert
        assert result.exit_code == 0
        mock_installer.uninstall.assert_called_once()
        assert "Skill uninstalled successfully" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_uninstall_with_yes_flag_skips_confirmation(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that 'owt skill uninstall -y' skips confirmation."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.target_dir = temp_directory / "skills" / "open-orchestrator"
        mock_installer.target_file = mock_installer.target_dir / "SKILL.md"
        mock_installer.is_installed.return_value = True
        mock_installer.uninstall.return_value = True

        # Act
        result = cli_runner.invoke(main, ["skill", "uninstall", "-y"])

        # Assert
        assert result.exit_code == 0
        mock_installer.uninstall.assert_called_once()
        assert "Skill uninstalled successfully" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_uninstall_shows_message_if_not_installed(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test that uninstall shows message if skill not installed."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.is_installed.return_value = False

        # Act
        result = cli_runner.invoke(main, ["skill", "uninstall"])

        # Assert
        assert result.exit_code == 0
        assert "not installed" in result.output
        mock_installer.uninstall.assert_not_called()

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_uninstall_cancels_on_no_confirmation(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that uninstall cancels when user declines confirmation."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.target_file = temp_directory / "skills" / "open-orchestrator" / "SKILL.md"
        mock_installer.is_installed.return_value = True

        # Act
        result = cli_runner.invoke(main, ["skill", "uninstall"], input="n\n")

        # Assert
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        mock_installer.uninstall.assert_not_called()

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_uninstall_handles_errors(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test that uninstall handles SkillInstallError gracefully."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.is_installed.return_value = True
        mock_installer.uninstall.side_effect = SkillInstallError("Permission denied")

        # Act
        result = cli_runner.invoke(main, ["skill", "uninstall", "-y"])

        # Assert
        assert result.exit_code != 0
        assert "Permission denied" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_status_shows_not_installed(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner
    ) -> None:
        """Test that 'owt skill status' shows 'Not installed' when missing."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.is_installed.return_value = False

        # Act
        result = cli_runner.invoke(main, ["skill", "status"])

        # Assert
        assert result.exit_code == 0
        assert "Not installed" in result.output
        assert "owt skill install" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_status_shows_installation_details_for_symlink(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that status shows installation details for symlinked skill."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.is_installed.return_value = True
        mock_installer.is_symlink.return_value = True
        mock_installer.target_file = temp_directory / "skills" / "open-orchestrator" / "SKILL.md"
        source_path = temp_directory / "source" / "SKILL.md"
        mock_installer.get_source_path.return_value = source_path
        mock_installer.get_symlink_target.return_value = source_path
        mock_installer.is_up_to_date.return_value = True

        # Act
        result = cli_runner.invoke(main, ["skill", "status"])

        # Assert
        assert result.exit_code == 0
        assert "Installed" in result.output
        assert "symlink" in result.output
        assert "Up-to-date" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_status_shows_installation_details_for_copy(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that status shows installation details for copied skill."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.is_installed.return_value = True
        mock_installer.is_symlink.return_value = False
        mock_installer.target_file = temp_directory / "skills" / "open-orchestrator" / "SKILL.md"
        source_path = temp_directory / "source" / "SKILL.md"
        mock_installer.get_source_path.return_value = source_path
        mock_installer.get_symlink_target.return_value = None
        mock_installer.is_up_to_date.return_value = True

        # Act
        result = cli_runner.invoke(main, ["skill", "status"])

        # Assert
        assert result.exit_code == 0
        assert "Installed" in result.output
        assert "copy" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_status_shows_out_of_sync_warning(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that status shows warning for out-of-sync copied skill."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.is_installed.return_value = True
        mock_installer.is_symlink.return_value = False
        mock_installer.target_file = temp_directory / "skills" / "open-orchestrator" / "SKILL.md"
        source_path = temp_directory / "source" / "SKILL.md"
        mock_installer.get_source_path.return_value = source_path
        mock_installer.is_up_to_date.return_value = False

        # Act
        result = cli_runner.invoke(main, ["skill", "status"])

        # Assert
        assert result.exit_code == 0
        assert "owt skill install --force" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_status_handles_source_not_found_gracefully(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that status handles missing source file gracefully."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.is_installed.return_value = True
        mock_installer.is_symlink.return_value = False
        mock_installer.target_file = temp_directory / "skills" / "open-orchestrator" / "SKILL.md"
        mock_installer.get_source_path.side_effect = SkillNotFoundError("Source not found")
        mock_installer.is_up_to_date.side_effect = SkillNotFoundError("Source not found")

        # Act
        result = cli_runner.invoke(main, ["skill", "status"])

        # Assert
        assert result.exit_code == 0
        assert "Not found in package" in result.output
        assert "Cannot verify" in result.output

    @patch("open_orchestrator.core.skill_installer.SkillInstaller")
    def test_skill_commands_exit_with_zero_on_success(
        self, mock_installer_class: MagicMock, cli_runner: CliRunner, temp_directory: Path
    ) -> None:
        """Test that CLI commands exit with code 0 on success."""
        # Arrange
        mock_installer = mock_installer_class.return_value
        mock_installer.target_dir = temp_directory / "skills" / "open-orchestrator"
        mock_installer.target_file = mock_installer.target_dir / "SKILL.md"
        mock_installer.SKILL_FILE = "SKILL.md"
        mock_installer.install.return_value = mock_installer.target_file
        mock_installer.get_source_path.return_value = Path("/source/SKILL.md")
        mock_installer.is_installed.return_value = False

        # Act - install command
        result = cli_runner.invoke(main, ["skill", "install"])

        # Assert
        assert result.exit_code == 0

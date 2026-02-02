"""
Tests for pytest fixtures defined in conftest.py.

Tests verify that fixtures create correct directory structures, mock objects,
and provide expected data for testing Open Orchestrator components.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock


class TestSkillInstallationFixtures:
    """Tests for skill installation fixtures."""

    def test_skills_source_dir_structure(self, skills_source_dir: Path) -> None:
        """Test skills_source_dir creates correct directory structure with SKILL.md."""
        # Arrange & Act
        # (fixture already creates the directory)

        # Assert
        assert skills_source_dir.exists()
        assert skills_source_dir.is_dir()
        assert (skills_source_dir / "SKILL.md").exists()
        assert (skills_source_dir / "SKILL.md").is_file()

    def test_skills_source_dir_content(self, skills_source_dir: Path) -> None:
        """Test SKILL.md contains expected content."""
        # Arrange
        skill_file = skills_source_dir / "SKILL.md"

        # Act
        content = skill_file.read_text()

        # Assert
        assert "# Open Orchestrator Skill" in content
        assert "/worktree" in content
        assert "/wt-create" in content
        assert "Git Worktree" in content

    def test_mock_skills_dir_structure(self, mock_skills_dir: Path) -> None:
        """Test mock_skills_dir creates empty target directory."""
        # Arrange & Act
        # (fixture already creates the directory)

        # Assert
        assert mock_skills_dir.exists()
        assert mock_skills_dir.is_dir()
        assert mock_skills_dir.name == "skills"
        assert mock_skills_dir.parent.name == ".claude"

    def test_mock_skills_dir_is_empty(self, mock_skills_dir: Path) -> None:
        """Test mock_skills_dir starts empty."""
        # Arrange & Act
        # (fixture already creates the directory)

        # Assert
        assert list(mock_skills_dir.iterdir()) == []


class TestHookTestingFixtures:
    """Tests for hook testing fixtures."""

    def test_hooks_config_directory_structure(self, hooks_config: Path) -> None:
        """Test hooks_config creates correct directory structure."""
        # Arrange & Act
        # (fixture already creates the directory)

        # Assert
        assert hooks_config.exists()
        assert hooks_config.is_dir()
        assert hooks_config.name == ".open-orchestrator"
        assert (hooks_config / "hooks.json").exists()

    def test_hooks_config_valid_json(self, hooks_config: Path) -> None:
        """Test hooks_config creates valid JSON configuration."""
        # Arrange
        hooks_file = hooks_config / "hooks.json"

        # Act
        content = hooks_file.read_text()
        data = json.loads(content)

        # Assert
        assert "hooks" in data
        assert isinstance(data["hooks"], list)
        assert len(data["hooks"]) >= 2

    def test_hooks_config_shell_hook(self, hooks_config: Path) -> None:
        """Test hooks_config includes shell command hook."""
        # Arrange
        hooks_file = hooks_config / "hooks.json"

        # Act
        data = json.loads(hooks_file.read_text())
        shell_hooks = [h for h in data["hooks"] if h["type"] == "shell"]

        # Assert
        assert len(shell_hooks) >= 1
        shell_hook = shell_hooks[0]
        assert "command" in shell_hook
        assert "events" in shell_hook
        assert shell_hook["enabled"] is True

    def test_hooks_config_webhook_hook(self, hooks_config: Path) -> None:
        """Test hooks_config includes webhook hook."""
        # Arrange
        hooks_file = hooks_config / "hooks.json"

        # Act
        data = json.loads(hooks_file.read_text())
        webhook_hooks = [h for h in data["hooks"] if h["type"] == "webhook"]

        # Assert
        assert len(webhook_hooks) >= 1
        webhook_hook = webhook_hooks[0]
        assert "url" in webhook_hook
        assert "events" in webhook_hook
        assert webhook_hook["enabled"] is True

    def test_mock_subprocess_returns_success(self, mock_subprocess: MagicMock) -> None:
        """Test mock_subprocess properly mocks subprocess.run."""
        # Arrange & Act
        result = mock_subprocess.return_value

        # Assert
        assert result.returncode == 0
        assert result.stdout == "Hook executed successfully"
        assert result.stderr == ""


class TestSessionManagementFixtures:
    """Tests for session management fixtures."""

    def test_temp_session_dir_structure(self, temp_session_dir: Path) -> None:
        """Test temp_session_dir creates directory structure."""
        # Arrange & Act
        # (fixture already creates the directory)

        # Assert
        assert temp_session_dir.exists()
        assert temp_session_dir.is_dir()
        assert temp_session_dir.name == "sessions"
        assert temp_session_dir.parent.name == ".open-orchestrator"

    def test_mock_session_store_has_sessions(self, mock_session_store: Path) -> None:
        """Test mock_session_store provides pre-populated session data."""
        # Arrange & Act
        session_dirs = list(mock_session_store.iterdir())

        # Assert
        assert len(session_dirs) >= 2
        assert all(d.is_dir() for d in session_dirs)

    def test_mock_session_store_worktree_1(self, mock_session_store: Path) -> None:
        """Test mock_session_store contains worktree-1 session."""
        # Arrange
        session_1 = mock_session_store / "worktree-1"
        session_file = session_1 / "session.json"

        # Act
        assert session_1.exists()
        assert session_file.exists()

        data = json.loads(session_file.read_text())

        # Assert
        assert data["worktree_name"] == "worktree-1"
        assert data["session_id"] == "abc123"
        assert "conversation_data" in data
        assert data["message_count"] == 15

    def test_mock_session_store_worktree_2(self, mock_session_store: Path) -> None:
        """Test mock_session_store contains worktree-2 session."""
        # Arrange
        session_2 = mock_session_store / "worktree-2"
        session_file = session_2 / "session.json"

        # Act
        assert session_2.exists()
        assert session_file.exists()

        data = json.loads(session_file.read_text())

        # Assert
        assert data["worktree_name"] == "worktree-2"
        assert data["session_id"] == "def456"
        assert "conversation_data" in data
        assert data["message_count"] == 8


class TestPRLinkingFixtures:
    """Tests for PR linking fixtures."""

    def test_mock_gh_cli_returns_pr_list(self, mock_gh_cli: MagicMock) -> None:
        """Test mock_gh_cli properly mocks subprocess.run for GitHub CLI."""
        # Arrange & Act
        result = mock_gh_cli.return_value

        # Assert
        assert result.returncode == 0
        assert result.stdout
        assert result.stderr == ""

        # Parse JSON output
        pr_data = json.loads(result.stdout)
        assert isinstance(pr_data, list)
        assert len(pr_data) >= 2

    def test_mock_gh_cli_pr_structure(self, mock_gh_cli: MagicMock) -> None:
        """Test mock_gh_cli PR data has correct structure."""
        # Arrange & Act
        result = mock_gh_cli.return_value
        pr_data = json.loads(result.stdout)
        first_pr = pr_data[0]

        # Assert
        assert "number" in first_pr
        assert "title" in first_pr
        assert "state" in first_pr
        assert "url" in first_pr
        assert first_pr["number"] == 123
        assert first_pr["state"] == "OPEN"

    def test_pr_store_directory_structure(self, pr_store: Path) -> None:
        """Test pr_store creates temporary directory structure."""
        # Arrange & Act
        # (fixture already creates the directory)

        # Assert
        assert pr_store.exists()
        assert pr_store.is_dir()
        assert pr_store.name == "prs"
        assert pr_store.parent.name == ".open-orchestrator"


class TestProcessManagerFixtures:
    """Tests for process manager fixtures."""

    def test_temp_pids_directory_structure(self, temp_pids: Path) -> None:
        """Test temp_pids creates directory for PID file storage."""
        # Arrange & Act
        # (fixture already creates the directory)

        # Assert
        assert temp_pids.exists()
        assert temp_pids.is_dir()
        assert temp_pids.name == "processes"
        assert temp_pids.parent.name == ".open-orchestrator"

    def test_mock_process_attributes(self, mock_process: MagicMock) -> None:
        """Test mock_process returns process with standard attributes."""
        # Arrange & Act
        # (fixture already creates the mock)

        # Assert
        assert mock_process.pid == 12345
        assert mock_process.returncode is None
        assert mock_process.poll() is None
        assert hasattr(mock_process, "terminate")
        assert hasattr(mock_process, "kill")
        assert hasattr(mock_process, "wait")

    def test_mock_process_lifecycle(self, mock_process: MagicMock) -> None:
        """Test mock_process simulates process lifecycle."""
        # Arrange & Act
        poll_result = mock_process.poll()
        terminate_result = mock_process.terminate()
        wait_result = mock_process.wait()

        # Assert
        assert poll_result is None  # Process is running
        assert terminate_result is None
        assert wait_result == 0  # Process exited successfully


class TestDashboardFixtures:
    """Tests for dashboard testing fixtures."""

    def test_mock_status_tracker_has_worktrees(self, mock_status_tracker: MagicMock) -> None:
        """Test mock_status_tracker returns StatusTracker with test worktree data."""
        # Arrange & Act
        all_statuses = mock_status_tracker.get_all_statuses()

        # Assert
        assert len(all_statuses) == 3
        assert all(hasattr(status, "worktree_name") for status in all_statuses)
        assert all(hasattr(status, "activity_status") for status in all_statuses)

    def test_mock_status_tracker_worktree_names(self, mock_status_tracker: MagicMock) -> None:
        """Test mock_status_tracker has expected worktree names."""
        # Arrange & Act
        all_statuses = mock_status_tracker.get_all_statuses()
        names = [status.worktree_name for status in all_statuses]

        # Assert
        assert "feature-auth" in names
        assert "fix-dashboard" in names
        assert "refactor-api" in names

    def test_mock_status_tracker_summary(self, mock_status_tracker: MagicMock) -> None:
        """Test mock_status_tracker provides summary data."""
        # Arrange & Act
        summary = mock_status_tracker.get_summary()

        # Assert
        assert hasattr(summary, "total_worktrees")
        assert hasattr(summary, "active_ai_sessions")
        assert hasattr(summary, "idle_ai_sessions")
        assert hasattr(summary, "blocked_ai_sessions")
        assert summary.total_worktrees == 3
        assert summary.active_ai_sessions == 2
        assert summary.idle_ai_sessions == 1
        assert summary.blocked_ai_sessions == 1


class TestFixtureIntegration:
    """Integration tests for fixture combinations."""

    def test_fixtures_use_consistent_temp_directory(
        self,
        temp_session_dir: Path,
        hooks_config: Path,
        pr_store: Path,
        temp_pids: Path
    ) -> None:
        """Test multiple fixtures share common temporary directory root."""
        # Arrange & Act
        # (fixtures already created)

        # Assert
        # All fixtures should be under .open-orchestrator directory
        assert temp_session_dir.parent.name == ".open-orchestrator"
        assert hooks_config.name == ".open-orchestrator"
        assert pr_store.parent.name == ".open-orchestrator"
        assert temp_pids.parent.name == ".open-orchestrator"

    def test_skill_fixtures_work_together(
        self,
        skills_source_dir: Path,
        mock_skills_dir: Path
    ) -> None:
        """Test skill installation fixtures can be used together."""
        # Arrange
        source_skill = skills_source_dir / "SKILL.md"
        target_dir = mock_skills_dir / "open-orchestrator"

        # Act
        target_dir.mkdir(parents=True, exist_ok=True)
        target_skill = target_dir / "SKILL.md"
        target_skill.write_text(source_skill.read_text())

        # Assert
        assert target_skill.exists()
        assert target_skill.read_text() == source_skill.read_text()

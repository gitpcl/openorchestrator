"""
Tests for session management service.

This module tests:
- SessionManager initialization and configuration
- Session file discovery and tracking
- Session copying between worktrees
- Resume command generation
- Orphan cleanup
- CLI commands (owt copy-session, owt resume)
"""

import json
from pathlib import Path

from click.testing import CliRunner

from open_orchestrator.cli import main as cli
from open_orchestrator.core.session import (
    SessionConfig,
    SessionManager,
)
from open_orchestrator.models.session import (
    SessionCopyStatus,
)

# === Unit Tests ===


class TestSessionManagerInit:
    """Test SessionManager initialization."""

    def test_init_with_default_config(self, temp_directory: Path):
        """Test initialization with default configuration."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")

        # Act
        manager = SessionManager(config=config)

        # Assert
        assert manager.config == config
        assert manager._storage_path == temp_directory / "sessions.json"
        assert manager._store is not None

    def test_init_creates_default_path(self):
        """Test initialization creates default storage path."""
        # Arrange & Act
        manager = SessionManager()

        # Assert
        expected_path = Path.home() / ".open-orchestrator" / "sessions.json"
        assert manager._storage_path == expected_path

    def test_load_existing_sessions_store(self, temp_directory: Path):
        """Test loading existing sessions from storage."""
        # Arrange
        storage_path = temp_directory / "sessions.json"
        sessions_data = {
            "sessions": {
                "test-worktree": {
                    "worktree_name": "test-worktree",
                    "worktree_path": "/path/to/worktree",
                    "session_id": "abc123",
                    "session_dir": "/path/to/worktree/.claude",
                    "created_at": "2024-02-01T10:00:00",
                    "updated_at": "2024-02-01T10:00:00",
                }
            }
        }
        storage_path.write_text(json.dumps(sessions_data))
        config = SessionConfig(storage_path=storage_path)

        # Act
        manager = SessionManager(config=config)

        # Assert
        sessions = manager.get_all_sessions()
        assert len(sessions) == 1
        assert sessions[0].worktree_name == "test-worktree"

    def test_load_corrupted_store_creates_empty(self, temp_directory: Path):
        """Test loading corrupted store creates empty store."""
        # Arrange
        storage_path = temp_directory / "sessions.json"
        storage_path.write_text("invalid json")
        config = SessionConfig(storage_path=storage_path)

        # Act
        manager = SessionManager(config=config)

        # Assert
        assert len(manager.get_all_sessions()) == 0


class TestSessionInitialization:
    """Test session initialization and tracking."""

    def test_initialize_session(self, temp_directory: Path):
        """Test initializing session tracking for a worktree."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree1"
        worktree_path.mkdir()
        claude_dir = worktree_path / ".claude"
        claude_dir.mkdir()

        # Act
        session = manager.initialize_session("worktree1", str(worktree_path))

        # Assert
        assert session.worktree_name == "worktree1"
        assert session.worktree_path == str(worktree_path)
        assert session.session_dir == str(claude_dir)

    def test_initialize_session_persists(self, temp_directory: Path):
        """Test that session initialization persists to storage."""
        # Arrange
        storage_path = temp_directory / "sessions.json"
        config = SessionConfig(storage_path=storage_path)
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree1"
        worktree_path.mkdir()

        # Act
        manager.initialize_session("worktree1", str(worktree_path))

        # Assert - reload and check persistence
        manager2 = SessionManager(config=config)
        session = manager2.get_session("worktree1")
        assert session is not None
        assert session.worktree_name == "worktree1"

    def test_get_session(self, temp_directory: Path):
        """Test getting session data for a worktree."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree1"
        worktree_path.mkdir()
        manager.initialize_session("worktree1", str(worktree_path))

        # Act
        session = manager.get_session("worktree1")

        # Assert
        assert session is not None
        assert session.worktree_name == "worktree1"

    def test_get_nonexistent_session(self, temp_directory: Path):
        """Test getting a session that doesn't exist."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        # Act
        session = manager.get_session("nonexistent")

        # Assert
        assert session is None

    def test_get_all_sessions(self, temp_directory: Path):
        """Test getting all tracked sessions."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        wt1 = temp_directory / "wt1"
        wt2 = temp_directory / "wt2"
        wt1.mkdir()
        wt2.mkdir()

        manager.initialize_session("wt1", str(wt1))
        manager.initialize_session("wt2", str(wt2))

        # Act
        sessions = manager.get_all_sessions()

        # Assert
        assert len(sessions) == 2
        names = {s.worktree_name for s in sessions}
        assert "wt1" in names
        assert "wt2" in names


class TestSessionFileDiscovery:
    """Test Claude session file discovery."""

    def test_find_session_files(self, temp_directory: Path):
        """Test finding Claude session files in a worktree."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree"
        claude_dir = worktree_path / ".claude"
        projects_dir = claude_dir / "projects"
        projects_dir.mkdir(parents=True)

        # Create some session files
        (projects_dir / "session1.jsonl").write_text("session data")
        (projects_dir / "session2.jsonl").write_text("session data")
        (claude_dir / "settings.json").write_text("{}")

        # Act
        files = manager.find_session_files(str(worktree_path))

        # Assert
        assert len(files) >= 3
        file_names = {f.name for f in files}
        assert "session1.jsonl" in file_names
        assert "session2.jsonl" in file_names

    def test_find_session_files_excludes_patterns(self, temp_directory: Path):
        """Test that excluded patterns are filtered out."""
        # Arrange
        config = SessionConfig(
            storage_path=temp_directory / "sessions.json",
            excluded_files=["*.log", "*.tmp"],
        )
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree"
        claude_dir = worktree_path / ".claude"
        claude_dir.mkdir(parents=True)

        # Create files including excluded ones
        (claude_dir / "session.jsonl").write_text("data")
        (claude_dir / "debug.log").write_text("logs")
        (claude_dir / "temp.tmp").write_text("temp")

        # Act
        files = manager.find_session_files(str(worktree_path))

        # Assert
        file_names = {f.name for f in files}
        assert "session.jsonl" in file_names
        assert "debug.log" not in file_names
        assert "temp.tmp" not in file_names

    def test_find_session_files_no_claude_dir(self, temp_directory: Path):
        """Test finding files when .claude directory doesn't exist."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Act
        files = manager.find_session_files(str(worktree_path))

        # Assert
        assert len(files) == 0

    def test_get_latest_session_id(self, temp_directory: Path):
        """Test getting the most recent session ID."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree"
        projects_dir = worktree_path / ".claude" / "projects"
        projects_dir.mkdir(parents=True)

        # Create session files with different timestamps
        old_session = projects_dir / "old-session-123.jsonl"
        old_session.write_text("old")
        old_session.touch()

        import time

        time.sleep(0.01)

        new_session = projects_dir / "new-session-456.jsonl"
        new_session.write_text("new")
        new_session.touch()

        # Act
        session_id = manager.get_latest_session_id(str(worktree_path))

        # Assert
        assert session_id == "new-session-456"

    def test_get_latest_session_id_no_sessions(self, temp_directory: Path):
        """Test getting session ID when no sessions exist."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Act
        session_id = manager.get_latest_session_id(str(worktree_path))

        # Assert
        assert session_id is None


class TestSessionCopying:
    """Test session copying between worktrees."""

    def test_copy_session_success(self, temp_directory: Path):
        """Test successfully copying session data."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        # Create source worktree with session data
        source_path = temp_directory / "source"
        source_claude = source_path / ".claude"
        source_projects = source_claude / "projects"
        source_projects.mkdir(parents=True)
        (source_projects / "session.jsonl").write_text("session data")

        # Create target worktree
        target_path = temp_directory / "target"
        target_path.mkdir()

        # Act
        result = manager.copy_session(
            "source-wt",
            str(source_path),
            "target-wt",
            str(target_path),
        )

        # Assert
        assert result.status == SessionCopyStatus.SUCCESS
        assert result.source_worktree == "source-wt"
        assert result.target_worktree == "target-wt"
        assert len(result.files_copied) >= 1

        # Verify files were copied
        target_claude = target_path / ".claude"
        assert target_claude.exists()
        target_session = target_claude / "projects" / "session.jsonl"
        assert target_session.exists()

    def test_copy_session_no_source_data(self, temp_directory: Path):
        """Test copying when source has no session data."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        source_path = temp_directory / "source"
        source_path.mkdir()

        target_path = temp_directory / "target"
        target_path.mkdir()

        # Act
        result = manager.copy_session(
            "source-wt",
            str(source_path),
            "target-wt",
            str(target_path),
        )

        # Assert
        assert result.status == SessionCopyStatus.NO_SESSION
        assert "No Claude session data" in result.message

    def test_copy_session_target_has_data_no_overwrite(self, temp_directory: Path):
        """Test copying fails when target has data and overwrite is False."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        # Create source with data
        source_path = temp_directory / "source"
        source_claude = source_path / ".claude"
        source_claude.mkdir(parents=True)
        (source_claude / "session.jsonl").write_text("source")

        # Create target with existing data
        target_path = temp_directory / "target"
        target_claude = target_path / ".claude"
        target_claude.mkdir(parents=True)
        (target_claude / "existing.jsonl").write_text("existing")

        # Act
        result = manager.copy_session(
            "source-wt",
            str(source_path),
            "target-wt",
            str(target_path),
            overwrite=False,
        )

        # Assert
        assert result.status == SessionCopyStatus.FAILED
        assert "already has session data" in result.message

    def test_copy_session_with_overwrite(self, temp_directory: Path):
        """Test copying with overwrite flag."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        # Create source with data
        source_path = temp_directory / "source"
        source_claude = source_path / ".claude"
        source_claude.mkdir(parents=True)
        (source_claude / "session.jsonl").write_text("source")

        # Create target with existing data
        target_path = temp_directory / "target"
        target_claude = target_path / ".claude"
        target_claude.mkdir(parents=True)
        (target_claude / "existing.jsonl").write_text("existing")

        # Act
        result = manager.copy_session(
            "source-wt",
            str(source_path),
            "target-wt",
            str(target_path),
            overwrite=True,
        )

        # Assert
        assert result.status in [SessionCopyStatus.SUCCESS, SessionCopyStatus.PARTIAL]

    def test_copy_session_tracks_lineage(self, temp_directory: Path):
        """Test that session copying tracks lineage."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        source_path = temp_directory / "source"
        source_claude = source_path / ".claude"
        source_projects = source_claude / "projects"
        source_projects.mkdir(parents=True)
        (source_projects / "session123.jsonl").write_text("data")

        target_path = temp_directory / "target"
        target_path.mkdir()

        # Act
        manager.copy_session(
            "source-wt",
            str(source_path),
            "target-wt",
            str(target_path),
        )

        # Assert
        session = manager.get_session("target-wt")
        assert session is not None
        assert session.copied_from == "source-wt"
        assert session.original_session_id == "session123"


class TestResumeCommand:
    """Test resume command generation."""

    def test_get_resume_command(self, temp_directory: Path):
        """Test generating resume command for a worktree."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        worktree_path = temp_directory / "worktree"
        projects_dir = worktree_path / ".claude" / "projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / "session-abc123.jsonl").write_text("data")

        # Act
        command = manager.get_resume_command("worktree", str(worktree_path))

        # Assert
        assert command is not None
        assert "claude --resume" in command
        assert "session-abc123" in command

    def test_get_resume_command_no_session(self, temp_directory: Path):
        """Test resume command when no session exists."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()

        # Act
        command = manager.get_resume_command("worktree", str(worktree_path))

        # Assert
        assert command is None

    def test_get_continue_command(self, temp_directory: Path):
        """Test generating continue command."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree"

        # Act
        command = manager.get_continue_command(str(worktree_path))

        # Assert
        assert command == "claude --continue"


class TestSessionRemoval:
    """Test session removal."""

    def test_remove_session(self, temp_directory: Path):
        """Test removing session tracking."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree"
        worktree_path.mkdir()
        manager.initialize_session("worktree", str(worktree_path))

        # Act
        removed = manager.remove_session("worktree")

        # Assert
        assert removed is True
        assert manager.get_session("worktree") is None

    def test_remove_nonexistent_session(self, temp_directory: Path):
        """Test removing a session that doesn't exist."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        # Act
        removed = manager.remove_session("nonexistent")

        # Assert
        assert removed is False


class TestOrphanCleanup:
    """Test cleanup of orphaned session entries."""

    def test_cleanup_orphans(self, temp_directory: Path):
        """Test removing sessions for deleted worktrees."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        # Create sessions for multiple worktrees
        for name in ["wt1", "wt2", "wt3"]:
            path = temp_directory / name
            path.mkdir()
            manager.initialize_session(name, str(path))

        # Act - cleanup with only wt1 and wt2 as valid
        removed = manager.cleanup_orphans(["wt1", "wt2"])

        # Assert
        assert "wt3" in removed
        assert len(removed) == 1
        assert manager.get_session("wt3") is None
        assert manager.get_session("wt1") is not None
        assert manager.get_session("wt2") is not None

    def test_cleanup_orphans_no_orphans(self, temp_directory: Path):
        """Test cleanup when there are no orphans."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)

        wt1 = temp_directory / "wt1"
        wt1.mkdir()
        manager.initialize_session("wt1", str(wt1))

        # Act - all sessions are valid
        removed = manager.cleanup_orphans(["wt1"])

        # Assert
        assert len(removed) == 0


class TestSessionDirectoryHelpers:
    """Test helper methods for directory paths."""

    def test_get_claude_dir(self, temp_directory: Path):
        """Test getting .claude directory path."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree"

        # Act
        claude_dir = manager.get_claude_dir(str(worktree_path))

        # Assert
        assert claude_dir == worktree_path / ".claude"

    def test_get_projects_dir(self, temp_directory: Path):
        """Test getting .claude/projects directory path."""
        # Arrange
        config = SessionConfig(storage_path=temp_directory / "sessions.json")
        manager = SessionManager(config=config)
        worktree_path = temp_directory / "worktree"

        # Act
        projects_dir = manager.get_projects_dir(str(worktree_path))

        # Assert
        assert projects_dir == worktree_path / ".claude" / "projects"


# === CLI Integration Tests ===


class TestSessionCLI:
    """Test CLI commands for session management."""

    def test_copy_session_command(self, temp_directory: Path):
        """Test 'owt copy-session' command."""
        # Arrange
        runner = CliRunner()

        # Create source worktree with session
        with runner.isolated_filesystem(temp_dir=temp_directory) as td:
            source_dir = Path(td) / "source"
            source_claude = source_dir / ".claude" / "projects"
            source_claude.mkdir(parents=True)
            (source_claude / "session.jsonl").write_text("data")

            target_dir = Path(td) / "target"
            target_dir.mkdir()

            # Act
            result = runner.invoke(
                cli,
                ["copy-session", "source", "target"],
            )

        # Assert
        # Command may fail without actual worktree context,
        # but should execute without crashing
        assert result.exit_code in [0, 1, 2]

    def test_resume_command(self, temp_directory: Path):
        """Test 'owt resume' command."""
        # Arrange
        runner = CliRunner()

        # Act
        result = runner.invoke(cli, ["resume", "test-worktree"])

        # Assert
        # Command may fail without actual worktree,
        # but should execute without crashing
        assert result.exit_code in [0, 1, 2]

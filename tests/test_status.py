"""
Tests for StatusTracker class and AI activity status tracking (SQLite backend).
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from open_orchestrator.config import AITool
from open_orchestrator.core.status import StatusConfig, StatusTracker
from open_orchestrator.models.status import AIActivityStatus


@pytest.fixture
def status_file(temp_directory: Path) -> Path:
    """Create a temporary status DB path."""
    db_path = temp_directory / ".open-orchestrator" / "status.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


@pytest.fixture
def status_config(status_file: Path) -> StatusConfig:
    """Create a StatusConfig for testing."""
    return StatusConfig(storage_path=status_file)


@pytest.fixture
def status_tracker(status_config: StatusConfig) -> StatusTracker:
    """Create a StatusTracker instance for testing."""
    return StatusTracker(status_config)


class TestStatusConfig:
    """Test StatusConfig dataclass."""

    def test_default_values(self) -> None:
        """Test StatusConfig default values."""
        config = StatusConfig()
        assert config.storage_path is None


class TestStatusTrackerInit:
    """Test StatusTracker initialization."""

    def test_init_with_config(self, status_config: StatusConfig) -> None:
        """Test StatusTracker initialization with config."""
        tracker = StatusTracker(status_config)
        assert tracker.config == status_config
        assert tracker._storage_path == status_config.storage_path

    def test_init_without_config(self) -> None:
        """Test StatusTracker initialization without config uses defaults."""
        tracker = StatusTracker()
        default_path = Path.home() / ".open-orchestrator" / "status.db"
        assert tracker._storage_path == default_path

    def test_migrate_existing_json(self, status_file: Path, status_config: StatusConfig) -> None:
        """Test migrating an existing JSON status store into SQLite."""
        json_path = status_file.parent / "ai_status.json"
        existing_data = {
            "statuses": {
                "test-worktree": {
                    "worktree_name": "test-worktree",
                    "worktree_path": "/path/to/worktree",
                    "branch": "feature/test",
                    "tmux_session": "owt-test",
                    "ai_tool": "claude",
                    "activity_status": "working",
                    "current_task": "Testing",
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                    "notes": None,
                }
            }
        }
        json_path.write_text(json.dumps(existing_data))

        tracker = StatusTracker(status_config)
        status = tracker.get_status("test-worktree")
        assert status is not None
        assert status.worktree_name == "test-worktree"
        assert status.activity_status == AIActivityStatus.WORKING

        # JSON should be renamed to .bak
        assert not json_path.exists()
        assert json_path.with_suffix(".json.bak").exists()


class TestInitializeStatus:
    """Test initializing status for a new worktree."""

    def test_initialize_status_basic(self, status_tracker: StatusTracker) -> None:
        """Test initializing status for a new worktree."""
        status = status_tracker.initialize_status(
            worktree_name="new-worktree",
            worktree_path="/path/to/new-worktree",
            branch="feature/new",
            tmux_session="owt-new-worktree",
        )
        assert status.worktree_name == "new-worktree"
        assert status.worktree_path == "/path/to/new-worktree"
        assert status.branch == "feature/new"
        assert status.tmux_session == "owt-new-worktree"
        assert status.ai_tool == "claude"
        assert status.activity_status == AIActivityStatus.IDLE

    def test_initialize_status_with_ai_tool(self, status_tracker: StatusTracker) -> None:
        """Test initializing status with specific AI tool."""
        status = status_tracker.initialize_status(
            worktree_name="droid-worktree",
            worktree_path="/path/to/droid-worktree",
            branch="feature/droid",
            ai_tool=AITool.DROID,
        )
        assert status.ai_tool == "droid"

    def test_initialize_status_persists(self, status_tracker: StatusTracker, status_file: Path) -> None:
        """Test initialized status is persisted to SQLite."""
        status_tracker.initialize_status(
            worktree_name="persist-test",
            worktree_path="/path/to/persist",
            branch="feature/persist",
        )
        assert status_file.exists()
        # Verify directly in SQLite
        conn = sqlite3.connect(str(status_file))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM worktree_status WHERE worktree_name = ?", ("persist-test",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["branch"] == "feature/persist"


class TestUpdateTask:
    """Test updating task status for a worktree."""

    def test_update_task_success(self, status_tracker: StatusTracker) -> None:
        """Test successfully updating task for a worktree."""
        status_tracker.initialize_status(
            worktree_name="task-worktree",
            worktree_path="/path/to/task-worktree",
            branch="feature/task",
        )
        updated = status_tracker.update_task(
            worktree_name="task-worktree",
            task="Implementing new feature",
            status=AIActivityStatus.WORKING,
        )
        assert updated is not None
        assert updated.current_task == "Implementing new feature"
        assert updated.activity_status == AIActivityStatus.WORKING

    def test_update_task_nonexistent_worktree(self, status_tracker: StatusTracker) -> None:
        """Test updating task for nonexistent worktree returns None."""
        result = status_tracker.update_task(worktree_name="nonexistent", task="Some task")
        assert result is None


class TestRecordCommand:
    """Test recording commands sent to worktrees."""

    def test_record_command_updates_activity_status(self, status_tracker: StatusTracker) -> None:
        """Test recording a command updates activity status from IDLE to WORKING."""
        status_tracker.initialize_status(
            worktree_name="activity-test",
            worktree_path="/path/to/activity",
            branch="feature/activity",
        )
        status = status_tracker.get_status("activity-test")
        assert status is not None
        assert status.activity_status == AIActivityStatus.IDLE

        status_tracker.record_command(target_worktree="activity-test", command="echo test")

        updated = status_tracker.get_status("activity-test")
        assert updated is not None
        assert updated.activity_status == AIActivityStatus.WORKING


class TestGetSummary:
    """Test generating status summaries."""

    def test_get_summary_empty(self, status_tracker: StatusTracker) -> None:
        """Test summary with no tracked worktrees."""
        summary = status_tracker.get_summary()
        assert summary.total_worktrees == 0
        assert summary.worktrees_with_status == 0
        assert summary.active_ai_sessions == 0

    def test_get_summary_with_statuses(self, status_tracker: StatusTracker) -> None:
        """Test summary with multiple worktree statuses."""
        status_tracker.initialize_status("worktree-1", "/path/1", "branch-1")
        status_tracker.update_task("worktree-1", "Task 1", AIActivityStatus.WORKING)

        status_tracker.initialize_status("worktree-2", "/path/2", "branch-2")

        status_tracker.initialize_status("worktree-3", "/path/3", "branch-3")
        status_tracker.update_task("worktree-3", "Task 3", AIActivityStatus.BLOCKED)

        summary = status_tracker.get_summary()
        assert summary.total_worktrees == 3
        assert summary.active_ai_sessions == 1
        assert summary.idle_ai_sessions == 1
        assert summary.blocked_ai_sessions == 1

    def test_get_summary_filtered(self, status_tracker: StatusTracker) -> None:
        """Test summary filtered by worktree names."""
        status_tracker.initialize_status("worktree-1", "/path/1", "branch-1")
        status_tracker.initialize_status("worktree-2", "/path/2", "branch-2")
        status_tracker.initialize_status("worktree-3", "/path/3", "branch-3")

        summary = status_tracker.get_summary(worktree_names=["worktree-1", "worktree-2"])
        assert summary.total_worktrees == 2
        assert summary.worktrees_with_status == 2


class TestMarkStatus:
    """Test marking status as completed or idle."""

    def test_mark_completed(self, status_tracker: StatusTracker) -> None:
        status_tracker.initialize_status("complete-test", "/path/to/complete", "branch")
        status_tracker.update_task("complete-test", "Task", AIActivityStatus.WORKING)

        updated = status_tracker.mark_completed("complete-test")
        assert updated is not None
        assert updated.activity_status == AIActivityStatus.COMPLETED

    def test_mark_idle(self, status_tracker: StatusTracker) -> None:
        status_tracker.initialize_status("idle-test", "/path/to/idle", "branch")
        status_tracker.update_task("idle-test", "Task", AIActivityStatus.WORKING)

        updated = status_tracker.mark_idle("idle-test")
        assert updated is not None
        assert updated.activity_status == AIActivityStatus.IDLE


class TestRemoveStatus:
    """Test removing status entries."""

    def test_remove_status_success(self, status_tracker: StatusTracker) -> None:
        status_tracker.initialize_status("remove-test", "/path/to/remove", "branch")
        assert status_tracker.get_status("remove-test") is not None

        result = status_tracker.remove_status("remove-test")
        assert result is True
        assert status_tracker.get_status("remove-test") is None

    def test_remove_status_nonexistent(self, status_tracker: StatusTracker) -> None:
        result = status_tracker.remove_status("nonexistent")
        assert result is False


class TestCleanupOrphans:
    """Test cleanup of orphaned status entries."""

    def test_cleanup_orphans_removes_invalid(self, status_tracker: StatusTracker) -> None:
        status_tracker.initialize_status("worktree-1", "/path/1", "branch-1")
        status_tracker.initialize_status("worktree-2", "/path/2", "branch-2")
        status_tracker.initialize_status("worktree-3", "/path/3", "branch-3")

        removed = status_tracker.cleanup_orphans(["worktree-1", "worktree-3"])
        assert "worktree-2" in removed
        assert status_tracker.get_status("worktree-1") is not None
        assert status_tracker.get_status("worktree-2") is None


class TestSetNotes:
    """Test setting notes for worktrees."""

    def test_set_notes_success(self, status_tracker: StatusTracker) -> None:
        status_tracker.initialize_status("notes-test", "/path/to/notes", "branch")
        updated = status_tracker.set_notes("notes-test", "Some important notes")
        assert updated is not None
        assert updated.notes == "Some important notes"

    def test_set_notes_nonexistent(self, status_tracker: StatusTracker) -> None:
        result = status_tracker.set_notes("nonexistent", "Notes")
        assert result is None


class TestSQLitePersistence:
    """Test SQLite persistence and permissions."""

    def test_status_file_created_with_permissions(self, status_tracker: StatusTracker, status_file: Path) -> None:
        status_tracker.initialize_status("perm-test", "/path/to/perm", "branch")
        assert status_file.exists()
        import stat

        mode = status_file.stat().st_mode
        permissions = stat.S_IMODE(mode)
        assert permissions == 0o600

    def test_status_survives_tracker_reload(self, status_file: Path, status_config: StatusConfig) -> None:
        tracker1 = StatusTracker(status_config)
        tracker1.initialize_status("reload-test", "/path/to/reload", "branch")
        tracker1.update_task("reload-test", "Task 1", AIActivityStatus.WORKING)
        tracker1.close()

        tracker2 = StatusTracker(status_config)
        status = tracker2.get_status("reload-test")
        assert status is not None
        assert status.current_task == "Task 1"
        assert status.activity_status == AIActivityStatus.WORKING

    def test_wal_mode_enabled(self, status_file: Path, status_config: StatusConfig) -> None:
        """Verify WAL journal mode is active."""
        tracker = StatusTracker(status_config)
        tracker.initialize_status("wal-test", "/path/wal", "branch")
        row = tracker._conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"


class TestSharedNotes:
    """Test shared notes functionality."""

    def test_add_and_get_shared_notes(self, status_tracker: StatusTracker) -> None:
        status_tracker.add_shared_note("Note 1")
        status_tracker.add_shared_note("Note 2")
        notes = status_tracker.get_shared_notes()
        assert notes == ["Note 1", "Note 2"]

    def test_clear_shared_notes(self, status_tracker: StatusTracker) -> None:
        status_tracker.add_shared_note("To be cleared")
        status_tracker.clear_shared_notes()
        assert status_tracker.get_shared_notes() == []


class TestPeerMessages:
    """Tests for peer messaging methods."""

    def test_store_message(self, status_tracker: StatusTracker) -> None:
        msg_id = status_tracker.store_message("agent-a", "agent-b", "hello")
        assert msg_id > 0

    def test_get_unread_messages(self, status_tracker: StatusTracker) -> None:
        status_tracker.store_message("agent-a", "agent-b", "msg 1")
        status_tracker.store_message("agent-c", "agent-b", "msg 2")
        status_tracker.store_message("agent-a", "agent-x", "not for b")

        msgs = status_tracker.get_unread_messages("agent-b")
        assert len(msgs) == 2
        assert msgs[0]["from_peer"] == "agent-a"
        assert msgs[0]["message"] == "msg 1"
        assert msgs[1]["from_peer"] == "agent-c"

    def test_no_unread_returns_empty(self, status_tracker: StatusTracker) -> None:
        assert status_tracker.get_unread_messages("nobody") == []

    def test_mark_messages_read(self, status_tracker: StatusTracker) -> None:
        id1 = status_tracker.store_message("a", "b", "first")
        id2 = status_tracker.store_message("a", "b", "second")

        status_tracker.mark_messages_read([id1])

        unread = status_tracker.get_unread_messages("b")
        assert len(unread) == 1
        assert unread[0]["id"] == id2

    def test_mark_read_empty_list(self, status_tracker: StatusTracker) -> None:
        status_tracker.mark_messages_read([])  # should not raise

    def test_mark_read_idempotent(self, status_tracker: StatusTracker) -> None:
        msg_id = status_tracker.store_message("a", "b", "test")
        status_tracker.mark_messages_read([msg_id])
        status_tracker.mark_messages_read([msg_id])  # no error
        assert status_tracker.get_unread_messages("b") == []

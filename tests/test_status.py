"""
Tests for StatusTracker class and AI activity status tracking.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from open_orchestrator.config import AITool
from open_orchestrator.core.status import StatusConfig, StatusTracker
from open_orchestrator.models.status import AIActivityStatus, TokenUsage


@pytest.fixture
def status_file(temp_directory: Path) -> Path:
    """Create a temporary status file path."""
    status_path = temp_directory / ".open-orchestrator" / "ai_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    return status_path


@pytest.fixture
def status_config(status_file: Path) -> StatusConfig:
    """Create a StatusConfig for testing."""
    return StatusConfig(
        storage_path=status_file,
        max_command_history=20,
        auto_cleanup_orphans=True,
        store_commands=True,
        redact_commands=True,
    )


@pytest.fixture
def status_tracker(status_config: StatusConfig) -> StatusTracker:
    """Create a StatusTracker instance for testing."""
    return StatusTracker(status_config)


class TestStatusConfig:
    """Test StatusConfig dataclass."""

    def test_default_values(self) -> None:
        """Test StatusConfig default values."""
        # Act
        config = StatusConfig()

        # Assert
        assert config.storage_path is None
        assert config.max_command_history == 20
        assert config.auto_cleanup_orphans is True
        assert config.store_commands is True
        assert config.redact_commands is True

    def test_custom_values(self, temp_directory: Path) -> None:
        """Test StatusConfig with custom values."""
        # Arrange
        custom_path = temp_directory / "custom_status.json"

        # Act
        config = StatusConfig(
            storage_path=custom_path,
            max_command_history=10,
            auto_cleanup_orphans=False,
            store_commands=False,
            redact_commands=False,
        )

        # Assert
        assert config.storage_path == custom_path
        assert config.max_command_history == 10
        assert config.auto_cleanup_orphans is False
        assert config.store_commands is False
        assert config.redact_commands is False

    def test_invalid_max_command_history(self) -> None:
        """Test StatusConfig raises error for invalid max_command_history."""
        # Act & Assert
        with pytest.raises(ValueError, match="must be at least 1"):
            StatusConfig(max_command_history=0)


class TestStatusTrackerInit:
    """Test StatusTracker initialization."""

    def test_init_with_config(self, status_config: StatusConfig) -> None:
        """Test StatusTracker initialization with config."""
        # Act
        tracker = StatusTracker(status_config)

        # Assert
        assert tracker.config == status_config
        assert tracker._storage_path == status_config.storage_path
        assert tracker._store is not None

    def test_init_without_config(self) -> None:
        """Test StatusTracker initialization without config uses defaults."""
        # Act
        tracker = StatusTracker()

        # Assert
        assert tracker.config is not None
        default_path = Path.home() / ".open-orchestrator" / "ai_status.json"
        assert tracker._storage_path == default_path

    def test_load_existing_store(self, status_file: Path, status_config: StatusConfig) -> None:
        """Test loading an existing status store from file."""
        # Arrange
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
                    "recent_commands": [],
                    "notes": None,
                }
            }
        }
        status_file.write_text(json.dumps(existing_data))

        # Act
        tracker = StatusTracker(status_config)

        # Assert
        status = tracker.get_status("test-worktree")
        assert status is not None
        assert status.worktree_name == "test-worktree"
        assert status.activity_status == AIActivityStatus.WORKING


class TestInitializeStatus:
    """Test initializing status for a new worktree."""

    def test_initialize_status_basic(self, status_tracker: StatusTracker) -> None:
        """Test initializing status for a new worktree."""
        # Act
        status = status_tracker.initialize_status(
            worktree_name="new-worktree",
            worktree_path="/path/to/new-worktree",
            branch="feature/new",
            tmux_session="owt-new-worktree",
        )

        # Assert
        assert status.worktree_name == "new-worktree"
        assert status.worktree_path == "/path/to/new-worktree"
        assert status.branch == "feature/new"
        assert status.tmux_session == "owt-new-worktree"
        assert status.ai_tool == "claude"
        assert status.activity_status == AIActivityStatus.IDLE

    def test_initialize_status_with_ai_tool(self, status_tracker: StatusTracker) -> None:
        """Test initializing status with specific AI tool."""
        # Act
        status = status_tracker.initialize_status(
            worktree_name="droid-worktree",
            worktree_path="/path/to/droid-worktree",
            branch="feature/droid",
            ai_tool=AITool.DROID,
        )

        # Assert
        assert status.ai_tool == "droid"

    def test_initialize_status_persists(
        self, status_tracker: StatusTracker, status_file: Path
    ) -> None:
        """Test initialized status is persisted to storage."""
        # Act
        status_tracker.initialize_status(
            worktree_name="persist-test",
            worktree_path="/path/to/persist",
            branch="feature/persist",
        )

        # Assert
        assert status_file.exists()
        data = json.loads(status_file.read_text())
        assert "persist-test" in data["statuses"]


class TestUpdateTask:
    """Test updating task status for a worktree."""

    def test_update_task_success(self, status_tracker: StatusTracker) -> None:
        """Test successfully updating task for a worktree."""
        # Arrange
        status_tracker.initialize_status(
            worktree_name="task-worktree",
            worktree_path="/path/to/task-worktree",
            branch="feature/task",
        )

        # Act
        updated = status_tracker.update_task(
            worktree_name="task-worktree",
            task="Implementing new feature",
            status=AIActivityStatus.WORKING,
        )

        # Assert
        assert updated is not None
        assert updated.current_task == "Implementing new feature"
        assert updated.activity_status == AIActivityStatus.WORKING

    def test_update_task_nonexistent_worktree(self, status_tracker: StatusTracker) -> None:
        """Test updating task for nonexistent worktree returns None."""
        # Act
        result = status_tracker.update_task(
            worktree_name="nonexistent",
            task="Some task",
        )

        # Assert
        assert result is None


class TestRecordCommand:
    """Test recording commands sent to worktrees."""

    def test_record_command_success(self, status_tracker: StatusTracker) -> None:
        """Test successfully recording a command."""
        # Arrange
        status_tracker.initialize_status(
            worktree_name="cmd-worktree",
            worktree_path="/path/to/cmd-worktree",
            branch="feature/cmd",
        )

        # Act
        updated = status_tracker.record_command(
            target_worktree="cmd-worktree",
            command="echo hello",
            source_worktree="main-worktree",
        )

        # Assert
        assert updated is not None
        assert len(updated.recent_commands) == 1
        assert updated.recent_commands[0].command == "echo hello"
        assert updated.recent_commands[0].source_worktree == "main-worktree"

    def test_record_command_with_redaction(self, status_tracker: StatusTracker) -> None:
        """Test command recording with secret redaction."""
        # Arrange
        status_tracker.initialize_status(
            worktree_name="secret-worktree",
            worktree_path="/path/to/secret",
            branch="feature/secret",
        )

        # Act
        updated = status_tracker.record_command(
            target_worktree="secret-worktree",
            command="export API_KEY=sk-1234567890abcdef",
        )

        # Assert
        assert updated is not None
        assert len(updated.recent_commands) == 1
        assert "[REDACTED]" in updated.recent_commands[0].command
        assert "sk-1234567890abcdef" not in updated.recent_commands[0].command

    def test_record_command_without_redaction(self, status_file: Path) -> None:
        """Test command recording without redaction."""
        # Arrange
        config = StatusConfig(storage_path=status_file, redact_commands=False)
        tracker = StatusTracker(config)
        tracker.initialize_status(
            worktree_name="no-redact",
            worktree_path="/path/to/no-redact",
            branch="feature/no-redact",
        )

        # Act
        updated = tracker.record_command(
            target_worktree="no-redact",
            command="export API_KEY=sk-1234567890abcdef",
        )

        # Assert
        assert updated is not None
        assert updated.recent_commands[0].command == "export API_KEY=sk-1234567890abcdef"

    def test_record_command_updates_activity_status(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test recording a command updates activity status from IDLE to WORKING."""
        # Arrange
        status_tracker.initialize_status(
            worktree_name="activity-test",
            worktree_path="/path/to/activity",
            branch="feature/activity",
        )
        # Status should be IDLE initially
        status = status_tracker.get_status("activity-test")
        assert status is not None
        assert status.activity_status == AIActivityStatus.IDLE

        # Act
        status_tracker.record_command(
            target_worktree="activity-test",
            command="echo test",
        )

        # Assert
        updated = status_tracker.get_status("activity-test")
        assert updated is not None
        assert updated.activity_status == AIActivityStatus.WORKING


class TestCommandSanitization:
    """Test command sanitization and secret redaction."""

    def test_sanitize_api_key(self, status_tracker: StatusTracker) -> None:
        """Test API key redaction."""
        # Arrange
        command = "curl -H 'api-key: sk-1234567890' https://api.example.com"

        # Act
        sanitized = status_tracker._sanitize_command(command)

        # Assert
        assert "[REDACTED]" in sanitized
        assert "sk-1234567890" not in sanitized

    def test_sanitize_password(self, status_tracker: StatusTracker) -> None:
        """Test password redaction."""
        # Arrange
        command = "mysql -u user -p password=secret123"

        # Act
        sanitized = status_tracker._sanitize_command(command)

        # Assert
        assert "[REDACTED]" in sanitized
        assert "secret123" not in sanitized

    def test_sanitize_bearer_token(self, status_tracker: StatusTracker) -> None:
        """Test Bearer token redaction."""
        # Arrange
        command = "curl -H 'Authorization: Bearer eyJhbGciOi...'"

        # Act
        sanitized = status_tracker._sanitize_command(command)

        # Assert
        assert "[REDACTED]" in sanitized
        assert "eyJhbGciOi" not in sanitized

    def test_sanitize_url_with_credentials(self, status_tracker: StatusTracker) -> None:
        """Test URL credential redaction."""
        # Arrange
        command = "git clone https://user:password@github.com/repo.git"

        # Act
        sanitized = status_tracker._sanitize_command(command)

        # Assert
        assert "[REDACTED]" in sanitized
        assert "user:password" not in sanitized


class TestGetSummary:
    """Test generating status summaries."""

    def test_get_summary_empty(self, status_tracker: StatusTracker) -> None:
        """Test summary with no tracked worktrees."""
        # Act
        summary = status_tracker.get_summary()

        # Assert
        assert summary.total_worktrees == 0
        assert summary.worktrees_with_status == 0
        assert summary.active_ai_sessions == 0
        assert summary.idle_ai_sessions == 0
        assert summary.blocked_ai_sessions == 0

    def test_get_summary_with_statuses(self, status_tracker: StatusTracker) -> None:
        """Test summary with multiple worktree statuses."""
        # Arrange
        status_tracker.initialize_status(
            "worktree-1", "/path/1", "branch-1"
        )
        status_tracker.update_task("worktree-1", "Task 1", AIActivityStatus.WORKING)

        status_tracker.initialize_status(
            "worktree-2", "/path/2", "branch-2"
        )
        # worktree-2 remains IDLE

        status_tracker.initialize_status(
            "worktree-3", "/path/3", "branch-3"
        )
        status_tracker.update_task("worktree-3", "Task 3", AIActivityStatus.BLOCKED)

        # Act
        summary = status_tracker.get_summary()

        # Assert
        assert summary.total_worktrees == 3
        assert summary.worktrees_with_status == 3
        assert summary.active_ai_sessions == 1
        assert summary.idle_ai_sessions == 1
        assert summary.blocked_ai_sessions == 1

    def test_get_summary_filtered(self, status_tracker: StatusTracker) -> None:
        """Test summary filtered by worktree names."""
        # Arrange
        status_tracker.initialize_status("worktree-1", "/path/1", "branch-1")
        status_tracker.initialize_status("worktree-2", "/path/2", "branch-2")
        status_tracker.initialize_status("worktree-3", "/path/3", "branch-3")

        # Act
        summary = status_tracker.get_summary(worktree_names=["worktree-1", "worktree-2"])

        # Assert
        assert summary.total_worktrees == 2
        assert summary.worktrees_with_status == 2


class TestMarkStatus:
    """Test marking status as completed or idle."""

    def test_mark_completed(self, status_tracker: StatusTracker) -> None:
        """Test marking a worktree task as completed."""
        # Arrange
        status_tracker.initialize_status(
            "complete-test", "/path/to/complete", "branch"
        )
        status_tracker.update_task("complete-test", "Task", AIActivityStatus.WORKING)

        # Act
        updated = status_tracker.mark_completed("complete-test")

        # Assert
        assert updated is not None
        assert updated.activity_status == AIActivityStatus.COMPLETED

    def test_mark_idle(self, status_tracker: StatusTracker) -> None:
        """Test marking a worktree as idle."""
        # Arrange
        status_tracker.initialize_status(
            "idle-test", "/path/to/idle", "branch"
        )
        status_tracker.update_task("idle-test", "Task", AIActivityStatus.WORKING)

        # Act
        updated = status_tracker.mark_idle("idle-test")

        # Assert
        assert updated is not None
        assert updated.activity_status == AIActivityStatus.IDLE


class TestRemoveStatus:
    """Test removing status entries."""

    def test_remove_status_success(self, status_tracker: StatusTracker) -> None:
        """Test successfully removing a status entry."""
        # Arrange
        status_tracker.initialize_status(
            "remove-test", "/path/to/remove", "branch"
        )
        assert status_tracker.get_status("remove-test") is not None

        # Act
        result = status_tracker.remove_status("remove-test")

        # Assert
        assert result is True
        assert status_tracker.get_status("remove-test") is None

    def test_remove_status_nonexistent(self, status_tracker: StatusTracker) -> None:
        """Test removing a nonexistent status entry returns False."""
        # Act
        result = status_tracker.remove_status("nonexistent")

        # Assert
        assert result is False


class TestCleanupOrphans:
    """Test cleanup of orphaned status entries."""

    def test_cleanup_orphans_removes_invalid(self, status_tracker: StatusTracker) -> None:
        """Test cleanup removes statuses for worktrees that no longer exist."""
        # Arrange
        status_tracker.initialize_status("worktree-1", "/path/1", "branch-1")
        status_tracker.initialize_status("worktree-2", "/path/2", "branch-2")
        status_tracker.initialize_status("worktree-3", "/path/3", "branch-3")

        # Act
        removed = status_tracker.cleanup_orphans(["worktree-1", "worktree-3"])

        # Assert
        assert "worktree-2" in removed
        assert status_tracker.get_status("worktree-1") is not None
        assert status_tracker.get_status("worktree-2") is None
        assert status_tracker.get_status("worktree-3") is not None

    def test_cleanup_orphans_no_removals(self, status_tracker: StatusTracker) -> None:
        """Test cleanup with no orphans to remove."""
        # Arrange
        status_tracker.initialize_status("worktree-1", "/path/1", "branch-1")

        # Act
        removed = status_tracker.cleanup_orphans(["worktree-1"])

        # Assert
        assert removed == []


class TestSetNotes:
    """Test setting notes for worktrees."""

    def test_set_notes_success(self, status_tracker: StatusTracker) -> None:
        """Test successfully setting notes for a worktree."""
        # Arrange
        status_tracker.initialize_status(
            "notes-test", "/path/to/notes", "branch"
        )

        # Act
        updated = status_tracker.set_notes("notes-test", "Some important notes")

        # Assert
        assert updated is not None
        assert updated.notes == "Some important notes"

    def test_set_notes_nonexistent(self, status_tracker: StatusTracker) -> None:
        """Test setting notes for nonexistent worktree returns None."""
        # Act
        result = status_tracker.set_notes("nonexistent", "Notes")

        # Assert
        assert result is None


class TestFilePersistence:
    """Test file persistence and locking."""

    def test_status_file_created_with_permissions(
        self, status_tracker: StatusTracker, status_file: Path
    ) -> None:
        """Test status file is created with secure permissions."""
        # Arrange
        status_tracker.initialize_status(
            "perm-test", "/path/to/perm", "branch"
        )

        # Assert
        assert status_file.exists()
        import stat
        mode = status_file.stat().st_mode
        permissions = stat.S_IMODE(mode)
        assert permissions == 0o600

    def test_status_survives_tracker_reload(
        self, status_file: Path, status_config: StatusConfig
    ) -> None:
        """Test status persists across tracker instances."""
        # Arrange
        tracker1 = StatusTracker(status_config)
        tracker1.initialize_status("reload-test", "/path/to/reload", "branch")
        tracker1.update_task("reload-test", "Task 1", AIActivityStatus.WORKING)

        # Act - Create new tracker instance
        tracker2 = StatusTracker(status_config)
        status = tracker2.get_status("reload-test")

        # Assert
        assert status is not None
        assert status.current_task == "Task 1"
        assert status.activity_status == AIActivityStatus.WORKING


class TestTokenUsage:
    """Test TokenUsage model properties and methods."""

    def test_default_values(self) -> None:
        """Test TokenUsage model defaults to zero for all token counts."""
        # Act
        token_usage = TokenUsage()

        # Assert
        assert token_usage.input_tokens == 0
        assert token_usage.output_tokens == 0
        assert token_usage.cache_read_tokens == 0
        assert token_usage.cache_write_tokens == 0

    def test_total_tokens_property(self) -> None:
        """Test total_tokens computes sum of input and output tokens."""
        # Arrange
        token_usage = TokenUsage(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=100,
            cache_write_tokens=50
        )

        # Act
        total = token_usage.total_tokens

        # Assert
        assert total == 1500  # Only input + output

    def test_estimated_cost_usd_with_zero_tokens(self) -> None:
        """Test estimated_cost_usd returns zero for zero tokens."""
        # Arrange
        token_usage = TokenUsage()

        # Act
        cost = token_usage.estimated_cost_usd

        # Assert
        assert cost == 0.0

    def test_estimated_cost_usd_calculation(self) -> None:
        """Test estimated_cost_usd uses correct pricing ($15/1M input, $75/1M output)."""
        # Arrange
        token_usage = TokenUsage(
            input_tokens=1_000_000,  # 1M input tokens
            output_tokens=1_000_000,  # 1M output tokens
        )

        # Act
        cost = token_usage.estimated_cost_usd

        # Assert
        # $15 for 1M input + $75 for 1M output = $90
        assert cost == 90.0

    def test_estimated_cost_usd_partial_tokens(self) -> None:
        """Test estimated_cost_usd calculation with partial million tokens."""
        # Arrange
        token_usage = TokenUsage(
            input_tokens=500_000,  # 0.5M input tokens
            output_tokens=250_000,  # 0.25M output tokens
        )

        # Act
        cost = token_usage.estimated_cost_usd

        # Assert
        # $15 * 0.5 + $75 * 0.25 = $7.5 + $18.75 = $26.25
        assert cost == 26.25

    def test_last_updated_timestamp_set(self) -> None:
        """Test last_updated timestamp is automatically set."""
        # Act
        token_usage = TokenUsage()

        # Assert
        assert token_usage.last_updated is not None
        assert isinstance(token_usage.last_updated, datetime)

    def test_cache_token_fields(self) -> None:
        """Test cache_read_tokens and cache_write_tokens are tracked separately."""
        # Arrange
        token_usage = TokenUsage(
            cache_read_tokens=1000,
            cache_write_tokens=500
        )

        # Act & Assert
        assert token_usage.cache_read_tokens == 1000
        assert token_usage.cache_write_tokens == 500
        # Cache tokens don't affect total_tokens
        assert token_usage.total_tokens == 0


class TestStatusTrackerTokenUsage:
    """Test StatusTracker token usage operations."""

    def test_update_token_usage_adds_to_existing_counts(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test update_token_usage adds tokens to existing counts."""
        # Arrange
        status_tracker.initialize_status(
            "token-test", "/path/to/token-test", "branch"
        )
        status_tracker.update_token_usage(
            "token-test",
            input_tokens=1000,
            output_tokens=500
        )

        # Act
        updated = status_tracker.update_token_usage(
            "token-test",
            input_tokens=500,
            output_tokens=250
        )

        # Assert
        assert updated is not None
        assert updated.token_usage.input_tokens == 1500
        assert updated.token_usage.output_tokens == 750

    def test_update_token_usage_with_cache_tokens(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test update_token_usage handles cache read and write tokens."""
        # Arrange
        status_tracker.initialize_status(
            "cache-test", "/path/to/cache-test", "branch"
        )

        # Act
        updated = status_tracker.update_token_usage(
            "cache-test",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=200,
            cache_write_tokens=100
        )

        # Assert
        assert updated is not None
        assert updated.token_usage.input_tokens == 1000
        assert updated.token_usage.output_tokens == 500
        assert updated.token_usage.cache_read_tokens == 200
        assert updated.token_usage.cache_write_tokens == 100

    def test_update_token_usage_updates_last_updated(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test update_token_usage updates last_updated timestamp."""
        # Arrange
        status_tracker.initialize_status(
            "timestamp-test", "/path/to/timestamp", "branch"
        )
        status = status_tracker.get_status("timestamp-test")
        assert status is not None
        original_timestamp = status.token_usage.last_updated

        # Act
        import time
        time.sleep(0.01)  # Ensure timestamp difference
        updated = status_tracker.update_token_usage(
            "timestamp-test",
            input_tokens=100
        )

        # Assert
        assert updated is not None
        assert updated.token_usage.last_updated > original_timestamp

    def test_update_token_usage_nonexistent_worktree_returns_none(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test update_token_usage for nonexistent worktree returns None."""
        # Act
        result = status_tracker.update_token_usage(
            "nonexistent",
            input_tokens=1000
        )

        # Assert
        assert result is None

    def test_reset_token_usage_clears_all_counts(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test reset_token_usage clears all token counts to zero."""
        # Arrange
        status_tracker.initialize_status(
            "reset-test", "/path/to/reset", "branch"
        )
        status_tracker.update_token_usage(
            "reset-test",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=200,
            cache_write_tokens=100
        )

        # Act
        reset = status_tracker.reset_token_usage("reset-test")

        # Assert
        assert reset is not None
        assert reset.token_usage.input_tokens == 0
        assert reset.token_usage.output_tokens == 0
        assert reset.token_usage.cache_read_tokens == 0
        assert reset.token_usage.cache_write_tokens == 0

    def test_reset_token_usage_nonexistent_worktree_returns_none(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test reset_token_usage for nonexistent worktree returns None."""
        # Act
        result = status_tracker.reset_token_usage("nonexistent")

        # Assert
        assert result is None

    def test_token_usage_defaults_to_zero_on_initialization(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test newly initialized worktree has zero token usage."""
        # Act
        status = status_tracker.initialize_status(
            "new-worktree", "/path/to/new", "branch"
        )

        # Assert
        assert status.token_usage.input_tokens == 0
        assert status.token_usage.output_tokens == 0
        assert status.token_usage.total_tokens == 0
        assert status.token_usage.estimated_cost_usd == 0.0


class TestTokenPersistence:
    """Test token usage persistence across StatusTracker instances."""

    def test_token_usage_survives_tracker_reload(
        self, status_file: Path, status_config: StatusConfig
    ) -> None:
        """Test token usage persists across tracker reloads."""
        # Arrange
        tracker1 = StatusTracker(status_config)
        tracker1.initialize_status("persist-token", "/path/to/persist", "branch")
        tracker1.update_token_usage(
            "persist-token",
            input_tokens=5000,
            output_tokens=2500,
            cache_read_tokens=1000,
            cache_write_tokens=500
        )

        # Act - Create new tracker instance
        tracker2 = StatusTracker(status_config)
        status = tracker2.get_status("persist-token")

        # Assert
        assert status is not None
        assert status.token_usage.input_tokens == 5000
        assert status.token_usage.output_tokens == 2500
        assert status.token_usage.cache_read_tokens == 1000
        assert status.token_usage.cache_write_tokens == 500

    def test_updated_token_counts_persist_to_storage(
        self, status_file: Path, status_config: StatusConfig
    ) -> None:
        """Test updated token counts are saved to storage file."""
        # Arrange
        tracker = StatusTracker(status_config)
        tracker.initialize_status("storage-test", "/path/to/storage", "branch")

        # Act
        tracker.update_token_usage(
            "storage-test",
            input_tokens=10000,
            output_tokens=5000
        )

        # Assert - Verify file contents
        assert status_file.exists()
        data = json.loads(status_file.read_text())
        token_data = data["statuses"]["storage-test"]["token_usage"]
        assert token_data["input_tokens"] == 10000
        assert token_data["output_tokens"] == 5000

    def test_reset_token_usage_persists_to_storage(
        self, status_file: Path, status_config: StatusConfig
    ) -> None:
        """Test reset token usage is persisted to storage."""
        # Arrange
        tracker = StatusTracker(status_config)
        tracker.initialize_status("reset-persist", "/path/to/reset", "branch")
        tracker.update_token_usage(
            "reset-persist",
            input_tokens=10000,
            output_tokens=5000
        )

        # Act
        tracker.reset_token_usage("reset-persist")

        # Assert - Verify file contents
        data = json.loads(status_file.read_text())
        token_data = data["statuses"]["reset-persist"]["token_usage"]
        assert token_data["input_tokens"] == 0
        assert token_data["output_tokens"] == 0


class TestStatusSummaryTokens:
    """Test StatusSummary token aggregation across worktrees."""

    def test_get_summary_aggregates_total_input_tokens(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test get_summary correctly aggregates total_input_tokens across multiple worktrees."""
        # Arrange
        status_tracker.initialize_status("wt-1", "/path/1", "branch-1")
        status_tracker.update_token_usage("wt-1", input_tokens=1000)

        status_tracker.initialize_status("wt-2", "/path/2", "branch-2")
        status_tracker.update_token_usage("wt-2", input_tokens=2000)

        status_tracker.initialize_status("wt-3", "/path/3", "branch-3")
        status_tracker.update_token_usage("wt-3", input_tokens=3000)

        # Act
        summary = status_tracker.get_summary()

        # Assert
        assert summary.total_input_tokens == 6000

    def test_get_summary_aggregates_total_output_tokens(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test get_summary correctly aggregates total_output_tokens across multiple worktrees."""
        # Arrange
        status_tracker.initialize_status("wt-1", "/path/1", "branch-1")
        status_tracker.update_token_usage("wt-1", output_tokens=500)

        status_tracker.initialize_status("wt-2", "/path/2", "branch-2")
        status_tracker.update_token_usage("wt-2", output_tokens=1000)

        status_tracker.initialize_status("wt-3", "/path/3", "branch-3")
        status_tracker.update_token_usage("wt-3", output_tokens=1500)

        # Act
        summary = status_tracker.get_summary()

        # Assert
        assert summary.total_output_tokens == 3000

    def test_get_summary_aggregates_total_estimated_cost(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test get_summary correctly aggregates total_estimated_cost_usd across multiple worktrees."""
        # Arrange
        # wt-1: 1M input + 1M output = $15 + $75 = $90
        status_tracker.initialize_status("wt-1", "/path/1", "branch-1")
        status_tracker.update_token_usage(
            "wt-1",
            input_tokens=1_000_000,
            output_tokens=1_000_000
        )

        # wt-2: 500k input + 500k output = $7.5 + $37.5 = $45
        status_tracker.initialize_status("wt-2", "/path/2", "branch-2")
        status_tracker.update_token_usage(
            "wt-2",
            input_tokens=500_000,
            output_tokens=500_000
        )

        # Act
        summary = status_tracker.get_summary()

        # Assert
        # Total cost should be $90 + $45 = $135
        assert summary.total_estimated_cost_usd == 135.0

    def test_get_summary_with_zero_tokens(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test get_summary with worktrees that have zero token usage."""
        # Arrange
        status_tracker.initialize_status("wt-1", "/path/1", "branch-1")
        status_tracker.initialize_status("wt-2", "/path/2", "branch-2")

        # Act
        summary = status_tracker.get_summary()

        # Assert
        assert summary.total_input_tokens == 0
        assert summary.total_output_tokens == 0
        assert summary.total_estimated_cost_usd == 0.0

    def test_get_summary_filtered_aggregates_only_specified_worktrees(
        self, status_tracker: StatusTracker
    ) -> None:
        """Test get_summary with worktree filter aggregates only specified worktrees."""
        # Arrange
        status_tracker.initialize_status("wt-1", "/path/1", "branch-1")
        status_tracker.update_token_usage("wt-1", input_tokens=1000, output_tokens=500)

        status_tracker.initialize_status("wt-2", "/path/2", "branch-2")
        status_tracker.update_token_usage("wt-2", input_tokens=2000, output_tokens=1000)

        status_tracker.initialize_status("wt-3", "/path/3", "branch-3")
        status_tracker.update_token_usage("wt-3", input_tokens=3000, output_tokens=1500)

        # Act - Only include wt-1 and wt-2
        summary = status_tracker.get_summary(worktree_names=["wt-1", "wt-2"])

        # Assert
        assert summary.total_input_tokens == 3000  # 1000 + 2000 (excludes wt-3)
        assert summary.total_output_tokens == 1500  # 500 + 1000 (excludes wt-3)

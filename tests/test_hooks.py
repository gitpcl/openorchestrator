"""
Tests for hook execution service.

This module tests:
- HookService initialization and configuration
- Hook registration and retrieval
- Hook execution (shell, notification, webhook, log)
- Hook filtering and triggering
- Hook history tracking
- CLI commands (owt hooks list/add/remove)
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from open_orchestrator.cli import main as cli
from open_orchestrator.core.hooks import (
    HooksConfig,
    HookService,
    get_hook_type_for_status,
)
from open_orchestrator.models.hooks import (
    HookAction,
    HookConfig,
    HookType,
)
from open_orchestrator.models.status import AIActivityStatus

# === Unit Tests ===


class TestHookServiceInit:
    """Test HookService initialization."""

    def test_init_with_default_config(self, temp_directory: Path):
        """Test initialization with default configuration."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")

        # Act
        service = HookService(config=config)

        # Assert
        assert service.config == config
        assert service._storage_path == temp_directory / "hooks.json"
        assert service._store is not None

    def test_init_creates_default_path(self):
        """Test initialization creates default storage path."""
        # Arrange & Act
        service = HookService()

        # Assert
        expected_path = Path.home() / ".open-orchestrator" / "hooks.json"
        assert service._storage_path == expected_path

    def test_load_existing_hooks_store(self, temp_directory: Path):
        """Test loading existing hooks from storage."""
        # Arrange
        storage_path = temp_directory / "hooks.json"
        hooks_data = {
            "hooks": {
                "test-hook": {
                    "name": "test-hook",
                    "hook_type": "on_status_changed",
                    "action": "shell_command",
                    "command": "echo test",
                    "enabled": True,
                }
            },
            "history": [],
        }
        storage_path.write_text(json.dumps(hooks_data))
        config = HooksConfig(storage_path=storage_path)

        # Act
        service = HookService(config=config)

        # Assert
        hooks = service.get_all_hooks()
        assert len(hooks) == 1
        assert hooks[0].name == "test-hook"

    def test_load_corrupted_store_creates_empty(self, temp_directory: Path):
        """Test loading corrupted store creates empty store."""
        # Arrange
        storage_path = temp_directory / "hooks.json"
        storage_path.write_text("invalid json")
        config = HooksConfig(storage_path=storage_path)

        # Act
        service = HookService(config=config)

        # Assert
        assert len(service.get_all_hooks()) == 0


class TestHookRegistration:
    """Test hook registration and management."""

    def test_register_new_hook(self, temp_directory: Path):
        """Test registering a new hook."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="test-hook",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.SHELL_COMMAND,
            command="echo 'Status changed'",
        )

        # Act
        service.register_hook(hook)

        # Assert
        retrieved = service.get_hook("test-hook")
        assert retrieved is not None
        assert retrieved.name == "test-hook"
        assert retrieved.command == "echo 'Status changed'"

    def test_register_hook_persists_to_storage(self, temp_directory: Path):
        """Test that hook registration persists to storage."""
        # Arrange
        storage_path = temp_directory / "hooks.json"
        config = HooksConfig(storage_path=storage_path)
        service = HookService(config=config)
        hook = HookConfig(
            name="persistent-hook",
            hook_type=HookType.ON_BLOCKED,
            action=HookAction.NOTIFICATION,
        )

        # Act
        service.register_hook(hook)

        # Assert - reload service and check persistence
        service2 = HookService(config=config)
        retrieved = service2.get_hook("persistent-hook")
        assert retrieved is not None
        assert retrieved.name == "persistent-hook"

    def test_update_existing_hook(self, temp_directory: Path):
        """Test updating an existing hook."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="update-hook",
            hook_type=HookType.ON_ERROR,
            action=HookAction.LOG,
        )
        service.register_hook(hook)

        # Act
        hook.enabled = False
        service.register_hook(hook)

        # Assert
        retrieved = service.get_hook("update-hook")
        assert retrieved.enabled is False

    def test_unregister_hook(self, temp_directory: Path):
        """Test unregistering a hook."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="remove-hook",
            hook_type=HookType.ON_IDLE,
            action=HookAction.SHELL_COMMAND,
            command="echo test",
        )
        service.register_hook(hook)

        # Act
        removed = service.unregister_hook("remove-hook")

        # Assert
        assert removed is True
        assert service.get_hook("remove-hook") is None

    def test_unregister_nonexistent_hook(self, temp_directory: Path):
        """Test unregistering a hook that doesn't exist."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)

        # Act
        removed = service.unregister_hook("nonexistent")

        # Assert
        assert removed is False

    def test_get_all_hooks(self, temp_directory: Path):
        """Test getting all registered hooks."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook1 = HookConfig(
            name="hook1",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.LOG,
        )
        hook2 = HookConfig(
            name="hook2",
            hook_type=HookType.ON_BLOCKED,
            action=HookAction.NOTIFICATION,
        )
        service.register_hook(hook1)
        service.register_hook(hook2)

        # Act
        hooks = service.get_all_hooks()

        # Assert
        assert len(hooks) == 2
        names = {h.name for h in hooks}
        assert "hook1" in names
        assert "hook2" in names


class TestHookExecution:
    """Test hook execution for different action types."""

    def test_execute_shell_command_hook(self, temp_directory: Path, mock_subprocess):
        """Test executing a shell command hook."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="shell-hook",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.SHELL_COMMAND,
            command="echo 'Status: {status}'",
        )
        service.register_hook(hook)

        # Act
        results = service.trigger_hooks(
            HookType.ON_STATUS_CHANGED,
            "test-worktree",
            {"status": "working"},
        )

        # Assert
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].hook_name == "shell-hook"
        mock_subprocess.assert_called_once()

    def test_execute_shell_command_with_environment_vars(
        self, temp_directory: Path, mock_subprocess
    ):
        """Test shell command receives environment variables."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="env-hook",
            hook_type=HookType.ON_BLOCKED,
            action=HookAction.SHELL_COMMAND,
            command="echo $OWT_WORKTREE",
        )
        service.register_hook(hook)

        # Act
        service.trigger_hooks(
            HookType.ON_BLOCKED,
            "my-worktree",
            {"task": "Implementation"},
        )

        # Assert
        call_args = mock_subprocess.call_args
        assert call_args[1]["env"]["OWT_WORKTREE"] == "my-worktree"
        assert "OWT_TASK" in call_args[1]["env"]

    def test_execute_log_action(self, temp_directory: Path):
        """Test executing a log action hook."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="log-hook",
            hook_type=HookType.ON_ERROR,
            action=HookAction.LOG,
        )
        service.register_hook(hook)

        # Act
        with patch("open_orchestrator.core.hooks.logger") as mock_logger:
            results = service.trigger_hooks(
                HookType.ON_ERROR,
                "test-worktree",
                {"error": "Test error"},
            )

        # Assert
        assert len(results) == 1
        assert results[0].success is True
        mock_logger.info.assert_called_once()

    @patch("subprocess.run")
    def test_execute_notification_on_macos(self, mock_run, temp_directory: Path):
        """Test executing notification hook on macOS."""
        # Arrange
        config = HooksConfig(
            storage_path=temp_directory / "hooks.json",
            enable_notifications=True,
        )
        service = HookService(config=config)
        hook = HookConfig(
            name="notify-hook",
            hook_type=HookType.ON_TASK_COMPLETED,
            action=HookAction.NOTIFICATION,
            notification_title="Task Done",
            notification_message="Completed: {task}",
        )
        service.register_hook(hook)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # Act
        with patch("platform.system", return_value="Darwin"):
            results = service.trigger_hooks(
                HookType.ON_TASK_COMPLETED,
                "test-worktree",
                {"task": "Authentication"},
            )

        # Assert
        assert len(results) == 1
        assert results[0].success is True
        # Verify osascript was called
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert "osascript" in call_args

    @patch("urllib.request.urlopen")
    def test_execute_webhook_hook(self, mock_urlopen, temp_directory: Path):
        """Test executing a webhook hook."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="webhook-hook",
            hook_type=HookType.ON_TASK_STARTED,
            action=HookAction.WEBHOOK,
            webhook_url="https://example.com/webhook",
        )
        service.register_hook(hook)

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = lambda s, *args: False
        mock_urlopen.return_value = mock_response

        # Act
        results = service.trigger_hooks(
            HookType.ON_TASK_STARTED,
            "test-worktree",
            {"task": "New feature"},
        )

        # Assert
        assert len(results) == 1
        assert results[0].success is True
        assert mock_urlopen.called

    def test_execute_shell_command_failure(self, temp_directory: Path):
        """Test shell command failure handling."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="fail-hook",
            hook_type=HookType.ON_ERROR,
            action=HookAction.SHELL_COMMAND,
            command="exit 1",
        )
        service.register_hook(hook)

        # Act
        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_result.stderr = "Command failed"
            mock_run.return_value = mock_result

            results = service.trigger_hooks(HookType.ON_ERROR, "test-worktree", {})

        # Assert
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None

    def test_execute_shell_command_timeout(self, temp_directory: Path):
        """Test shell command timeout handling."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="timeout-hook",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.SHELL_COMMAND,
            command="sleep 100",
            timeout_seconds=1,
        )
        service.register_hook(hook)

        # Act
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("sleep 100", 1)

            results = service.trigger_hooks(
                HookType.ON_STATUS_CHANGED,
                "test-worktree",
                {},
            )

        # Assert
        assert len(results) == 1
        assert results[0].success is False
        assert "timed out" in results[0].error.lower()


class TestHookFiltering:
    """Test hook filtering and conditional execution."""

    def test_filter_by_worktree(self, temp_directory: Path, mock_subprocess):
        """Test filtering hooks by worktree name."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="filtered-hook",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.SHELL_COMMAND,
            command="echo test",
            filter_worktrees=["allowed-worktree"],
        )
        service.register_hook(hook)

        # Act - trigger for non-allowed worktree
        results1 = service.trigger_hooks(
            HookType.ON_STATUS_CHANGED,
            "blocked-worktree",
            {},
        )

        # Act - trigger for allowed worktree
        results2 = service.trigger_hooks(
            HookType.ON_STATUS_CHANGED,
            "allowed-worktree",
            {},
        )

        # Assert
        assert len(results1) == 0  # Filtered out
        assert len(results2) == 1  # Executed

    def test_filter_by_status(self, temp_directory: Path, mock_subprocess):
        """Test filtering hooks by status."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="status-filtered-hook",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.SHELL_COMMAND,
            command="echo test",
            filter_statuses=[AIActivityStatus.BLOCKED, AIActivityStatus.ERROR],
        )
        service.register_hook(hook)

        # Act - trigger for non-filtered status
        results1 = service.trigger_hooks(
            HookType.ON_STATUS_CHANGED,
            "test-worktree",
            {"status": AIActivityStatus.WORKING},
        )

        # Act - trigger for filtered status
        results2 = service.trigger_hooks(
            HookType.ON_STATUS_CHANGED,
            "test-worktree",
            {"status": AIActivityStatus.BLOCKED},
        )

        # Assert
        assert len(results1) == 0  # Filtered out
        assert len(results2) == 1  # Executed

    def test_disabled_hook_not_executed(self, temp_directory: Path, mock_subprocess):
        """Test that disabled hooks are not executed."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="disabled-hook",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.SHELL_COMMAND,
            command="echo test",
            enabled=False,
        )
        service.register_hook(hook)

        # Act
        results = service.trigger_hooks(
            HookType.ON_STATUS_CHANGED,
            "test-worktree",
            {},
        )

        # Assert
        assert len(results) == 0

    def test_enable_disable_hook(self, temp_directory: Path):
        """Test enabling and disabling hooks."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="toggle-hook",
            hook_type=HookType.ON_ERROR,
            action=HookAction.LOG,
            enabled=True,
        )
        service.register_hook(hook)

        # Act
        disabled = service.disable_hook("toggle-hook")
        hook_after_disable = service.get_hook("toggle-hook")

        enabled = service.enable_hook("toggle-hook")
        hook_after_enable = service.get_hook("toggle-hook")

        # Assert
        assert disabled is True
        assert hook_after_disable.enabled is False
        assert enabled is True
        assert hook_after_enable.enabled is True


class TestHookHistory:
    """Test hook execution history tracking."""

    def test_history_tracks_executions(self, temp_directory: Path, mock_subprocess):
        """Test that hook executions are tracked in history."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="tracked-hook",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.SHELL_COMMAND,
            command="echo test",
        )
        service.register_hook(hook)

        # Act
        service.trigger_hooks(HookType.ON_STATUS_CHANGED, "test-worktree", {})

        # Assert
        history = service.get_history()
        assert len(history) == 1
        assert history[0].hook_name == "tracked-hook"

    def test_history_limit(self, temp_directory: Path, mock_subprocess):
        """Test that history respects limit parameter."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="history-hook",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.SHELL_COMMAND,
            command="echo test",
        )
        service.register_hook(hook)

        # Act - trigger multiple times
        for _ in range(5):
            service.trigger_hooks(HookType.ON_STATUS_CHANGED, "test-worktree", {})

        history = service.get_history(limit=3)

        # Assert
        assert len(history) == 3

    def test_clear_history(self, temp_directory: Path, mock_subprocess):
        """Test clearing hook execution history."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)
        hook = HookConfig(
            name="clear-hook",
            hook_type=HookType.ON_STATUS_CHANGED,
            action=HookAction.SHELL_COMMAND,
            command="echo test",
        )
        service.register_hook(hook)
        service.trigger_hooks(HookType.ON_STATUS_CHANGED, "test-worktree", {})

        # Act
        count = service.clear_history()

        # Assert
        assert count == 1
        assert len(service.get_history()) == 0


class TestDefaultHooks:
    """Test default hook creation."""

    def test_create_default_hooks(self, temp_directory: Path):
        """Test creating default hook set."""
        # Arrange
        config = HooksConfig(storage_path=temp_directory / "hooks.json")
        service = HookService(config=config)

        # Act
        defaults = service.create_default_hooks()

        # Assert
        assert len(defaults) >= 4
        hook_names = {h.name for h in defaults}
        assert "notify-on-blocked" in hook_names
        assert "notify-on-completed" in hook_names
        assert "notify-on-error" in hook_names

        # Verify hooks are registered
        all_hooks = service.get_all_hooks()
        assert len(all_hooks) >= 4


class TestStatusToHookType:
    """Test status change to hook type conversion."""

    def test_blocked_status_triggers_on_blocked(self):
        """Test BLOCKED status triggers ON_BLOCKED hook."""
        # Act
        hook_type = get_hook_type_for_status(None, AIActivityStatus.BLOCKED)

        # Assert
        assert hook_type == HookType.ON_BLOCKED

    def test_error_status_triggers_on_error(self):
        """Test ERROR status triggers ON_ERROR hook."""
        # Act
        hook_type = get_hook_type_for_status(None, AIActivityStatus.ERROR)

        # Assert
        assert hook_type == HookType.ON_ERROR

    def test_idle_status_triggers_on_idle(self):
        """Test IDLE status triggers ON_IDLE hook."""
        # Act
        hook_type = get_hook_type_for_status(None, AIActivityStatus.IDLE)

        # Assert
        assert hook_type == HookType.ON_IDLE

    def test_completed_status_triggers_on_task_completed(self):
        """Test COMPLETED status triggers ON_TASK_COMPLETED hook."""
        # Act
        hook_type = get_hook_type_for_status(None, AIActivityStatus.COMPLETED)

        # Assert
        assert hook_type == HookType.ON_TASK_COMPLETED

    def test_working_status_triggers_on_task_started(self):
        """Test WORKING status triggers ON_TASK_STARTED hook when transitioning."""
        # Act
        hook_type = get_hook_type_for_status(
            AIActivityStatus.IDLE,
            AIActivityStatus.WORKING,
        )

        # Assert
        assert hook_type == HookType.ON_TASK_STARTED

    def test_status_change_triggers_on_status_changed(self):
        """Test other status changes trigger ON_STATUS_CHANGED hook."""
        # Act
        hook_type = get_hook_type_for_status(
            AIActivityStatus.WORKING,
            AIActivityStatus.WORKING,
        )

        # Assert
        assert hook_type == HookType.ON_STATUS_CHANGED


# === CLI Integration Tests ===


class TestHooksCLI:
    """Test CLI commands for hook management."""

    def test_hooks_list_command(self, temp_directory: Path):
        """Test 'owt hooks list' command."""
        # Arrange
        runner = CliRunner()

        # Act
        result = runner.invoke(cli, ["hooks", "list"])

        # Assert
        assert result.exit_code == 0

    def test_hooks_add_command(self, temp_directory: Path):
        """Test 'owt hooks add' command."""
        # Arrange
        runner = CliRunner()

        # Act
        with runner.isolated_filesystem(temp_dir=temp_directory):
            result = runner.invoke(
                cli,
                [
                    "hooks",
                    "add",
                    "--name",
                    "test-hook",
                    "--type",
                    "on_status_changed",
                    "--action",
                    "shell_command",
                    "--command",
                    "echo test",
                ],
            )

        # Assert
        assert result.exit_code == 0
        assert "test-hook" in result.output or "Added" in result.output

    def test_hooks_remove_command(self, temp_directory: Path):
        """Test 'owt hooks remove' command."""
        # Arrange
        runner = CliRunner()

        # Act
        with runner.isolated_filesystem(temp_dir=temp_directory):
            # First add a hook
            runner.invoke(
                cli,
                [
                    "hooks",
                    "add",
                    "--name",
                    "remove-test",
                    "--type",
                    "on_blocked",
                    "--action",
                    "log",
                ],
            )

            # Then remove it
            result = runner.invoke(cli, ["hooks", "remove", "remove-test"])

        # Assert
        assert result.exit_code == 0
        assert "Removed" in result.output or "removed" in result.output

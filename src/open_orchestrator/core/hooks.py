"""
Hook execution service for status change notifications.

This module provides functionality to:
- Execute hooks when AI tool status changes
- Send notifications (shell, webhook, system notification)
- Track hook execution history
"""

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from open_orchestrator.models.hooks import (
    HookAction,
    HookConfig,
    HookExecutionResult,
    HooksStore,
    HookType,
)
from open_orchestrator.models.status import AIActivityStatus
from open_orchestrator.utils.io import atomic_write_text, exclusive_file_lock, shared_file_lock

logger = logging.getLogger(__name__)


class HookError(Exception):
    """Base exception for hook operations."""


class HookExecutionError(HookError):
    """Raised when hook execution fails."""


@dataclass
class HooksConfig:
    """Configuration for the hooks service."""

    storage_path: Path | None = None
    max_history_entries: int = 100
    default_timeout: int = 30
    enable_notifications: bool = True
    notification_sound: bool = True
    log_hook_output: bool = True
    allowed_commands: list[str] = field(default_factory=list)


def _escape_applescript(s: str) -> str:
    """Escape a string for safe embedding in AppleScript double-quoted strings."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


class HookService:
    """
    Manages and executes hooks for status changes.

    Hooks can execute shell commands, send notifications,
    or make webhook calls when AI tool status changes.
    """

    DEFAULT_STORAGE_FILENAME = "hooks.json"

    def __init__(self, config: HooksConfig | None = None):
        self.config = config or HooksConfig()
        self._storage_path = self.config.storage_path or self._get_default_path()
        self._store: HooksStore = HooksStore()
        self._removed_keys: set[str] = set()
        self._load_store()

    def _get_default_path(self) -> Path:
        """Get default path for hooks storage."""
        return Path.home() / ".open-orchestrator" / self.DEFAULT_STORAGE_FILENAME

    def _load_store(self) -> None:
        """Load hooks store from persistent storage."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path) as f:
                    with shared_file_lock(f):
                        data = json.load(f)
                        self._store = HooksStore.model_validate(data)
            except (OSError, json.JSONDecodeError, ValueError):
                self._store = HooksStore()
        else:
            self._store = HooksStore()

    def _save_store(self) -> None:
        """Persist hooks store with exclusive lock to prevent lost updates."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._storage_path.with_suffix(".lock")
        try:
            with open(lock_path, "w") as lock_f:
                with exclusive_file_lock(lock_f):
                    # Re-read to merge concurrent changes
                    if self._storage_path.exists():
                        try:
                            with open(self._storage_path) as f:
                                disk_data = json.load(f)
                                disk_store = HooksStore.model_validate(disk_data)
                                # Merge: keep disk hooks we don't have locally
                                # (skip keys explicitly removed in this session)
                                for name, hook in disk_store.hooks.items():
                                    if name not in self._store.hooks and name not in self._removed_keys:
                                        self._store.hooks[name] = hook
                        except (OSError, json.JSONDecodeError, ValueError):
                            pass
                    data = json.dumps(
                        self._store.model_dump(mode="json"),
                        indent=2,
                        default=str,
                    )
                    atomic_write_text(self._storage_path, data, perms=0o600)
        except OSError:
            # Fallback: write without lock
            data = json.dumps(
                self._store.model_dump(mode="json"),
                indent=2,
                default=str,
            )
            atomic_write_text(self._storage_path, data, perms=0o600)

    def register_hook(self, hook: HookConfig) -> None:
        """Register a new hook or update existing one."""
        self._store.set_hook(hook)
        self._save_store()

    def unregister_hook(self, name: str) -> bool:
        """Unregister a hook by name. Returns True if removed."""
        removed = self._store.remove_hook(name)
        if removed:
            self._removed_keys.add(name)
            self._save_store()
        return removed

    def get_hook(self, name: str) -> HookConfig | None:
        """Get a hook by name."""
        return self._store.get_hook(name)

    def get_all_hooks(self) -> list[HookConfig]:
        """Get all registered hooks."""
        return self._store.get_all_hooks()

    def enable_hook(self, name: str) -> bool:
        """Enable a hook. Returns True if found and enabled."""
        hook = self._store.get_hook(name)
        if hook:
            updated = hook.model_copy(update={"enabled": True})
            self._store.set_hook(updated)
            self._save_store()
            return True
        return False

    def disable_hook(self, name: str) -> bool:
        """Disable a hook. Returns True if found and disabled."""
        hook = self._store.get_hook(name)
        if hook:
            updated = hook.model_copy(update={"enabled": False})
            self._store.set_hook(updated)
            self._save_store()
            return True
        return False

    def trigger_hooks(
        self,
        hook_type: HookType,
        worktree_name: str,
        context: dict[str, Any] | None = None,
    ) -> list[HookExecutionResult]:
        """
        Trigger all hooks of a specific type.

        Args:
            hook_type: Type of hook to trigger
            worktree_name: Name of the worktree that triggered the hook
            context: Additional context data

        Returns:
            List of execution results
        """
        hooks = self._store.get_hooks_for_type(hook_type)
        results = []

        for hook in hooks:
            # Check worktree filter
            if hook.filter_worktrees and worktree_name not in hook.filter_worktrees:
                continue

            # Check status filter if applicable
            if context and "status" in context:
                status = context["status"]
                if hook.filter_statuses and status not in hook.filter_statuses:
                    continue

            if hook.run_async:
                # Fire and forget in background thread
                thread = threading.Thread(
                    target=self._execute_and_record,
                    args=(hook, worktree_name, context or {}),
                    daemon=True,
                )
                thread.start()
            else:
                result = self._execute_hook(hook, worktree_name, context or {})
                results.append(result)
                self._store.add_history_entry(result)

        if results:
            self._save_store()

        return results

    def _execute_and_record(
        self,
        hook: HookConfig,
        worktree_name: str,
        context: dict[str, Any],
    ) -> None:
        """Execute a hook and record the result (for async execution)."""
        try:
            result = self._execute_hook(hook, worktree_name, context)
            self._store.add_history_entry(result)
            self._save_store()
        except Exception as e:
            logger.warning(f"Async hook '{hook.name}' failed: {e}")

    def _execute_hook(
        self,
        hook: HookConfig,
        worktree_name: str,
        context: dict[str, Any],
    ) -> HookExecutionResult:
        """Execute a single hook."""
        start_time = time.time()

        try:
            if hook.action == HookAction.SHELL_COMMAND:
                output = self._execute_shell_command(hook, worktree_name, context)
            elif hook.action == HookAction.NOTIFICATION:
                output = self._send_notification(hook, worktree_name, context)
            elif hook.action == HookAction.WEBHOOK:
                output = self._send_webhook(hook, worktree_name, context)
            elif hook.action == HookAction.LOG:
                output = self._log_event(hook, worktree_name, context)
            else:
                raise HookExecutionError(f"Unknown action: {hook.action}")

            duration_ms = int((time.time() - start_time) * 1000)

            return HookExecutionResult(
                hook_name=hook.name,
                hook_type=hook.hook_type,
                action=hook.action,
                success=True,
                output=output,
                duration_ms=duration_ms,
                worktree_name=worktree_name,
                trigger_context={str(k): str(v) for k, v in context.items()},
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.warning(f"Hook '{hook.name}' failed: {e}")

            return HookExecutionResult(
                hook_name=hook.name,
                hook_type=hook.hook_type,
                action=hook.action,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                worktree_name=worktree_name,
                trigger_context={str(k): str(v) for k, v in context.items()},
            )

    def _execute_shell_command(
        self,
        hook: HookConfig,
        worktree_name: str,
        context: dict[str, Any],
    ) -> str:
        """Execute a shell command hook."""
        if not hook.command:
            raise HookExecutionError("No command specified for shell hook")

        # Expand environment variables in command
        env = os.environ.copy()
        env["OWT_WORKTREE"] = worktree_name
        env["OWT_HOOK_TYPE"] = str(hook.hook_type)

        for key, value in context.items():
            env[f"OWT_{key.upper()}"] = str(value)

        # Parse and execute command
        try:
            result = subprocess.run(
                hook.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=hook.timeout_seconds,
                env=env,
            )

            output = result.stdout
            if result.returncode != 0:
                error_output = result.stderr or result.stdout
                raise HookExecutionError(f"Command exited with code {result.returncode}: {error_output}")

            return output

        except subprocess.TimeoutExpired as e:
            raise HookExecutionError(f"Command timed out after {hook.timeout_seconds}s") from e

    def _send_notification(
        self,
        hook: HookConfig,
        worktree_name: str,
        context: dict[str, Any],
    ) -> str:
        """Send a system notification."""
        if not self.config.enable_notifications:
            return "Notifications disabled in config"

        title = hook.notification_title or f"Open Orchestrator: {worktree_name}"
        message = hook.notification_message or f"Status changed to {context.get('status', 'unknown')}"

        # Expand variables in message using safe substitution
        format_vars = {
            "worktree": worktree_name,
            "status": context.get("status", "unknown"),
            "task": context.get("task", ""),
        }
        format_vars.update(context)
        try:
            message = message.format(**{k: v for k, v in format_vars.items() if isinstance(k, str) and isinstance(v, (str, int, float))})
        except (KeyError, ValueError, IndexError):
            pass  # Leave message as-is if format fails

        # Use osascript on macOS for notifications
        import platform

        if platform.system() == "Darwin":
            sound_opt = 'sound name "default"' if self.config.notification_sound else ""
            safe_message = _escape_applescript(message)
            safe_title = _escape_applescript(title)
            script = f'display notification "{safe_message}" with title "{safe_title}" {sound_opt}'

            try:
                subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    timeout=5,
                    check=True,
                )
                return f"Notification sent: {title}"
            except subprocess.CalledProcessError as e:
                raise HookExecutionError(f"osascript failed: {e.stderr}") from e

        elif platform.system() == "Linux":
            try:
                subprocess.run(
                    ["notify-send", title, message],
                    capture_output=True,
                    timeout=5,
                    check=True,
                )
                return f"Notification sent: {title}"
            except FileNotFoundError as e:
                raise HookExecutionError("notify-send not found") from e

        else:
            return f"Notifications not supported on {platform.system()}"

    def _send_webhook(
        self,
        hook: HookConfig,
        worktree_name: str,
        context: dict[str, Any],
    ) -> str:
        """Send a webhook POST request."""
        if not hook.webhook_url:
            raise HookExecutionError("No webhook URL specified")

        import urllib.request

        payload = {
            "hook_name": hook.name,
            "hook_type": str(hook.hook_type),
            "worktree": worktree_name,
            "timestamp": datetime.now().isoformat(),
            "context": context,
        }

        data = json.dumps(payload).encode("utf-8")

        try:
            request = urllib.request.Request(
                hook.webhook_url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "open-orchestrator/1.0",
                },
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=hook.timeout_seconds) as response:
                return f"Webhook sent: {response.status}"

        except Exception as e:
            raise HookExecutionError(f"Webhook failed: {e}") from e

    def _log_event(
        self,
        hook: HookConfig,
        worktree_name: str,
        context: dict[str, Any],
    ) -> str:
        """Log an event to the application logger."""
        message = f"Hook triggered: {hook.name} for {worktree_name}"

        if context:
            message += f" - context: {context}"

        logger.info(message)
        return message

    def get_history(self, limit: int = 20) -> list[HookExecutionResult]:
        """Get recent hook execution history."""
        entries = self._store.get_recent_history(limit)
        return [e.result for e in entries]

    def clear_history(self) -> int:
        """Clear all history. Returns count of entries cleared."""
        count = self._store.clear_history()
        self._save_store()
        return count

    def create_default_hooks(self) -> list[HookConfig]:
        """Create a set of default hooks for common scenarios."""
        defaults = [
            HookConfig(
                name="notify-on-blocked",
                hook_type=HookType.ON_BLOCKED,
                action=HookAction.NOTIFICATION,
                notification_title="Claude Blocked",
                notification_message="{worktree}: Claude needs help - {task}",
            ),
            HookConfig(
                name="notify-on-completed",
                hook_type=HookType.ON_TASK_COMPLETED,
                action=HookAction.NOTIFICATION,
                notification_title="Task Completed",
                notification_message="{worktree}: Claude finished - {task}",
            ),
            HookConfig(
                name="notify-on-error",
                hook_type=HookType.ON_ERROR,
                action=HookAction.NOTIFICATION,
                notification_title="Claude Error",
                notification_message="{worktree}: Error occurred",
            ),
            HookConfig(
                name="log-status-changes",
                hook_type=HookType.ON_STATUS_CHANGED,
                action=HookAction.LOG,
                enabled=False,  # Disabled by default
            ),
        ]

        for hook in defaults:
            self._store.set_hook(hook)

        self._save_store()
        return defaults


def get_hook_service_from_config() -> HookService:
    """Create a HookService using settings from the app config.

    Reads hook-related settings from the user's config file
    (e.g. ~/.config/open-orchestrator/config.toml) and applies them
    to the HooksConfig dataclass used by HookService.
    """
    from open_orchestrator.config import load_config

    config = load_config()
    hooks_cfg = HooksConfig()

    hook_settings = config.hooks
    hooks_cfg.max_history_entries = hook_settings.max_history_entries
    hooks_cfg.default_timeout = hook_settings.default_timeout
    hooks_cfg.enable_notifications = hook_settings.enable_notifications
    hooks_cfg.notification_sound = hook_settings.notification_sound
    hooks_cfg.log_hook_output = hook_settings.log_hook_output

    return HookService(hooks_cfg)


def get_hook_type_for_status(
    old_status: AIActivityStatus | None,
    new_status: AIActivityStatus,
) -> HookType:
    """Determine which hook type to trigger based on status change."""
    if new_status == AIActivityStatus.BLOCKED:
        return HookType.ON_BLOCKED
    if new_status == AIActivityStatus.ERROR:
        return HookType.ON_ERROR
    if new_status == AIActivityStatus.IDLE:
        return HookType.ON_IDLE
    if new_status == AIActivityStatus.COMPLETED:
        return HookType.ON_TASK_COMPLETED
    if new_status == AIActivityStatus.WORKING and old_status != AIActivityStatus.WORKING:
        return HookType.ON_TASK_STARTED

    return HookType.ON_STATUS_CHANGED

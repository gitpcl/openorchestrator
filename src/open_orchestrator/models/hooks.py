"""
Pydantic models for status change hooks.

This module provides data models for:
- Hook configuration and types
- Hook execution results
- Hook history tracking
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class HookType(str, Enum):
    """Types of hooks that can be triggered."""

    ON_STATUS_CHANGED = "on_status_changed"
    ON_TASK_STARTED = "on_task_started"
    ON_TASK_COMPLETED = "on_task_completed"
    ON_BLOCKED = "on_blocked"
    ON_ERROR = "on_error"
    ON_IDLE = "on_idle"


class HookAction(str, Enum):
    """Actions a hook can perform."""

    SHELL_COMMAND = "shell"
    NOTIFICATION = "notification"
    WEBHOOK = "webhook"
    LOG = "log"


class HookConfig(BaseModel):
    """Configuration for a single hook."""

    model_config = ConfigDict(use_enum_values=True)

    name: str = Field(..., description="Unique name for this hook")
    enabled: bool = Field(default=True, description="Whether the hook is enabled")
    hook_type: HookType = Field(..., description="When this hook should trigger")
    action: HookAction = Field(
        default=HookAction.SHELL_COMMAND,
        description="What action to perform"
    )
    command: str | None = Field(
        default=None,
        description="Shell command to execute (for SHELL_COMMAND action)"
    )
    webhook_url: str | None = Field(
        default=None,
        description="URL to POST to (for WEBHOOK action)"
    )
    notification_title: str | None = Field(
        default=None,
        description="Notification title (for NOTIFICATION action)"
    )
    notification_message: str | None = Field(
        default=None,
        description="Notification message template"
    )
    filter_worktrees: list[str] = Field(
        default_factory=list,
        description="Only trigger for these worktrees (empty = all)"
    )
    filter_statuses: list[str] = Field(
        default_factory=list,
        description="Only trigger for these statuses (empty = all)"
    )
    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="Maximum time for hook execution"
    )
    run_async: bool = Field(
        default=True,
        description="Run hook asynchronously (don't block)"
    )


class HookExecutionResult(BaseModel):
    """Result of a hook execution."""

    model_config = ConfigDict(use_enum_values=True)

    hook_name: str = Field(..., description="Name of the executed hook")
    hook_type: HookType = Field(..., description="Type of hook that was triggered")
    action: HookAction = Field(..., description="Action that was performed")
    success: bool = Field(..., description="Whether execution succeeded")
    output: str | None = Field(default=None, description="Output from the hook")
    error: str | None = Field(default=None, description="Error message if failed")
    duration_ms: int = Field(default=0, ge=0, description="Execution time in ms")
    executed_at: datetime = Field(
        default_factory=datetime.now,
        description="When the hook was executed"
    )
    worktree_name: str = Field(..., description="Worktree that triggered the hook")
    trigger_context: dict[str, str] = Field(
        default_factory=dict,
        description="Context data that triggered the hook"
    )


class HookHistoryEntry(BaseModel):
    """Entry in the hook execution history."""

    model_config = ConfigDict(use_enum_values=True)

    result: HookExecutionResult = Field(..., description="Execution result")
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When this entry was created"
    )


class HooksStore(BaseModel):
    """Persistent storage for hooks configuration and history."""

    version: str = Field(default="1.0", description="Storage format version")
    updated_at: datetime = Field(
        default_factory=datetime.now,
        description="When the store was last updated"
    )
    hooks: dict[str, HookConfig] = Field(
        default_factory=dict,
        description="Map of hook name to configuration"
    )
    history: list[HookHistoryEntry] = Field(
        default_factory=list,
        description="Recent hook execution history"
    )
    max_history_entries: int = Field(
        default=100,
        ge=10,
        le=1000,
        description="Maximum history entries to keep"
    )

    def get_hook(self, name: str) -> HookConfig | None:
        """Get hook configuration by name."""
        return self.hooks.get(name)

    def set_hook(self, hook: HookConfig) -> None:
        """Set or update a hook configuration."""
        self.hooks[hook.name] = hook
        self.updated_at = datetime.now()

    def remove_hook(self, name: str) -> bool:
        """Remove a hook. Returns True if removed."""
        if name in self.hooks:
            del self.hooks[name]
            self.updated_at = datetime.now()
            return True
        return False

    def get_hooks_for_type(self, hook_type: HookType) -> list[HookConfig]:
        """Get all enabled hooks of a specific type."""
        return [
            h for h in self.hooks.values()
            if h.enabled and h.hook_type == hook_type
        ]

    def get_all_hooks(self) -> list[HookConfig]:
        """Get all hook configurations."""
        return list(self.hooks.values())

    def add_history_entry(self, result: HookExecutionResult) -> None:
        """Add an execution result to history."""
        entry = HookHistoryEntry(result=result, timestamp=datetime.now())
        self.history.append(entry)

        # Trim history if needed
        if len(self.history) > self.max_history_entries:
            self.history = self.history[-self.max_history_entries:]

        self.updated_at = datetime.now()

    def get_recent_history(self, limit: int = 20) -> list[HookHistoryEntry]:
        """Get most recent history entries."""
        return self.history[-limit:]

    def clear_history(self) -> int:
        """Clear all history entries. Returns count cleared."""
        count = len(self.history)
        self.history = []
        self.updated_at = datetime.now()
        return count

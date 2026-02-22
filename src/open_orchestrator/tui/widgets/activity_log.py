"""
Activity log widget.

Displays recent command activity across all worktrees.
"""

from typing import Any

from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.worktree import WorktreeManager


class ActivityLogWidget(Widget):
    """
    Widget displaying recent command activity.

    Shows the most recent commands executed across all worktrees
    with timestamps and worktree names.
    """

    max_entries: reactive[int] = reactive(10)

    def __init__(
        self,
        status_tracker: StatusTracker,
        wt_manager: WorktreeManager,
        max_entries: int = 10,
        **kwargs: Any,
    ):
        """
        Initialize the activity log widget.

        Args:
            status_tracker: StatusTracker instance for retrieving command data
            wt_manager: WorktreeManager instance for listing worktrees
            max_entries: Maximum number of log entries to display
            **kwargs: Additional widget arguments
        """
        super().__init__(**kwargs)
        self.status_tracker = status_tracker
        self.wt_manager = wt_manager
        self.max_entries = max_entries

    def compose(self) -> ComposeResult:
        """Compose the widget's child widgets."""
        yield Static("", id="activity-content")

    def on_mount(self) -> None:
        """Mount event handler - populate widget with initial data."""
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh the activity log data."""
        worktrees = self.wt_manager.list_all()
        all_commands = []

        for worktree in worktrees:
            if worktree.is_main:
                continue

            wt_status = self.status_tracker.get_status(worktree.name)
            if wt_status and wt_status.recent_commands:
                for cmd in wt_status.recent_commands[-self.max_entries :]:
                    all_commands.append((worktree.name, cmd))

        # Sort by timestamp (most recent first)
        all_commands.sort(key=lambda x: x[1].timestamp, reverse=True)

        lines = []
        for wt_name, cmd in all_commands[: self.max_entries]:
            line = Text()
            line.append(cmd.timestamp.strftime("%H:%M:%S"), style="dim")
            line.append(" [")
            line.append(wt_name, style="cyan")
            line.append("] ")
            line.append(cmd.command[:50] + "..." if len(cmd.command) > 50 else cmd.command)
            lines.append(line)

        if not lines:
            lines.append(Text("No recent activity", style="dim"))

        content = Group(*lines)
        static = self.query_one("#activity-content", Static)
        static.update(content)


__all__ = ["ActivityLogWidget"]

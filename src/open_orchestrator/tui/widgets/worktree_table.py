"""
Worktree status table widget.

Displays a table showing the status, branch, task, and metrics
for all non-main worktrees.
"""


from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable

from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.status import AIActivityStatus


class WorktreeTableWidget(Widget):
    """
    Table widget displaying worktree status information.

    Shows status icon, worktree name, branch, current task, optional
    token usage and cost, optional command count, and last update time.
    Excludes the main worktree from display.
    """

    STATUS_STYLES = {
        AIActivityStatus.IDLE: ("dim", "○"),
        AIActivityStatus.WORKING: ("green bold", "●"),
        AIActivityStatus.BLOCKED: ("red bold", "■"),
        AIActivityStatus.WAITING: ("yellow", "◌"),
        AIActivityStatus.COMPLETED: ("blue", "✓"),
        AIActivityStatus.ERROR: ("red", "✗"),
        AIActivityStatus.UNKNOWN: ("dim", "?"),
    }

    show_token_usage: reactive[bool] = reactive(True)
    show_commands: reactive[bool] = reactive(True)

    def __init__(
        self,
        status_tracker: StatusTracker,
        wt_manager: WorktreeManager,
        show_token_usage: bool = True,
        show_commands: bool = True,
        **kwargs: Any,
    ):
        """
        Initialize the worktree table widget.

        Args:
            status_tracker: StatusTracker instance for retrieving status data
            wt_manager: WorktreeManager instance for listing worktrees
            show_token_usage: Whether to display token and cost columns
            show_commands: Whether to display command count column
            **kwargs: Additional widget arguments
        """
        super().__init__(**kwargs)
        self.status_tracker = status_tracker
        self.wt_manager = wt_manager
        self.show_token_usage = show_token_usage
        self.show_commands = show_commands

    def compose(self) -> ComposeResult:
        """Compose the widget's child widgets."""
        table: DataTable = DataTable()
        table.add_column("Status", width=3)
        table.add_column("Worktree")
        table.add_column("Branch")
        table.add_column("Current Task")

        if self.show_token_usage:
            table.add_column("Tokens")
            table.add_column("Cost")

        if self.show_commands:
            table.add_column("Cmds", width=4)

        table.add_column("Updated", width=14)

        yield table

    def on_mount(self) -> None:
        """Mount event handler - populate table with initial data."""
        self.refresh_data()

    def _get_status_icon(self, status: str) -> tuple[str, str]:
        """
        Get style and icon for a status.

        Args:
            status: Status string value

        Returns:
            Tuple of (style, icon) for the status
        """
        try:
            activity_status = AIActivityStatus(status)
            return self.STATUS_STYLES.get(activity_status, ("dim", "?"))
        except ValueError:
            return ("dim", "?")

    def refresh_data(self) -> None:
        """Refresh the table data from status tracker and worktree manager."""
        table = self.query_one(DataTable)
        table.clear()

        # Get data
        worktrees = self.wt_manager.list_all()

        for worktree in worktrees:
            if worktree.is_main:
                continue

            wt_status = self.status_tracker.get_status(worktree.name)

            if wt_status:
                style, icon = self._get_status_icon(wt_status.activity_status)
                status_cell = Text(icon, style=style)

                task = wt_status.current_task or "-"
                if len(task) > 35:
                    task = task[:32] + "..."

                updated = wt_status.updated_at.strftime("%H:%M %b %d")

                row: list[str | Text] = [
                    status_cell,
                    worktree.name,
                    wt_status.branch,
                    task,
                ]

                if self.show_token_usage:
                    tokens = wt_status.token_usage
                    row.append(f"{tokens.total_tokens:,}" if tokens.total_tokens > 0 else "-")
                    row.append(f"${tokens.estimated_cost_usd:.2f}" if tokens.total_tokens > 0 else "-")

                if self.show_commands:
                    row.append(str(len(wt_status.recent_commands)))

                row.append(updated)
            else:
                # No status tracked
                row = [
                    Text("○", style="dim"),
                    worktree.name,
                    worktree.branch,
                    Text("-", style="dim"),
                ]

                if self.show_token_usage:
                    row.extend(["-", "-"])

                if self.show_commands:
                    row.append("-")

                row.append("-")

            table.add_row(*row)


__all__ = ["WorktreeTableWidget"]

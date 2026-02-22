"""
Status summary panel widget.

Displays aggregated statistics across all worktrees including
active/idle/blocked counts and total token usage.
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


class StatusPanelWidget(Widget):
    """
    Summary panel widget displaying aggregate worktree statistics.

    Shows counts of working/idle/blocked AI sessions and
    total token usage with cost estimation.
    """

    show_token_usage: reactive[bool] = reactive(True)

    def __init__(
        self,
        status_tracker: StatusTracker,
        wt_manager: WorktreeManager,
        show_token_usage: bool = True,
        **kwargs: Any,
    ):
        """
        Initialize the status panel widget.

        Args:
            status_tracker: StatusTracker instance for retrieving summary data
            wt_manager: WorktreeManager instance for listing worktrees
            show_token_usage: Whether to display token usage information
            **kwargs: Additional widget arguments
        """
        super().__init__(**kwargs)
        self.status_tracker = status_tracker
        self.wt_manager = wt_manager
        self.show_token_usage = show_token_usage

    def compose(self) -> ComposeResult:
        """Compose the widget's child widgets."""
        yield Static("", id="summary-content")

    def on_mount(self) -> None:
        """Mount event handler - populate panel with initial data."""
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh the summary panel data."""
        worktrees = self.wt_manager.list_all()
        worktree_names = [wt.name for wt in worktrees]
        summary = self.status_tracker.get_summary(worktree_names)

        lines = []

        # Status counts
        status_line = Text()
        status_line.append("● ", style="green bold")
        status_line.append(f"Working: {summary.active_ai_sessions}  ")
        status_line.append("○ ", style="dim")
        status_line.append(f"Idle: {summary.idle_ai_sessions}  ")
        status_line.append("■ ", style="red bold")
        status_line.append(f"Blocked: {summary.blocked_ai_sessions}")
        lines.append(status_line)

        # Token usage
        if self.show_token_usage and (summary.total_input_tokens > 0 or summary.total_output_tokens > 0):
            total_tokens = summary.total_input_tokens + summary.total_output_tokens
            token_line = Text()
            token_line.append("Tokens: ", style="bold")
            token_line.append(f"{total_tokens:,}")
            token_line.append(f"  (${summary.total_estimated_cost_usd:.4f})", style="dim")
            lines.append(token_line)

        content = Group(*lines)
        static = self.query_one("#summary-content", Static)
        static.update(content)


__all__ = ["StatusPanelWidget"]

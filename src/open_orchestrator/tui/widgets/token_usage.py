"""
Token usage widget.

Displays aggregated token usage statistics and cost estimates
across all worktrees.
"""

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.worktree import WorktreeManager


class TokenUsageWidget(Widget):
    """
    Widget displaying token usage statistics.

    Shows total input tokens, output tokens, and estimated cost
    aggregated across all worktrees.
    """

    show_detailed: reactive[bool] = reactive(True)

    def __init__(
        self,
        status_tracker: StatusTracker,
        wt_manager: WorktreeManager,
        show_detailed: bool = True,
        **kwargs: Any,
    ):
        """
        Initialize the token usage widget.

        Args:
            status_tracker: StatusTracker instance for retrieving token data
            wt_manager: WorktreeManager instance for listing worktrees
            show_detailed: Whether to show detailed breakdown (input/output)
            **kwargs: Additional widget arguments
        """
        super().__init__(**kwargs)
        self.status_tracker = status_tracker
        self.wt_manager = wt_manager
        self.show_detailed = show_detailed

    def compose(self) -> ComposeResult:
        """Compose the widget's child widgets."""
        yield Static("", id="token-content")

    def on_mount(self) -> None:
        """Mount event handler - populate widget with initial data."""
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh the token usage data."""
        worktrees = self.wt_manager.list_all()
        worktree_names = [wt.name for wt in worktrees]
        summary = self.status_tracker.get_summary(worktree_names)

        content = Text()

        if summary.total_input_tokens > 0 or summary.total_output_tokens > 0:
            total_tokens = summary.total_input_tokens + summary.total_output_tokens

            if self.show_detailed:
                content.append("Token Usage:\n", style="bold")
                content.append(f"  Input:  {summary.total_input_tokens:,}\n")
                content.append(f"  Output: {summary.total_output_tokens:,}\n")
                content.append(f"  Total:  {total_tokens:,}\n", style="bold")
                content.append(f"  Cost:   ${summary.total_estimated_cost_usd:.4f}", style="yellow")
            else:
                content.append("Tokens: ", style="bold")
                content.append(f"{total_tokens:,}")
                content.append(f"  (${summary.total_estimated_cost_usd:.4f})", style="yellow")
        else:
            content.append("No token usage", style="dim")

        static = self.query_one("#token-content", Static)
        static.update(content)


__all__ = ["TokenUsageWidget"]

"""
Live dashboard for monitoring worktree AI tool activity.

This module provides a real-time terminal UI for monitoring the status
of AI tools across all worktrees.
"""

import gc
import time
from dataclasses import dataclass
from datetime import datetime

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.status import AIActivityStatus


@dataclass
class DashboardConfig:
    """Configuration for the dashboard."""

    refresh_rate: float = 2.0  # Seconds between updates
    show_token_usage: bool = True
    show_commands: bool = True
    compact: bool = False


class Dashboard:
    """
    Live terminal dashboard for monitoring worktree AI activity.

    Provides a real-time view of what AI tools are doing across
    all worktrees, with status updates, token usage, and more.
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

    def __init__(
        self,
        config: DashboardConfig | None = None,
        console: Console | None = None,
    ):
        self.config = config or DashboardConfig()
        self.console = console or Console()
        self.status_tracker = StatusTracker()
        self.wt_manager = WorktreeManager()
        self._running = False

    def _get_status_icon(self, status: str) -> tuple[str, str]:
        """Get style and icon for a status."""
        try:
            activity_status = AIActivityStatus(status)
            return self.STATUS_STYLES.get(activity_status, ("dim", "?"))
        except ValueError:
            return ("dim", "?")

    def _create_header(self) -> Panel:
        """Create the dashboard header."""
        now = datetime.now().strftime("%H:%M:%S")
        title = Text()
        title.append("Open Orchestrator Dashboard", style="bold cyan")
        title.append(f"  •  {now}", style="dim")

        return Panel(
            title,
            style="cyan",
            border_style="cyan",
        )

    def _create_worktree_table(self) -> Table:
        """Create the worktree status table."""
        table = Table(
            show_header=True,
            header_style="bold white",
            expand=True,
            border_style="dim",
        )

        table.add_column("Status", width=3, justify="center")
        table.add_column("Worktree", style="bold")
        table.add_column("Branch", style="green")
        table.add_column("Current Task")

        if self.config.show_token_usage:
            table.add_column("Tokens", justify="right")
            table.add_column("Cost", justify="right")

        if self.config.show_commands:
            table.add_column("Cmds", width=4, justify="center")

        table.add_column("Updated", width=14)

        # Get data
        worktrees = self.wt_manager.list_all()
        worktree_names = [wt.name for wt in worktrees]
        self.status_tracker.cleanup_orphans(worktree_names)

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

                row = [
                    status_cell,
                    worktree.name,
                    wt_status.branch,
                    task,
                ]

                if self.config.show_token_usage:
                    tokens = wt_status.token_usage
                    row.append(f"{tokens.total_tokens:,}" if tokens.total_tokens > 0 else "-")
                    row.append(f"${tokens.estimated_cost_usd:.2f}" if tokens.total_tokens > 0 else "-")

                if self.config.show_commands:
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

                if self.config.show_token_usage:
                    row.extend(["-", "-"])

                if self.config.show_commands:
                    row.append("-")

                row.append("-")

            table.add_row(*row)

        return table

    def _create_summary_panel(self) -> Panel:
        """Create the summary panel."""
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
        if self.config.show_token_usage and (summary.total_input_tokens > 0 or summary.total_output_tokens > 0):
            total_tokens = summary.total_input_tokens + summary.total_output_tokens
            token_line = Text()
            token_line.append("Tokens: ", style="bold")
            token_line.append(f"{total_tokens:,}")
            token_line.append(f"  (${summary.total_estimated_cost_usd:.4f})", style="dim")
            lines.append(token_line)

        return Panel(
            Group(*lines),
            title="Summary",
            border_style="dim",
        )

    def _create_legend(self) -> Text:
        """Create the status legend."""
        legend = Text()
        legend.append("Legend: ", style="dim")
        legend.append("● ", style="green bold")
        legend.append("Working  ", style="dim")
        legend.append("○ ", style="dim")
        legend.append("Idle  ", style="dim")
        legend.append("■ ", style="red bold")
        legend.append("Blocked  ", style="dim")
        legend.append("◌ ", style="yellow")
        legend.append("Waiting  ", style="dim")
        legend.append("✓ ", style="blue")
        legend.append("Done  ", style="dim")
        legend.append("[dim]Press Ctrl+C to exit[/dim]")
        return legend

    def _create_layout(self) -> Layout:
        """Create the full dashboard layout."""
        layout = Layout()

        if self.config.compact:
            # Compact mode: just the table
            layout.update(
                Group(
                    self._create_header(),
                    self._create_worktree_table(),
                    self._create_legend(),
                )
            )
        else:
            # Full mode with summary
            layout.split_column(
                Layout(self._create_header(), size=3),
                Layout(self._create_worktree_table(), name="main"),
                Layout(self._create_summary_panel(), size=5),
                Layout(self._create_legend(), size=1),
            )

        return layout

    def run(self) -> None:
        """Run the live dashboard."""
        self._running = True
        iteration = 0

        try:
            with Live(
                self._create_layout(),
                console=self.console,
                refresh_per_second=1 / self.config.refresh_rate,
                screen=True,
            ) as live:
                while self._running:
                    live.update(self._create_layout())
                    time.sleep(self.config.refresh_rate)

                    # Force garbage collection every 30 iterations to prevent accumulation
                    iteration += 1
                    if iteration % 30 == 0:
                        gc.collect()
        except KeyboardInterrupt:
            self._running = False

    def stop(self) -> None:
        """Stop the dashboard."""
        self._running = False


__all__ = [
    "Dashboard",
    "DashboardConfig",
]

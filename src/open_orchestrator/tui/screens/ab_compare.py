"""
A/B comparison screen for comparing two AI tools side-by-side.

This screen displays real-time status updates for two AI tools working on
the same task in parallel worktrees, allowing users to compare their
performance, token usage, and outputs.
"""

from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.ab_workspace import ABWorkspace
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus


class ToolPanel(Static):
    """
    Panel displaying status for a single AI tool.

    Shows worktree status, current task, and token usage for one tool
    in the A/B comparison.
    """

    focused: reactive[bool] = reactive(False)

    def __init__(
        self,
        status_tracker: StatusTracker,
        worktree_name: str,
        tool_name: str,
        **kwargs: Any,
    ):
        """
        Initialize the tool panel.

        Args:
            status_tracker: StatusTracker instance for retrieving status data
            worktree_name: Name of the worktree to monitor
            tool_name: Name of the AI tool (e.g., "claude", "opencode")
            **kwargs: Additional widget arguments
        """
        super().__init__(**kwargs)
        self.status_tracker = status_tracker
        self.worktree_name = worktree_name
        self.tool_name = tool_name

    def on_mount(self) -> None:
        """Mount event handler - populate panel with initial data."""
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh the panel with latest status data."""
        status = self.status_tracker.get_status(self.worktree_name)

        if not status:
            self.update(self._render_no_data())
            return

        self.update(self._render_status(status))

    def _render_no_data(self) -> Panel:
        """Render panel when no status data is available."""
        content = Text("No status data available", style="dim")
        border_style = "blue bold" if self.focused else "dim"
        return Panel(
            content,
            title=f"[bold]{self.tool_name.upper()}[/bold]",
            border_style=border_style,
        )

    def _render_status(self, status: WorktreeAIStatus) -> Panel:
        """
        Render panel with status data.

        Args:
            status: WorktreeAIStatus instance

        Returns:
            Rich Panel with formatted status information
        """
        lines = []

        # Status line with icon
        status_icon, status_style = self._get_status_icon(status.activity_status)
        status_line = Text()
        status_line.append(status_icon, style=status_style)
        status_line.append(f" {status.activity_status.value.capitalize()}")
        lines.append(status_line)

        # Current task
        if status.current_task:
            task_line = Text()
            task_line.append("Task: ", style="bold")
            task_line.append(status.current_task[:60] + "..." if len(status.current_task) > 60 else status.current_task)
            lines.append(task_line)

        # Token usage
        if status.token_usage:
            token_line = Text()
            token_line.append("Tokens: ", style="bold")
            token_line.append(f"{status.token_usage.total_tokens:,}")
            lines.append(token_line)

            cost_line = Text()
            cost_line.append("Cost: ", style="bold")
            cost = status.token_usage.calculate_cost_for_tool(self.tool_name)
            cost_line.append(f"${cost:.4f}", style="yellow")
            lines.append(cost_line)

        # Last update
        if status.updated_at:
            update_line = Text()
            update_line.append("Updated: ", style="bold dim")
            update_line.append(status.updated_at.strftime("%H:%M:%S"), style="dim")
            lines.append(update_line)

        content = Group(*lines)
        border_style = "blue bold" if self.focused else "dim"
        return Panel(
            content,
            title=f"[bold]{self.tool_name.upper()}[/bold]",
            border_style=border_style,
        )

    def _get_status_icon(self, status: AIActivityStatus) -> tuple[str, str]:
        """
        Get icon and style for activity status.

        Args:
            status: AIActivityStatus enum value

        Returns:
            Tuple of (icon, style)
        """
        icons = {
            AIActivityStatus.WORKING: ("●", "green bold"),
            AIActivityStatus.IDLE: ("○", "dim"),
            AIActivityStatus.BLOCKED: ("■", "red bold"),
            AIActivityStatus.WAITING: ("◐", "yellow"),
            AIActivityStatus.COMPLETED: ("✓", "green bold"),
            AIActivityStatus.ERROR: ("✗", "red bold"),
            AIActivityStatus.UNKNOWN: ("?", "dim"),
        }
        return icons.get(status, ("?", "dim"))


class CostComparisonPanel(Static):
    """
    Panel comparing token usage and costs between two AI tools.

    Displays side-by-side comparison of input tokens, output tokens,
    total cost, and identifies the cheaper option.
    """

    def __init__(
        self,
        status_tracker: StatusTracker,
        worktree_a: str,
        tool_a: str,
        worktree_b: str,
        tool_b: str,
        **kwargs: Any,
    ):
        """
        Initialize the cost comparison panel.

        Args:
            status_tracker: StatusTracker instance for retrieving status data
            worktree_a: Name of first worktree
            tool_a: Name of first AI tool
            worktree_b: Name of second worktree
            tool_b: Name of second AI tool
            **kwargs: Additional widget arguments
        """
        super().__init__(**kwargs)
        self.status_tracker = status_tracker
        self.worktree_a = worktree_a
        self.tool_a = tool_a
        self.worktree_b = worktree_b
        self.tool_b = tool_b

    def on_mount(self) -> None:
        """Mount event handler - populate panel with initial data."""
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh the comparison panel with latest data."""
        status_a = self.status_tracker.get_status(self.worktree_a)
        status_b = self.status_tracker.get_status(self.worktree_b)

        self.update(self._render_comparison(status_a, status_b))

    def _render_comparison(self, status_a: WorktreeAIStatus | None, status_b: WorktreeAIStatus | None) -> Panel:
        """
        Render cost comparison panel.

        Args:
            status_a: WorktreeAIStatus for first tool (None if not available)
            status_b: WorktreeAIStatus for second tool (None if not available)

        Returns:
            Rich Panel with cost comparison
        """
        lines = []

        if not status_a or not status_b:
            content = Text("Waiting for status data...", style="dim")
            return Panel(content, title="[bold]Cost Comparison[/bold]", border_style="yellow")

        # Get token usage
        tokens_a = status_a.token_usage if status_a.token_usage else None
        tokens_b = status_b.token_usage if status_b.token_usage else None

        if not tokens_a or not tokens_b:
            content = Text("No token usage data available", style="dim")
            return Panel(content, title="[bold]Cost Comparison[/bold]", border_style="yellow")

        # Calculate costs
        cost_a = tokens_a.calculate_cost_for_tool(self.tool_a)
        cost_b = tokens_b.calculate_cost_for_tool(self.tool_b)

        # Input tokens comparison
        input_line = Text()
        input_line.append("Input Tokens:  ", style="bold")
        input_line.append(f"{self.tool_a}: {tokens_a.input_tokens:,}".ljust(30))
        input_line.append(f"  {self.tool_b}: {tokens_b.input_tokens:,}")
        lines.append(input_line)

        # Output tokens comparison
        output_line = Text()
        output_line.append("Output Tokens: ", style="bold")
        output_line.append(f"{self.tool_a}: {tokens_a.output_tokens:,}".ljust(30))
        output_line.append(f"  {self.tool_b}: {tokens_b.output_tokens:,}")
        lines.append(output_line)

        # Total tokens comparison
        total_line = Text()
        total_line.append("Total Tokens:  ", style="bold")
        total_line.append(f"{self.tool_a}: {tokens_a.total_tokens:,}".ljust(30))
        total_line.append(f"  {self.tool_b}: {tokens_b.total_tokens:,}")
        lines.append(total_line)

        # Cost comparison
        cost_line = Text()
        cost_line.append("Estimated Cost:", style="bold")
        cost_line.append(f"{self.tool_a}: ${cost_a:.4f}".ljust(30), style="yellow")
        cost_line.append(f"  {self.tool_b}: ${cost_b:.4f}", style="yellow")
        lines.append(cost_line)

        # Identify cheaper option
        if cost_a < cost_b:
            savings = cost_b - cost_a
            cheaper_line = Text()
            cheaper_line.append("💰 ", style="green bold")
            cheaper_line.append(f"{self.tool_a.upper()} is cheaper by ${savings:.4f}", style="green bold")
            lines.append(cheaper_line)
        elif cost_b < cost_a:
            savings = cost_a - cost_b
            cheaper_line = Text()
            cheaper_line.append("💰 ", style="green bold")
            cheaper_line.append(f"{self.tool_b.upper()} is cheaper by ${savings:.4f}", style="green bold")
            lines.append(cheaper_line)
        else:
            equal_line = Text("Both tools have equal cost", style="dim")
            lines.append(equal_line)

        comparison_content: RenderableType = Group(*lines)
        return Panel(comparison_content, title="[bold]Cost Comparison[/bold]", border_style="yellow")


class ABCompareScreen(Screen[None]):
    """
    A/B comparison screen showing two AI tools side-by-side.

    Displays real-time status updates, token usage comparison, and
    activity logs for two AI tools working on the same task.

    Keyboard shortcuts:
    - Tab: Switch focus between left and right panels
    - q: Exit to main screen
    """

    CSS = """
    ABCompareScreen {
        layout: vertical;
    }

    #split-panes {
        layout: horizontal;
        height: 1fr;
    }

    #left-pane, #right-pane {
        width: 1fr;
        border: solid $accent;
        padding: 1;
    }

    #cost-comparison {
        height: auto;
        padding: 1;
    }

    .focused {
        border: solid blue;
    }
    """

    BINDINGS = [
        ("tab", "toggle_focus", "Switch Focus"),
        ("q", "exit_screen", "Back"),
    ]

    left_focused: reactive[bool] = reactive(True)

    def __init__(
        self,
        workspace: ABWorkspace,
        status_tracker: StatusTracker,
        wt_manager: WorktreeManager,
        **kwargs: Any,
    ):
        """
        Initialize the A/B comparison screen.

        Args:
            workspace: ABWorkspace instance containing worktree pair
            status_tracker: StatusTracker instance for polling status
            wt_manager: WorktreeManager instance for worktree operations
            **kwargs: Additional screen arguments
        """
        super().__init__(**kwargs)
        self.workspace = workspace
        self.status_tracker = status_tracker
        self.wt_manager = wt_manager
        self._refresh_interval: float = 2.0

    def compose(self) -> ComposeResult:
        """Compose the screen layout."""
        yield Header()

        # Split-pane container
        with Horizontal(id="split-panes"):
            with Vertical(id="left-pane"):
                yield ToolPanel(
                    status_tracker=self.status_tracker,
                    worktree_name=self.workspace.worktree_a,
                    tool_name=self.workspace.tool_a.value,
                    id="tool-a-panel",
                )

            with Vertical(id="right-pane"):
                yield ToolPanel(
                    status_tracker=self.status_tracker,
                    worktree_name=self.workspace.worktree_b,
                    tool_name=self.workspace.tool_b.value,
                    id="tool-b-panel",
                )

        # Cost comparison panel at bottom
        yield Container(
            CostComparisonPanel(
                status_tracker=self.status_tracker,
                worktree_a=self.workspace.worktree_a,
                tool_a=self.workspace.tool_a.value,
                worktree_b=self.workspace.worktree_b,
                tool_b=self.workspace.tool_b.value,
            ),
            id="cost-comparison",
        )

        yield Footer()

    def on_mount(self) -> None:
        """Mount event handler - start periodic refresh and set initial focus."""
        self.set_interval(self._refresh_interval, self._refresh_ui)
        self._update_focus()

    def _refresh_ui(self) -> None:
        """Refresh UI with latest status data."""
        # Refresh tool panels
        tool_a_panel = self.query_one("#tool-a-panel", ToolPanel)
        tool_a_panel.refresh_data()

        tool_b_panel = self.query_one("#tool-b-panel", ToolPanel)
        tool_b_panel.refresh_data()

        # Refresh cost comparison
        cost_panel = self.query_one(CostComparisonPanel)
        cost_panel.refresh_data()

    def _update_focus(self) -> None:
        """Update focus indicators on tool panels."""
        tool_a_panel = self.query_one("#tool-a-panel", ToolPanel)
        tool_b_panel = self.query_one("#tool-b-panel", ToolPanel)

        tool_a_panel.focused = self.left_focused
        tool_b_panel.focused = not self.left_focused

        # Trigger re-render
        tool_a_panel.refresh_data()
        tool_b_panel.refresh_data()

    def action_toggle_focus(self) -> None:
        """Handle 'tab' key - toggle focus between left and right panels."""
        self.left_focused = not self.left_focused
        self._update_focus()

    def action_exit_screen(self) -> None:
        """Handle 'q' key - exit back to main screen."""
        self.app.pop_screen()


__all__ = ["ABCompareScreen"]

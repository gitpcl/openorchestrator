"""
OrchestratorApp TUI - Main interactive application.

Provides dmux-style interactive worktree management with keyboard navigation.
"""

import sys
from typing import Any

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header

from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.tui.widgets import WorktreeTableWidget


class OrchestratorApp(App[None]):
    """
    Main TUI application for Open Orchestrator.

    Provides interactive worktree management with keyboard navigation:
    - n: Create new worktree
    - d: Delete selected worktree
    - j/k: Navigate up/down
    - enter: Attach to selected worktree
    - a: Launch A/B comparison
    - q: Quit application

    Polls StatusTracker every 2 seconds for automatic state updates.
    """

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        ("n", "new_worktree", "New"),
        ("d", "delete_worktree", "Delete"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("enter", "attach", "Attach"),
        ("a", "ab_launch", "A/B Launch"),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        status_tracker: StatusTracker | None = None,
        wt_manager: WorktreeManager | None = None,
        **kwargs: Any,
    ):
        """
        Initialize the OrchestratorApp.

        Args:
            status_tracker: StatusTracker instance for state polling
            wt_manager: WorktreeManager instance for worktree operations
            **kwargs: Additional app arguments
        """
        super().__init__(**kwargs)
        self.status_tracker = status_tracker or StatusTracker()
        self.wt_manager = wt_manager or WorktreeManager()
        self._refresh_interval: float = 2.0

    def compose(self) -> ComposeResult:
        """Compose the application layout."""
        yield Header()
        yield WorktreeTableWidget(
            status_tracker=self.status_tracker,
            wt_manager=self.wt_manager,
        )
        yield Footer()

    def on_mount(self) -> None:
        """Mount event handler - start periodic refresh."""
        self.set_interval(self._refresh_interval, self._refresh_ui)

    def _refresh_ui(self) -> None:
        """Refresh UI with latest status data."""
        widget = self.query_one(WorktreeTableWidget)
        widget.refresh_data()

    def _get_selected_worktree(self) -> str | None:
        """
        Get the name of the currently selected worktree.

        Returns:
            Selected worktree name, or None if no selection
        """
        widget = self.query_one(WorktreeTableWidget)
        table = widget.query_one(DataTable)

        if table.cursor_row is None:
            return None

        if table.row_count == 0:
            return None

        # Get worktree name from the second column (index 1)
        row_key = table.cursor_row

        try:
            row_data = table.get_row(row_key)  # type: ignore[arg-type]
        except Exception:
            # Row doesn't exist or table not ready
            return None

        if len(row_data) < 2:
            return None

        # Worktree name is in column 1 (Status=0, Worktree=1, Branch=2, ...)
        return str(row_data[1])

    def action_new_worktree(self) -> None:
        """Handle 'n' key - create new worktree."""
        # Placeholder for new worktree creation
        # Will be implemented when integrated with CLI
        self.notify("New worktree creation not yet implemented")

    def action_delete_worktree(self) -> None:
        """Handle 'd' key - delete selected worktree."""
        selected = self._get_selected_worktree()
        if selected is None:
            self.notify("No worktree selected")
            return

        # Placeholder for worktree deletion
        # Will be implemented when integrated with CLI
        self.notify(f"Delete worktree '{selected}' not yet implemented")

    def action_cursor_down(self) -> None:
        """Handle 'j' key - move cursor down."""
        widget = self.query_one(WorktreeTableWidget)
        table = widget.query_one(DataTable)

        if table.row_count == 0:
            return

        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        """Handle 'k' key - move cursor up."""
        widget = self.query_one(WorktreeTableWidget)
        table = widget.query_one(DataTable)

        if table.row_count == 0:
            return

        table.action_cursor_up()

    def action_attach(self) -> None:
        """Handle 'enter' key - attach to selected worktree."""
        selected = self._get_selected_worktree()
        if selected is None:
            self.notify("No worktree selected")
            return

        # Placeholder for tmux attach
        # Will be implemented when integrated with CLI
        self.notify(f"Attach to worktree '{selected}' not yet implemented")

    def action_ab_launch(self) -> None:
        """Handle 'a' key - launch A/B comparison."""
        # Placeholder for A/B launcher
        # Will be implemented in Task 6
        self.notify("A/B launch not yet implemented")


def is_interactive_terminal() -> bool:
    """
    Check if the current terminal is interactive.

    Returns:
        True if running in an interactive terminal, False otherwise
    """
    return sys.stdout.isatty()


def launch_tui(
    status_tracker: StatusTracker | None = None,
    wt_manager: WorktreeManager | None = None,
) -> None:
    """
    Launch the TUI application.

    Falls back to CLI mode if terminal is non-interactive.

    Args:
        status_tracker: Optional StatusTracker instance
        wt_manager: Optional WorktreeManager instance
    """
    if not is_interactive_terminal():
        from rich.console import Console

        console = Console()
        console.print(
            "[yellow]Non-interactive terminal detected. Use 'owt list' for CLI mode.[/yellow]"
        )
        return

    app = OrchestratorApp(
        status_tracker=status_tracker,
        wt_manager=wt_manager,
    )
    app.run()


__all__ = [
    "OrchestratorApp",
    "is_interactive_terminal",
    "launch_tui",
]

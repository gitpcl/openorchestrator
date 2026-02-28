"""OrchestratorApp TUI - dmux-style persistent sidebar.

A Textual app that runs in tmux pane 0, capturing keys directly (no prefix
needed). Manages agent panes via keyboard shortcuts: n/x/m/j/k/Enter/q/?.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Static
from textual import work

from open_orchestrator.config import (
    THEMES,
    AITool,
    get_active_theme,
    get_default_config_path,
    load_config,
    save_config,
)
from open_orchestrator.core.ab_launcher import ABLauncher
from open_orchestrator.core.merge import MergeConflictError, MergeError, MergeManager
from open_orchestrator.core.pane_actions import (
    PaneActionError,
    create_pane,
    popup_result_path,
    read_popup_result,
    remove_pane,
)
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.core.workspace import WorkspaceManager
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.status import AIActivityStatus
from open_orchestrator.tui.screens import (
    ABCompareScreen,
    ConfirmScreen,
    HelpOverlayScreen,
    ThemePickerScreen,
)

logger = logging.getLogger(__name__)

# dmux-style status icons and colors
STATUS_ICONS: dict[str, tuple[str, str]] = {
    AIActivityStatus.WORKING: ("\u273b", "#00d7ff"),       # ✻ cyan (working)
    AIActivityStatus.IDLE: ("\u25cc", "#6c6c6c"),          # ◌ gray
    AIActivityStatus.BLOCKED: ("\u25b3", "#ff5f5f"),       # △ red
    AIActivityStatus.WAITING: ("\u29d6", "#ffaf00"),        # ⧖ yellow (waiting)
    AIActivityStatus.COMPLETED: ("\u2713", "#5fff5f"),      # ✓ green
    AIActivityStatus.ERROR: ("\u2717", "#ff5f5f"),          # ✗ red
    AIActivityStatus.UNKNOWN: ("\u25cc", "#6c6c6c"),        # ◌ gray
}

# Agent abbreviation tags (matches dmux style)
AGENT_TAGS: dict[str, str] = {
    "claude": "cc",
    "opencode": "oc",
    "droid": "dr",
    "codex": "cx",
    "gemini-cli": "gc",
    "aider": "ai",
    "amp": "am",
    "kilo-code": "kc",
}


TCSS_TEMPLATE = """\
/* OrchestratorApp TUI Styles — themed */

Screen {{
    background: #1c1c1c;
}}

#sidebar {{
    width: 100%;
    height: 1fr;
    background: #1c1c1c;
}}

#sidebar-title {{
    width: 100%;
    height: 1;
    background: {accent};
    color: #1c1c1c;
    text-align: center;
    text-style: bold;
    padding: 0 1;
}}

PaneListWidget {{
    height: 1fr;
    border: none;
}}

PaneListWidget DataTable {{
    height: 100%;
    background: #1c1c1c;
}}

DataTable > .datatable--cursor {{
    background: {cursor_bg};
    color: {accent};
}}

DataTable > .datatable--header {{
    height: 0;
}}

DataTable > .datatable--hover {{
    background: #2a2a2a;
}}

#status-bar {{
    width: 100%;
    height: 1;
    background: #262626;
    color: #6c6c6c;
    padding: 0 1;
}}

Footer {{
    background: #262626;
}}

FooterKey {{
    background: #262626;
}}

ConfirmScreen {{
    align: center middle;
    background: rgba(0, 0, 0, 0.7);
}}

#confirm-dialog {{
    width: 100%;
    max-width: 50;
    height: auto;
    border: thick {accent};
    background: #1c1c1c;
    padding: 1 1;
}}

#confirm-message {{
    width: 100%;
    text-align: center;
    color: #ffffff;
    margin-bottom: 1;
}}

HelpOverlayScreen {{
    align: center middle;
    background: rgba(0, 0, 0, 0.7);
}}

#help-dialog {{
    width: 100%;
    max-width: 48;
    height: auto;
    border: thick {accent};
    background: #1c1c1c;
    padding: 1 1;
}}

#help-title {{
    width: 100%;
    text-align: center;
    text-style: bold;
    color: {accent};
    margin-bottom: 1;
}}

#help-content {{
    width: 100%;
    color: #d0d0d0;
}}

#help-footer {{
    width: 100%;
    text-align: center;
    margin-top: 1;
    color: #6c6c6c;
}}

ThemePickerScreen {{
    align: center middle;
    background: rgba(0, 0, 0, 0.7);
}}

#theme-dialog {{
    width: 100%;
    max-width: 40;
    height: auto;
    border: thick {accent};
    background: #1c1c1c;
    padding: 1 1;
}}

#theme-title {{
    width: 100%;
    text-align: center;
    text-style: bold;
    color: {accent};
    margin-bottom: 1;
}}

#theme-footer {{
    width: 100%;
    text-align: center;
    margin-top: 1;
    color: #6c6c6c;
}}

ToastRack {{
    align: center bottom;
    width: 100%;
    margin-bottom: 3;
}}

Toast {{
    width: 1fr;
    max-width: 100%;
    margin: 0 1 0 2;
    border-left: wide {accent};
}}
"""


def build_css(theme_name: str) -> str:
    """Build CSS string from template using the given theme."""
    theme = THEMES.get(theme_name, THEMES["cyan"])
    return TCSS_TEMPLATE.format(accent=theme.accent, cursor_bg=theme.cursor_bg)


class PaneListWidget(Widget):
    """dmux-style pane list widget.

    Renders compact single-line pane cards with:
    - Selection indicator (▸)
    - Status icon with color
    - Pane slug name
    - Agent tag [cc], [oc], etc.
    """

    def __init__(
        self,
        status_tracker: StatusTracker,
        wt_manager: WorktreeManager,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.status_tracker = status_tracker
        self.wt_manager = wt_manager
        self._pane_names: list[str] = []

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", width=2)      # selection indicator
        table.add_column("", width=2)      # status icon
        table.add_column("Pane", width=20) # pane name
        table.add_column("", width=5)      # agent tag
        table.show_header = False
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh pane list from status tracker and worktree manager."""
        table = self.query_one(DataTable)
        cursor_row = table.cursor_row if table.row_count > 0 else 0
        table.clear()

        worktrees = self.wt_manager.list_all()
        self._pane_names = []

        for wt in worktrees:
            if wt.is_main:
                continue

            self._pane_names.append(wt.name)
            idx = len(self._pane_names) - 1
            is_selected = idx == cursor_row

            wt_status = self.status_tracker.get_status(wt.name)

            # Selection indicator
            selector = Text("\u25b8 " if is_selected else "  ")

            # Status icon
            if wt_status:
                icon_char, icon_color = STATUS_ICONS.get(
                    wt_status.activity_status,
                    ("\u25cc", "#6c6c6c"),
                )
                status_icon = Text(icon_char, style=icon_color)

                # Agent tag
                tag = AGENT_TAGS.get(str(wt_status.ai_tool), "")
                agent_tag = Text(f"[{tag}]", style="#6c6c6c") if tag else Text("")
            else:
                status_icon = Text("\u25cc", style="#6c6c6c")
                agent_tag = Text("")

            # Pane name (clip to width)
            name = wt.name[:20]
            pane_name = Text(name, style="bold" if is_selected else "")

            table.add_row(selector, status_icon, pane_name, agent_tag)

        # Restore cursor position
        if table.row_count > 0:
            safe_row = min(cursor_row, table.row_count - 1)
            table.move_cursor(row=safe_row)

    @property
    def pane_names(self) -> list[str]:
        """List of pane names currently displayed."""
        return list(self._pane_names)

    @property
    def selected_pane_name(self) -> str | None:
        """Get the name of the currently selected pane."""
        table = self.query_one(DataTable)
        idx = table.cursor_row if table.row_count > 0 else -1
        if 0 <= idx < len(self._pane_names):
            return self._pane_names[idx]
        return None

    @property
    def pane_count(self) -> int:
        return len(self._pane_names)


class StatusBarWidget(Static):
    """Compact status bar at bottom of sidebar showing active/total counts."""

    def __init__(self, status_tracker: StatusTracker, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.status_tracker = status_tracker

    def refresh_data(self, pane_names: list[str]) -> None:
        summary = self.status_tracker.get_summary(pane_names)
        active = summary.active_ai_sessions if summary else 0
        total = len(pane_names)
        self.update(f" {active} active / {total} total")


class OrchestratorApp(App[None]):
    """dmux-style persistent TUI sidebar.

    Runs in tmux pane 0, captures keys directly. No prefix needed.
    """

    CSS = ""  # Set by launch_tui() before app.run()

    BINDINGS = [
        ("n", "new_pane", "[n]ew"),
        ("x", "close_pane", "[x] close"),
        ("m", "merge_worktree", "[m]erge"),
        ("j", "cursor_down", ""),
        ("k", "cursor_up", ""),
        ("down", "cursor_down", ""),
        ("up", "cursor_up", ""),
        ("enter", "attach", "attach"),
        ("a", "ab_launch", "a/b"),
        ("s", "settings", "[s]ettings"),
        ("question_mark", "show_help", "[?] help"),
        ("q", "quit_tui", "[q]uit"),
    ]

    TITLE = "owt"

    def __init__(
        self,
        status_tracker: StatusTracker | None = None,
        wt_manager: WorktreeManager | None = None,
        ab_launcher: ABLauncher | None = None,
        workspace_name: str | None = None,
        repo_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.status_tracker = status_tracker or StatusTracker()
        self.wt_manager = wt_manager or WorktreeManager()
        self.ab_launcher = ab_launcher or ABLauncher()
        self.workspace_name = workspace_name or os.environ.get("OWT_WORKSPACE", "")
        self.repo_path = repo_path or os.environ.get("OWT_REPO", "")
        self._refresh_interval: float = 2.0

    def compose(self) -> ComposeResult:
        with Vertical(id="sidebar"):
            yield Static("[b]owt[/b]", id="sidebar-title")
            yield PaneListWidget(
                status_tracker=self.status_tracker,
                wt_manager=self.wt_manager,
                id="pane-list",
            )
            yield StatusBarWidget(
                status_tracker=self.status_tracker,
                id="status-bar",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(self._refresh_interval, self._refresh_ui)

    def _refresh_ui(self) -> None:
        try:
            pane_list = self.query_one("#pane-list", PaneListWidget)
            pane_list.refresh_data()

            status_bar = self.query_one("#status-bar", StatusBarWidget)
            status_bar.refresh_data(pane_list.pane_names)
        except Exception:
            logger.debug("Failed to refresh TUI", exc_info=True)

    def _get_selected_worktree(self) -> str | None:
        pane_list = self.query_one("#pane-list", PaneListWidget)
        return pane_list.selected_pane_name

    # ── Navigation ──────────────────────────────────────────

    def action_cursor_down(self) -> None:
        pane_list = self.query_one("#pane-list", PaneListWidget)
        table = pane_list.query_one(DataTable)
        if table.row_count == 0:
            return
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        pane_list = self.query_one("#pane-list", PaneListWidget)
        table = pane_list.query_one(DataTable)
        if table.row_count == 0:
            return
        table.action_cursor_up()

    # ── Pane actions ────────────────────────────────────────

    def action_new_pane(self) -> None:
        """Open the popup picker via tmux display-popup, then create pane."""
        if not self.workspace_name:
            self.notify("No workspace configured", severity="error")
            return
        self._run_popup_picker()

    @work(thread=True)
    def _run_popup_picker(self) -> None:
        """Run the curses popup picker via tmux display-popup (background thread)."""
        result_file = popup_result_path(self.workspace_name)

        # tmux display-popup runs as an overlay — doesn't interfere with Textual
        try:
            subprocess.run(
                [
                    "tmux", "display-popup", "-E",
                    "-w", "60", "-h", "20",
                    f"owt-popup {result_file}",
                ],
                check=False,
            )
        except FileNotFoundError:
            self.call_from_thread(
                self.notify, "tmux not available", severity="error",
            )
            return

        # Read result (cleans up temp file)
        try:
            popup_data = read_popup_result(result_file)
        except PaneActionError:
            return  # User cancelled the popup

        branch = popup_data.get("branch")
        ai_tool_str = popup_data.get("ai_tool", "claude")

        if not branch:
            self.call_from_thread(
                self.notify, "No branch selected", severity="warning",
            )
            return

        try:
            result = create_pane(
                workspace_name=self.workspace_name,
                repo_path=self.repo_path,
                branch=branch,
                ai_tool=AITool(ai_tool_str),
                template_name=popup_data.get("template"),
            )
            self.call_from_thread(
                self.notify, f"Pane created: {result.worktree_name}",
            )
        except PaneActionError as e:
            self.call_from_thread(
                self.notify, f"Failed: {e}", severity="error",
            )

        self.call_from_thread(self._refresh_ui)

    def action_close_pane(self) -> None:
        """Close selected pane with confirmation."""
        selected = self._get_selected_worktree()
        if selected is None:
            self.notify("No pane selected", severity="warning")
            return

        self.push_screen(
            ConfirmScreen(f"Close '{selected}' and delete worktree?"),
            callback=lambda confirmed: self._do_close_pane(selected) if confirmed else None,
        )

    @work(thread=True)
    def _do_close_pane(self, worktree_name: str) -> None:
        """Actually close the pane (background thread)."""
        try:
            remove_pane(
                workspace_name=self.workspace_name,
                worktree_name=worktree_name,
                repo_path=self.repo_path,
            )
            self.call_from_thread(
                self.notify, f"Closed: {worktree_name}",
            )
        except PaneActionError as e:
            self.call_from_thread(
                self.notify, f"Failed: {e}", severity="error",
            )

        self.call_from_thread(self._refresh_ui)

    def action_merge_worktree(self) -> None:
        """Merge selected worktree branch."""
        selected = self._get_selected_worktree()
        if selected is None:
            self.notify("No pane selected", severity="warning")
            return

        self.push_screen(
            ConfirmScreen(f"Merge '{selected}' into base branch?"),
            callback=lambda confirmed: self._do_merge(selected) if confirmed else None,
        )

    @work(thread=True)
    def _do_merge(self, worktree_name: str) -> None:
        """Actually merge the worktree (background thread)."""
        try:
            merge_mgr = MergeManager(repo_path=self.repo_path)
            result = merge_mgr.merge(worktree_name, delete_worktree=False)

            msg = f"Merged: {result.source_branch} -> {result.target_branch}"
            if result.commits_merged:
                msg += f" ({result.commits_merged} commits)"
            self.call_from_thread(self.notify, msg)

        except MergeConflictError as e:
            conflicts = ", ".join(e.conflicts[:3])
            self.call_from_thread(
                self.notify,
                f"Conflicts in: {conflicts}",
                severity="error",
            )
        except MergeError as e:
            self.call_from_thread(
                self.notify, f"Merge failed: {e}", severity="error",
            )

        self.call_from_thread(self._refresh_ui)

    def action_attach(self) -> None:
        """Focus the tmux pane for the selected worktree."""
        selected = self._get_selected_worktree()
        if selected is None:
            self.notify("No pane selected", severity="warning")
            return
        self._do_attach(selected)

    @work(thread=True)
    def _do_attach(self, worktree_name: str) -> None:
        """Focus the tmux pane (background thread to avoid blocking UI)."""
        try:
            ws_mgr = WorkspaceManager()
            workspace = ws_mgr.get_workspace(self.workspace_name)
            target = workspace.get_pane_by_worktree(worktree_name)
            if target:
                TmuxManager._run_tmux_cmd("select-pane", "-t", f"%{target.pane_index}")
            else:
                self.call_from_thread(
                    self.notify, f"Pane not found for '{worktree_name}'", severity="warning",
                )
        except Exception as e:
            self.call_from_thread(
                self.notify, f"Cannot attach: {e}", severity="error",
            )

    # ── A/B Launch ──────────────────────────────────────────

    def action_ab_launch(self) -> None:
        """Launch A/B comparison screen."""
        selected = self._get_selected_worktree()
        if selected is None:
            self.notify("No pane selected", severity="warning")
            return

        workspace = self.ab_launcher.store.find_by_worktree(selected)
        if workspace is None:
            self.notify(
                f"'{selected}' is not part of an A/B workspace. "
                "Create one with: owt create <branch> --ab <tool1> <tool2>",
                severity="warning",
            )
            return

        screen = ABCompareScreen(
            workspace=workspace,
            status_tracker=self.status_tracker,
            wt_manager=self.wt_manager,
        )
        self.push_screen(screen)

    # ── Settings / Theme ────────────────────────────────

    def action_settings(self) -> None:
        """Open theme picker."""
        self.push_screen(
            ThemePickerScreen(),
            callback=self._apply_theme,
        )

    def _apply_theme(self, theme_name: str | None) -> None:
        """Apply selected theme live and persist to config."""
        if theme_name is None:
            return

        theme = THEMES.get(theme_name)
        if theme is None:
            return

        # Live-update widget styles
        try:
            self.query_one("#sidebar-title").styles.background = theme.accent
            self.query_one("#sidebar-title").styles.color = "#1c1c1c"
        except Exception:
            pass

        # Persist to config file
        try:
            config = load_config()
            config.ui.theme = theme_name
            save_config(config, get_default_config_path())
        except Exception:
            logger.warning("Failed to save theme preference", exc_info=True)

        self.notify(f"Theme: {theme_name}")

    # ── Help & Quit ─────────────────────────────────────────

    def action_show_help(self) -> None:
        """Show keybinding help overlay."""
        self.push_screen(HelpOverlayScreen())

    def action_quit_tui(self) -> None:
        """Quit with confirmation."""
        pane_list = self.query_one("#pane-list", PaneListWidget)
        count = pane_list.pane_count

        if count > 0:
            self.push_screen(
                ConfirmScreen(
                    f"{count} agent pane(s) still running. Quit anyway?",
                    confirm_label="Quit",
                ),
                callback=lambda confirmed: self._do_quit() if confirmed else None,
            )
        else:
            self._do_quit()

    def _do_quit(self) -> None:
        """Kill the tmux session (all agent panes), then exit Textual."""
        # Kill the whole tmux session first so agent panes don't linger.
        # This kills all panes including pane 0 (where TUI runs), which
        # causes Textual's terminal to close. We call exit() as well for
        # cases where we're not inside tmux.
        if self.workspace_name:
            TmuxManager._run_tmux_cmd("kill-session", "-t", self.workspace_name)
        self.exit()


def is_interactive_terminal() -> bool:
    """Check if the current terminal is interactive."""
    return sys.stdout.isatty()


def launch_tui(
    status_tracker: StatusTracker | None = None,
    wt_manager: WorktreeManager | None = None,
    ab_launcher: ABLauncher | None = None,
    workspace_name: str | None = None,
    repo_path: str | None = None,
) -> None:
    """Launch the TUI application.

    Falls back to CLI mode if terminal is non-interactive.
    """
    if not is_interactive_terminal():
        from rich.console import Console

        console = Console()
        console.print(
            "[yellow]Non-interactive terminal detected. Use 'owt list' for CLI mode.[/yellow]"
        )
        return

    config = load_config()
    OrchestratorApp.CSS = build_css(config.ui.theme)

    app = OrchestratorApp(
        status_tracker=status_tracker,
        wt_manager=wt_manager,
        ab_launcher=ab_launcher,
        workspace_name=workspace_name,
        repo_path=repo_path,
    )
    app.run()


__all__ = [
    "OrchestratorApp",
    "is_interactive_terminal",
    "launch_tui",
]

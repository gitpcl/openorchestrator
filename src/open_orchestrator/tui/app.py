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
from textual.theme import Theme
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
    ThemePickerPanel,
)

logger = logging.getLogger(__name__)

# Theme prefix for Textual theme registration
_THEME_PREFIX = "owt-"

# Shared base colors used in TCSS_TEMPLATE and Textual themes
_BG_PRIMARY = "#1c1c1c"
_BG_SECONDARY = "#262626"
_BG_HOVER = "#2a2a2a"
_FG_PRIMARY = "#e0e0e0"
_FG_DIM = "#6c6c6c"

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
AGENT_TAGS: dict[AITool, str] = {
    AITool.CLAUDE: "cc",
    AITool.OPENCODE: "oc",
    AITool.DROID: "dr",
    AITool.CODEX: "cx",
    AITool.GEMINI_CLI: "gc",
    AITool.AIDER: "ai",
    AITool.AMP: "am",
    AITool.KILO_CODE: "kc",
}


TCSS_TEMPLATE = f"""\
/* OrchestratorApp TUI Styles — themed */

Screen {{{{
    background: {_BG_PRIMARY};
}}}}

#sidebar {{{{
    width: 100%;
    height: 1fr;
    background: {_BG_PRIMARY};
}}}}

#sidebar-title {{{{
    width: 1fr;
    height: 1;
    background: {{accent}};
    color: {_BG_PRIMARY};
    text-align: center;
    text-style: bold;
}}}}

PaneListWidget {{{{
    height: 1fr;
    border: none;
}}}}

PaneListWidget DataTable {{{{
    height: 100%;
    background: {_BG_PRIMARY};
}}}}

DataTable > .datatable--cursor {{{{
    background: {{cursor_bg}};
    color: {{accent}};
}}}}

DataTable > .datatable--header {{{{
    height: 0;
}}}}

DataTable > .datatable--hover {{{{
    background: {_BG_HOVER};
}}}}

#status-bar {{{{
    width: 100%;
    height: 1;
    background: {_BG_SECONDARY};
    color: {_FG_DIM};
    padding: 0 1;
}}}}

Footer {{{{
    background: {_BG_SECONDARY};
}}}}

FooterKey {{{{
    background: {_BG_SECONDARY};
}}}}

ConfirmScreen {{{{
    align: center middle;
    background: rgba(0, 0, 0, 0.7);
}}}}

#confirm-dialog {{{{
    width: 100%;
    max-width: 50;
    height: auto;
    border: thick {{accent}};
    background: {_BG_PRIMARY};
    padding: 1 1;
}}}}

#confirm-message {{{{
    width: 100%;
    text-align: center;
    color: #ffffff;
    margin-bottom: 1;
}}}}

HelpOverlayScreen {{{{
    align: center middle;
    background: rgba(0, 0, 0, 0.7);
}}}}

#help-dialog {{{{
    width: 100%;
    max-width: 48;
    height: auto;
    border: thick {{accent}};
    background: {_BG_PRIMARY};
    padding: 1 1;
}}}}

#help-title {{{{
    width: 100%;
    text-align: center;
    text-style: bold;
    color: {{accent}};
    margin-bottom: 1;
}}}}

#help-content {{{{
    width: 100%;
    color: #d0d0d0;
}}}}

#help-footer {{{{
    width: 100%;
    text-align: center;
    margin-top: 1;
    color: {_FG_DIM};
}}}}

ToastRack {{{{
    align: center bottom;
    width: 100%;
    margin-bottom: 3;
}}}}

Toast {{{{
    width: 1fr;
    max-width: 100%;
    margin: 0 1 0 2;
    border-left: wide {{accent}};
}}}}

CommandPalette > Vertical {{{{
    margin-top: 0;
}}}}
"""


def build_css(theme_name: str) -> str:
    """Build CSS string from template using the given theme."""
    theme = THEMES.get(theme_name, THEMES["cyan"])
    return TCSS_TEMPLATE.format(accent=theme.accent, cursor_bg=theme.cursor_bg)


def _build_textual_themes() -> dict[str, Theme]:
    """Build Textual Theme objects from our THEMES dict.

    These control Textual's built-in widgets: command palette, key bindings
    overlay, scrollbars, focus rings, etc.
    """
    result: dict[str, Theme] = {}
    for name, colors in THEMES.items():
        theme_name = f"{_THEME_PREFIX}{name}"
        result[theme_name] = Theme(
            name=theme_name,
            primary=colors.accent,
            secondary=colors.cursor_bg,
            accent=colors.accent,
            foreground=_FG_PRIMARY,
            background=_BG_PRIMARY,
            surface=_BG_SECONDARY,
            panel=_BG_SECONDARY,
            dark=True,
        )
    return result


TEXTUAL_THEMES = _build_textual_themes()


def _build_pane_data(
    wt_manager: WorktreeManager, status_tracker: StatusTracker
) -> list[tuple[Any, Any]]:
    """Build pane data list from worktrees and status tracker.

    Returns a list of (WorktreeInfo, WorktreeAIStatus | None) tuples with
    the main worktree first (status=None).
    """
    worktrees = wt_manager.list_all()
    pane_data: list[tuple[Any, Any]] = []
    for wt in worktrees:
        if wt.is_main:
            pane_data.insert(0, (wt, None))
        else:
            status = status_tracker.get_status(wt.name)
            pane_data.append((wt, status))
    return pane_data


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
        self._main_name: str | None = None

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", width=2)      # status icon
        table.add_column("Pane", width=20) # pane name
        table.add_column("", width=5)      # agent tag
        table.show_header = False
        self.refresh_data()

    def refresh_data(self) -> None:
        """Refresh pane list from status tracker and worktree manager.

        Performs I/O inline — suitable for on_mount (runs once).
        For periodic refresh, use update_from_data() with pre-fetched data.
        """
        pane_data = _build_pane_data(self.wt_manager, self.status_tracker)
        self.update_from_data(pane_data)

    def update_from_data(self, pane_data: list[tuple[Any, Any]]) -> None:
        """Update the pane list from pre-fetched data (called on main thread).

        Args:
            pane_data: List of (WorktreeInfo, WorktreeAIStatus | None) tuples.
        """
        table = self.query_one(DataTable)
        cursor_row = table.cursor_row if table.row_count > 0 else 0
        table.clear()

        self._pane_names = []
        self._main_name = None

        for wt, wt_status in pane_data:
            if wt.is_main:
                self._main_name = wt.name
            self._pane_names.append(wt.name)

            if wt.is_main:
                # Orchestrator pane — distinct styling
                status_icon = Text("\u25c6", style="#ffaf00")  # ◆ yellow diamond
                pane_name = Text("orchestrator", style="bold")
                agent_tag = Text("")
            elif wt_status:
                icon_char, icon_color = STATUS_ICONS.get(
                    wt_status.activity_status,
                    ("\u25cc", "#6c6c6c"),
                )
                status_icon = Text(icon_char, style=icon_color)

                # Agent tag
                ai_tool_key = AITool(wt_status.ai_tool) if isinstance(wt_status.ai_tool, str) else wt_status.ai_tool
                tag = AGENT_TAGS.get(ai_tool_key, "")
                agent_tag = Text(f"[{tag}]", style="#6c6c6c") if tag else Text("")
                pane_name = Text(wt.name[:20])
            else:
                status_icon = Text("\u25cc", style="#6c6c6c")
                agent_tag = Text("")
                pane_name = Text(wt.name[:20])

            table.add_row(status_icon, pane_name, agent_tag)

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

    def is_main_pane(self, name: str) -> bool:
        """Check if the given pane name is the main orchestrator pane."""
        return name == self._main_name


class StatusBarWidget(Static):
    """Compact status bar at bottom of sidebar showing active/total counts."""

    def __init__(self, status_tracker: StatusTracker, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.status_tracker = status_tracker

    def refresh_data(self, pane_names: list[str], summary: Any | None = None) -> None:
        """Refresh status bar counts.

        Args:
            pane_names: List of pane names (used for total count).
            summary: Pre-fetched StatusSummary. If None, fetches inline (I/O).
        """
        if summary is None:
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

    @property
    def available_themes(self) -> dict[str, Theme]:
        """Only expose owt themes, hiding Textual's built-in themes."""
        owt = {k: v for k, v in self._registered_themes.items() if k.startswith(_THEME_PREFIX)}
        # Before on_mount registers our themes, fall back to all registered
        # so Textual's __init__ can resolve its default theme.
        return owt if owt else self._registered_themes

    def search_themes(self) -> None:
        """Override to use our theme picker instead of Textual's."""
        self.action_settings()

    def __init__(
        self,
        status_tracker: StatusTracker | None = None,
        wt_manager: WorktreeManager | None = None,
        ab_launcher: ABLauncher | None = None,
        workspace_name: str | None = None,
        repo_path: str | None = None,
        theme_name: str = "cyan",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.status_tracker = status_tracker or StatusTracker()
        self.wt_manager = wt_manager or WorktreeManager()
        self.ab_launcher = ab_launcher or ABLauncher()
        self.workspace_name = workspace_name or os.environ.get("OWT_WORKSPACE", "")
        self.repo_path = repo_path or os.environ.get("OWT_REPO", "")
        self._refresh_interval: float = 2.0
        self._owt_theme_name = theme_name
        self._pending_session_kill: str | None = None

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
        # Register owt themes and apply the configured one
        for textual_theme in TEXTUAL_THEMES.values():
            self.register_theme(textual_theme)
        self.theme = f"{_THEME_PREFIX}{self._owt_theme_name}"

        self.set_interval(self._refresh_interval, self._refresh_ui)

    def _refresh_ui(self) -> None:
        """Trigger a background data fetch for UI refresh."""
        self._fetch_refresh_data()

    @work(thread=True, exclusive=True, group="refresh")
    def _fetch_refresh_data(self) -> None:
        """Fetch data in background thread, then update UI on main thread."""
        try:
            # Re-read status from disk so we pick up changes from other processes
            self.status_tracker.reload()
            pane_data = _build_pane_data(self.wt_manager, self.status_tracker)

            pane_names = [wt.name for wt, _ in pane_data if not wt.is_main]
            summary = self.status_tracker.get_summary(pane_names)
            self.call_from_thread(self._apply_refresh_data, pane_data, summary)
        except Exception:
            logger.debug("Failed to fetch refresh data", exc_info=True)

    def _apply_refresh_data(self, pane_data: list[tuple[Any, Any]], summary: Any) -> None:
        """Apply pre-fetched data to widgets (runs on main thread)."""
        try:
            pane_list = self.query_one("#pane-list", PaneListWidget)
            pane_list.update_from_data(pane_data)

            pane_names = [wt.name for wt, _ in pane_data]
            status_bar = self.query_one("#status-bar", StatusBarWidget)
            status_bar.refresh_data(pane_names, summary=summary)
        except Exception:
            logger.debug("Failed to apply refresh data", exc_info=True)

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

        pane_list = self.query_one("#pane-list", PaneListWidget)
        if pane_list.is_main_pane(selected):
            self.notify("Cannot close orchestrator pane", severity="warning")
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

        pane_list = self.query_one("#pane-list", PaneListWidget)
        if pane_list.is_main_pane(selected):
            self.notify("Cannot merge orchestrator pane", severity="warning")
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
        """Toggle the theme picker side panel."""
        existing = self.screen.query(ThemePickerPanel)
        if existing:
            existing.first().remove()
        else:
            self.screen.mount(ThemePickerPanel())

    def _dismiss_theme_panel(self) -> None:
        """Remove the theme picker panel if open."""
        existing = self.screen.query(ThemePickerPanel)
        if existing:
            existing.first().remove()

    def on_theme_picker_panel_theme_selected(
        self, event: ThemePickerPanel.ThemeSelected
    ) -> None:
        """Handle theme selection from the side panel."""
        theme_name = event.theme_name
        theme = THEMES.get(theme_name)
        if theme is None:
            return

        # Switch Textual's theme (controls palette, keys overlay, scrollbars)
        self.theme = f"{_THEME_PREFIX}{theme_name}"

        # Regenerate and reload custom CSS with new accent/cursor colors
        new_css = build_css(theme_name)
        OrchestratorApp.CSS = new_css
        # Update the cached source in the stylesheet so reparse picks it up
        for key, (css, is_defaults, tie_breaker, scope) in list(
            self.stylesheet.source.items()
        ):
            if isinstance(key, tuple) and "OrchestratorApp.CSS" in str(key):
                self.stylesheet.source[key] = (
                    new_css,
                    is_defaults,
                    tie_breaker,
                    scope,
                )
                break
        self.refresh_css(animate=False)

        # Persist to config file (must happen before install_status_bar
        # so get_active_theme() reads the new value)
        try:
            config = load_config()
            config.ui.theme = theme_name
            save_config(config, get_default_config_path())
        except Exception:
            logger.warning("Failed to save theme preference", exc_info=True)

        # Update tmux status bar and pane borders to match new theme
        if self.workspace_name:
            tmux = TmuxManager()
            tmux.install_status_bar(self.workspace_name)
            tmux._run_tmux_cmd("refresh-client", "-S")

        self.notify(f"Theme: {theme_name}")
        self._dismiss_theme_panel()

    # ── Command Palette ─────────────────────────────────────

    def action_command_palette(self) -> None:
        """Override to dismiss the help panel before opening the palette."""
        from textual.command import CommandPalette

        if CommandPalette.is_open(self):
            return

        # Close the help/keys panel if it's open so it doesn't stack
        if self.screen.query("HelpPanel"):
            self.action_hide_help_panel()

        self.push_screen(CommandPalette())

    # ── Help & Quit ─────────────────────────────────────────

    def action_show_help(self) -> None:
        """Show keybinding help overlay."""
        self.push_screen(HelpOverlayScreen())

    def action_quit(self) -> None:
        """Override Textual's built-in quit to go through our cleanup."""
        self.action_quit_tui()

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
        """Exit Textual first, then kill the tmux session on unmount."""
        self._pending_session_kill = self.workspace_name
        self.exit()

    def on_unmount(self) -> None:
        """Called after Textual has restored the terminal. Kill tmux session now."""
        if self._pending_session_kill:
            TmuxManager._run_tmux_cmd("kill-session", "-t", self._pending_session_kill)


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
    theme_name = config.ui.theme
    OrchestratorApp.CSS = build_css(theme_name)

    app = OrchestratorApp(
        status_tracker=status_tracker,
        wt_manager=wt_manager,
        ab_launcher=ab_launcher,
        workspace_name=workspace_name,
        repo_path=repo_path,
        theme_name=theme_name,
    )
    app.run()


__all__ = [
    "OrchestratorApp",
    "is_interactive_terminal",
    "launch_tui",
]

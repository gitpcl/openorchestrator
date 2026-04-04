"""
Switchboard: Textual-based card grid for multi-agent orchestration.

The switchboard is the command center for Open Orchestrator. It displays
all active worktrees as cards with status lights, and provides keyboard
shortcuts to navigate, patch into sessions, send messages, and manage
worktrees — all from one screen.

The switchboard runs in its own tmux session ("owt-switchboard"). When
you patch into an agent session (Enter), the switchboard stays alive.
Alt+s from any session switches back to the switchboard. Press q to
exit completely back to the terminal.

Metaphor: Like a telephone switchboard operator managing multiple lines.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess

from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Static

from open_orchestrator.config import AITool
from open_orchestrator.core.status import StatusTracker, runtime_status_config
from open_orchestrator.core.switchboard_cards import (
    _ALLOW_PROMPT_RE,  # noqa: F401
    _BLOCKED_RE,  # noqa: F401
    _INTERRUPTED_RE,  # noqa: F401
    _PROMPT_RE,  # noqa: F401
    _STATUS_BAR_RE,  # noqa: F401
    _TOOL_HEADER_RE,  # noqa: F401
    CARD_WIDTH,
    HEAVY_EVERY,
    HOOK_CAPABLE_TOOLS,  # noqa: F401
    HOOK_TRUST_MAX_SECONDS,  # noqa: F401
    SHELL_TIMEOUT,
    TICK_MS,
    Card,
    _build_cards,
    _build_cards_async,
    _detect_pane_status,  # noqa: F401
    _format_elapsed,
    _render_card,
)
from open_orchestrator.core.switchboard_modals import (
    ConfirmModal,
    DetailModal,
    InputModal,
    SearchableSelectModal,
    SelectOption,
)
from open_orchestrator.core.switchboard_tmux import launch_switchboard  # noqa: F401
from open_orchestrator.core.theme import COLORS, STATUS_COLORS
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

logger = logging.getLogger(__name__)

# Re-export all public names for backward compatibility
__all__ = [
    "Card",
    "CardGrid",
    "CardWidget",
    "ConfirmModal",
    "DetailModal",
    "HOOK_CAPABLE_TOOLS",
    "HOOK_TRUST_MAX_SECONDS",
    "InputModal",
    "SearchableSelectModal",
    "SelectOption",
    "SwitchboardApp",
    "_ALLOW_PROMPT_RE",
    "_BLOCKED_RE",
    "_INTERRUPTED_RE",
    "_PROMPT_RE",
    "_STATUS_BAR_RE",
    "_TOOL_HEADER_RE",
    "_build_cards",
    "_detect_pane_status",
    "launch_switchboard",
]


# ---------------------------------------------------------------------------
# Card widgets (tightly coupled to SwitchboardApp)
# ---------------------------------------------------------------------------


class CardWidget(Static):
    """Textual widget for a single worktree card with independent rendering."""

    DEFAULT_CSS = f"""
    CardWidget {{
        width: {CARD_WIDTH + 4};
        height: 5;
    }}
    """

    def __init__(self, card: Card, index: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.card = card
        self.card_index = index

    def render(self) -> Panel:
        app: SwitchboardApp = self.app  # type: ignore[assignment]
        selected = self.card_index == app._selected
        content = _render_card(self.card, app._tick)
        flash_remaining = self.card.flash_until - app._tick
        if flash_remaining > 3:
            border_style = "bold white reverse"
        elif flash_remaining > 0:
            border_style = "bold white"
        elif selected:
            border_style = "white"
        else:
            border_style = COLORS["card_border"]
        return Panel(
            content,
            width=CARD_WIDTH + 2,
            padding=(0, 1),
            border_style=border_style,
        )


class CardGrid(Static):
    """Widget that renders all cards in a wrapping grid via Rich Columns.

    Uses CardWidget instances for granular updates when possible,
    but falls back to bulk Columns rendering for the grid layout.
    """

    DEFAULT_CSS = """
    CardGrid {
        width: 100%;
        height: 1fr;
        overflow-y: auto;
        padding: 1 1;
    }
    """

    def render(self) -> Text | Columns:
        app: SwitchboardApp = self.app  # type: ignore[assignment]
        if not app._cards:
            return Text.from_markup(
                "\n\n\n"
                "  [bold white]Open Orchestrator[/bold white]\n\n"
                "  No active worktrees.\n\n"
                "  [dim]Press [bold]n[/bold] to create a new worktree[/dim]\n"
                "  [dim]Press [bold]q[/bold] to quit[/dim]\n"
            )

        panels = []
        for i, card in enumerate(app._cards):
            widget = CardWidget(card, i)
            widget._app = app  # type: ignore[attr-defined]
            panels.append(widget.render())

        return Columns(panels, padding=(0, 1))


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


class SwitchboardApp(App[None]):
    """Textual app replacing the curses switchboard."""

    CSS = f"""
    Screen {{
        layout: vertical;
        background: {COLORS["background"]};
    }}
    #header {{
        dock: top;
        width: 1fr;
        height: 1;
        layout: horizontal;
        background: {COLORS["header_bg"]};
        color: {COLORS["text_primary"]};
        text-style: bold;
        padding: 0 1;
    }}
    #header-title {{
        width: auto;
        height: 1;
    }}
    #header-stats {{
        width: 1fr;
        height: 1;
        text-align: right;
    }}
    #bottom-bar {{
        dock: bottom;
        width: 1fr;
        height: auto;
    }}
    #toast {{
        width: 1fr;
        height: 1;
        background: {COLORS["toast_info"]};
        color: {COLORS["text_primary"]};
        text-style: bold;
        display: none;
    }}
    #toast.visible {{
        display: block;
    }}
    #footer {{
        width: 1fr;
        height: 1;
        background: {COLORS["header_bg"]};
        color: {COLORS["text_secondary"]};
        padding: 0 1;
    }}
    """

    BINDINGS = [
        Binding("up", "navigate('up')", "Up", show=False),
        Binding("down", "navigate('down')", "Down", show=False),
        Binding("left", "navigate('left')", "Left", show=False),
        Binding("right", "navigate('right')", "Right", show=False),
        Binding("enter", "patch_in", "Patch in", show=False),
        Binding("s", "send_message", "Send", show=False),
        Binding("n", "new_worktree", "New", show=False),
        Binding("d", "delete_worktree", "Delete", show=False),
        Binding("S", "ship", "Ship", show=False),
        Binding("m", "merge", "Merge", show=False),
        Binding("f", "show_files", "Files", show=False),
        Binding("i", "show_info", "Info", show=False),
        Binding("a", "broadcast", "Broadcast", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    _FOOTER_KEYS = (
        "[bold]\u2191\u2193\u2190\u2192[/bold] [dim]nav[/dim] | "
        "[bold]Enter[/bold] [dim]patch[/dim] | "
        "[bold]s[/bold] [dim]send[/dim] | "
        "[bold]a[/bold] [dim]all[/dim] | "
        "[bold]n[/bold] [dim]new[/dim] | "
        "[bold]S[/bold] [dim]ship[/dim] | "
        "[bold]f[/bold] [dim]files[/dim] | "
        "[bold]i[/bold] [dim]info[/dim] | "
        "[bold]q[/bold] [dim]quit[/dim]"
    )

    def _build_footer(self) -> str:
        """Build dynamic footer with keybind hints and card position."""
        n = len(self._cards)
        pos = f"Card {self._selected + 1}/{n}" if n > 0 else "No cards"
        return f" {self._FOOTER_KEYS}  [bold]{pos}[/bold]"

    def __init__(self, detected_bg: str | None = None) -> None:
        super().__init__()
        self._detected_bg = detected_bg
        self._bg_color: str | None = None
        self._tracker = StatusTracker(runtime_status_config())
        self._tmux = TmuxManager()
        try:
            self._wt_manager: WorktreeManager | None = WorktreeManager()
        except Exception:
            logger.debug("Failed to initialise WorktreeManager", exc_info=True)
            self._wt_manager = None
        # Pre-build cards synchronously so the first render already has content
        # (eliminates "No active worktrees" flash). asyncio.run() is safe here
        # because Textual's event loop hasn't started yet.
        try:
            self._cards, self._file_map = _build_cards(self._tracker)
        except Exception:
            logger.debug("Failed to pre-build switchboard cards", exc_info=True)
            self._cards, self._file_map = [], {}
        # Pre-cache statuses so elapsed times render on first frame
        self._cached_statuses: dict[str, WorktreeAIStatus] = {s.worktree_name: s for s in self._tracker.get_all_statuses()}
        self._selected = 0
        self._tick = 0
        self._prev_statuses: dict[str, AIActivityStatus] = {}
        self._cols = 4
        self._heavy_refresh_count = 0
        self._last_generation = ""  # Change-detection token from StatusTracker
        # Transient state for new-worktree modal flow
        self._new_wt_task: str = ""
        self._new_wt_branch: str = ""
        self._new_wt_tool: AITool = AITool.CLAUDE

    def compose(self) -> ComposeResult:
        with Container(id="header"):
            yield Static(" SWITCHBOARD", id="header-title")
            yield Static(id="header-stats")
        yield CardGrid(id="card-grid")
        with Container(id="bottom-bar"):
            yield Static(id="toast")
            yield Static(self._build_footer(), id="footer")

    def on_mount(self) -> None:
        # Apply custom background color from env var or config
        self._apply_background_color()
        self.set_interval(TICK_MS / 1000.0, self._on_tick)
        self.set_interval(HEAVY_EVERY * TICK_MS / 1000.0, self._heavy_refresh)
        # Defer first refresh until size is known
        self.call_after_refresh(self._heavy_refresh)

    def _apply_background_color(self) -> None:
        """Apply terminal background color (auto-detected, env var, or config)."""
        import os

        bg = self._detected_bg
        if not bg:
            bg = os.environ.get("OWT_BACKGROUND")
        if not bg:
            try:
                from open_orchestrator.config import load_config

                bg = load_config().switchboard.background_color
            except Exception:
                pass
        if bg:
            from open_orchestrator.core.switchboard_modals import _darken

            self._bg_color = bg
            darker = _darken(bg, 0.75)
            self.screen.styles.background = bg
            # Header/footer slightly darker than bg
            try:
                self.query_one("#header").styles.background = darker
                self.query_one("#footer").styles.background = darker
            except Exception:
                pass

    def on_resize(self) -> None:
        """Recalculate columns for navigation on terminal resize."""
        self._cols = max(1, (self.size.width - 2) // (CARD_WIDTH + 4))

    def on_unmount(self) -> None:
        """Clean up resources on exit."""
        self._tracker.close()

    def _on_tick(self) -> None:
        """Fast tick: update spinners and elapsed times."""
        self._tick += 1
        # Re-compute elapsed from cached statuses (no I/O)
        for card in self._cards:
            wt_status = self._cached_statuses.get(card.name)
            if wt_status:
                card.elapsed = _format_elapsed(wt_status)
        self.query_one("#card-grid", CardGrid).refresh()

    async def _heavy_refresh(self) -> None:
        """Heavy refresh: parallel async pane + diff polling.

        Skips the expensive _build_cards_async when no status changes
        have been detected since the last refresh (change-detection guard).
        """
        self._heavy_refresh_count += 1

        # Change-detection: skip expensive rebuild if nothing changed
        if self._tracker.has_changed_since(self._last_generation):
            self._last_generation = self._tracker.get_generation()
        elif self._heavy_refresh_count > 1:
            # Still update elapsed times from cache but skip full rebuild
            logger.debug("Skipping heavy refresh — no status changes")
            self._update_header()
            self.query_one("#card-grid", CardGrid).refresh()
            return

        self._cards, self._file_map = await _build_cards_async(self._tracker, self._wt_manager)

        # Periodic orphan cleanup (~every 20s)
        if self._heavy_refresh_count % 10 == 0:
            valid_names = [c.name for c in self._cards]
            self._tracker.cleanup_orphans(valid_names)

        # Cache statuses for light-tick elapsed updates
        self._cached_statuses = {s.worktree_name: s for s in self._tracker.get_all_statuses()}

        # Flash on status transitions
        current_names = {c.name for c in self._cards}
        for name in list(self._prev_statuses):
            if name not in current_names:
                del self._prev_statuses[name]
        for card in self._cards:
            prev = self._prev_statuses.get(card.name)
            if prev is not None and prev != card.status:
                card.flash_until = self._tick + 5
            self._prev_statuses[card.name] = card.status

        # Clamp selection
        if self._cards:
            self._selected = min(self._selected, len(self._cards) - 1)

        self._update_header()
        self._update_footer()
        self.query_one("#card-grid", CardGrid).refresh()

    def _update_header(self) -> None:
        """Update the header stats (right side). Title is static."""
        cards = self._cards
        active = sum(1 for c in cards if c.status == AIActivityStatus.WORKING)
        waiting = sum(1 for c in cards if c.status in (AIActivityStatus.WAITING, AIActivityStatus.BLOCKED))
        total = len(cards)
        idle = total - active - waiting
        overlaps = sum(1 for c in cards if c.overlap_count > 0)

        working_color = STATUS_COLORS["working"]
        waiting_color = STATUS_COLORS["waiting"]
        idle_color = STATUS_COLORS["idle"]

        parts = [
            f"{total} lines",
            f"[{working_color}]\u25cf{active} active[/{working_color}]",
        ]
        if waiting:
            parts.append(f"[{waiting_color}]\u26a0{waiting} waiting[/{waiting_color}]")
        parts.append(f"[{idle_color}]\u25cb{idle}[/{idle_color}]")
        if overlaps:
            parts.append(f"[yellow]!{overlaps} overlap[/yellow]")

        # DAG progress indicator
        try:
            dag_progress = self._tracker.get_metadata("dag_progress")
            if dag_progress:
                parts.append(f"DAG: {dag_progress}")
        except Exception:
            logger.debug("Failed to read DAG progress", exc_info=True)

        stats = "  ".join(parts) + " "
        self.query_one("#header-stats", Static).update(stats)

    # ---- Actions ----

    def action_navigate(self, direction: str) -> None:
        if not self._cards:
            return
        n = len(self._cards)
        if direction == "up":
            self._selected = max(self._selected - self._cols, 0)
        elif direction == "down":
            self._selected = min(self._selected + self._cols, n - 1)
        elif direction == "left":
            self._selected = max(self._selected - 1, 0)
        elif direction == "right":
            self._selected = min(self._selected + 1, n - 1)
        self.query_one("#card-grid", CardGrid).refresh()
        self._update_footer()

    def _update_footer(self) -> None:
        """Refresh the footer bar with current card position."""
        self.query_one("#footer", Static).update(self._build_footer())

    def _show_toast(self, message: str, variant: str = "info") -> None:
        """Show a temporary toast bar above the footer."""
        toast = self.query_one("#toast", Static)
        color = COLORS.get(f"toast_{variant}", COLORS["toast_info"])
        toast.update(Text(f" {message}"))
        toast.styles.background = color
        toast.styles.color = "#ffffff" if variant in ("error", "warning") else COLORS["text_primary"]
        toast.add_class("visible")
        self.set_timer(3.0 if variant != "error" else 5.0, self._hide_toast)

    def _hide_toast(self) -> None:
        toast = self.query_one("#toast", Static)
        toast.remove_class("visible")

    def action_patch_in(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]
        if not card.tmux_session or not self._tmux.session_exists(card.tmux_session):

            def _on_delete_confirm(yes: bool | None) -> None:
                if yes:
                    self.run_worker(
                        self._run_shell_bg(
                            ["owt", "delete", card.name, "--yes"],
                            f"Deleting '{card.name}'...",
                            clamp=True,
                        )
                    )
                else:

                    def _on_recreate(yes2: bool | None) -> None:
                        if yes2:
                            self.run_worker(
                                self._run_shell_bg(
                                    ["owt", "new", card.branch, "--yes"],
                                    f"Recreating '{card.name}'...",
                                )
                            )

                    self.push_screen(ConfirmModal(f"Recreate session for '{card.name}'?"), _on_recreate)

            self.push_screen(ConfirmModal(f"No session for '{card.name}'. Delete stale worktree?"), _on_delete_confirm)
            return
        self._tmux.switch_client(card.tmux_session)

    def action_send_message(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]

        def _on_input(msg: str | None) -> None:
            if msg and card.tmux_session:
                try:
                    self._tmux.send_keys_to_pane(card.tmux_session, msg)
                except Exception:
                    logger.debug("Failed to send to %s", card.name, exc_info=True)

        self.push_screen(InputModal(f"Send to {card.name}: "), _on_input)

    def action_new_worktree(self) -> None:
        def _on_input(task: str | None) -> None:
            if not task:
                return
            self._new_wt_task = task
            from open_orchestrator.core.branch_namer import generate_branch_name

            try:
                branch = generate_branch_name(task)
            except ValueError:
                branch = task.lower().replace(" ", "-")[:40]
            self._new_wt_branch = branch
            self.push_screen(
                ConfirmModal(f"Task: {task}\nBranch: {branch}\n\nProceed?"),
                self._on_new_confirm,
            )

        self.push_screen(InputModal("Task description:"), _on_input)

    def _on_new_confirm(self, yes: bool | None) -> None:
        if not yes:
            return
        from open_orchestrator.core.agent_detector import detect_installed_agents

        installed = detect_installed_agents()
        if len(installed) == 0:
            self._show_toast("No AI tools found (claude, opencode, droid)", variant="error")
            return
        elif len(installed) == 1:
            self._new_wt_tool = installed[0]
            self._do_create_worktree()
        else:
            options = [SelectOption(value=t.value, label=t.value, category="Detected") for t in installed]

            def _on_tool(tool_value: str | None) -> None:
                if not tool_value:
                    return
                self._new_wt_tool = AITool(tool_value)
                self._do_create_worktree()

            self.push_screen(SearchableSelectModal("Select AI tool", options), _on_tool)

    async def _run_shell_bg(self, cmd: list[str], toast_msg: str, *, clamp: bool = False) -> None:
        """Run a shell command in the background without suspending the UI."""
        self._show_toast(toast_msg)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SHELL_TIMEOUT)
            if proc.returncode == 0:
                self._show_toast(f"Done: {' '.join(cmd[:3])}", variant="success")
            else:
                err = stderr.decode(errors="replace").strip().split("\n")[-1] if stderr else "unknown error"
                self._show_toast(f"Failed: {err}", variant="error")
        except asyncio.TimeoutError:
            self._show_toast("Command timed out", variant="error")
        except Exception as exc:
            self._show_toast(f"Error: {exc}", variant="error")

        if clamp:
            self._selected = min(self._selected, max(0, len(self._cards) - 2))
        await self._heavy_refresh()

    def _do_create_worktree(self) -> None:
        """Create worktree via owt new subprocess."""
        task = self._new_wt_task
        ai_tool = self._new_wt_tool
        cmd = ["owt", "new", task, "--yes", "--ai-tool", ai_tool.value]
        self.run_worker(self._run_shell_bg(cmd, "Creating worktree..."))

    def _confirm_and_shell(self, prompt: str, cmd: list[str]) -> None:
        """Confirm, shell out in background, then clamp selection."""
        if not self._cards:
            return

        def _on_confirm(yes: bool | None) -> None:
            if yes:
                self.run_worker(self._run_shell_bg(cmd, f"Running: {' '.join(cmd[:3])}...", clamp=True))

        self.push_screen(ConfirmModal(prompt), _on_confirm)

    def action_delete_worktree(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]
        self._confirm_and_shell(f"Delete '{card.name}'?", ["owt", "delete", card.name, "--yes"])

    def action_ship(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]
        self._confirm_and_shell(f"Ship '{card.name}'? (commit+merge+delete)", ["owt", "ship", card.name, "--yes"])

    def action_merge(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]

        completed = [c for c in self._cards if c.status == AIActivityStatus.COMPLETED]
        if len(completed) > 1 and card.status != AIActivityStatus.COMPLETED:
            options = [
                SelectOption(value=c.name, label=c.name, description=c.diff_stat or "", category="Completed") for c in completed
            ]

            def _on_pick(name: str | None) -> None:
                if name:
                    self._confirm_and_shell(f"Merge '{name}'?", ["owt", "merge", name])

            self.push_screen(SearchableSelectModal("Merge which worktree?", options), _on_pick)
            return

        self._confirm_and_shell(f"Merge '{card.name}'?", ["owt", "merge", card.name])

    def action_show_files(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]
        if card.overlap_count <= 0 or not card.overlap_names:
            return

        my_files = set(self._file_map.get(card.name, []))
        overlap_files: dict[str, list[str]] = {}
        for other_name, other_files_list in self._file_map.items():
            if other_name == card.name:
                continue
            for f in my_files & set(other_files_list):
                overlap_files.setdefault(f, []).append(other_name)

        lines = [f"  {f_path} \u2190 {', '.join(wt_names)}" for f_path, wt_names in sorted(overlap_files.items())]
        self.push_screen(DetailModal(f"Overlap: {card.name} ({card.overlap_count} files)", lines))

    async def action_show_info(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]
        status = self._tracker.get_status(card.name)
        wt_path = status.worktree_path if status else ""

        commits: list[str] = []
        if wt_path:
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["git", "log", "--oneline", "-5"],
                    capture_output=True,
                    text=True,
                    cwd=wt_path,
                    timeout=5,
                )
                if result.returncode == 0:
                    commits = [line for line in result.stdout.strip().split("\n") if line]
            except (subprocess.TimeoutExpired, OSError):
                pass

        lines = [
            f"  Branch: {card.branch}",
            f"  Status: {card.status.value}  Elapsed: {card.elapsed}",
            f"  AI: {card.ai_tool}  Diff: {card.diff_stat or 'n/a'}",
            f"  Task: {card.task or chr(8212)}",
            "",
            "  Recent commits:",
        ]
        for c in commits[:5]:
            lines.append(f"    {c}")
        if card.overlap_count > 0:
            lines.append("")
            lines.append(f"  \u26a0 {card.overlap_count} file(s) overlap with: {', '.join(card.overlap_names or [])}")

        self.push_screen(DetailModal(f"Detail: {card.name}", lines))

    def action_broadcast(self) -> None:
        if not self._cards:
            return

        def _on_input(msg: str | None) -> None:
            if msg:
                for card in self._cards:
                    if card.tmux_session:
                        try:
                            self._tmux.send_keys_to_pane(card.tmux_session, msg)
                            self._tracker.record_command(card.name, msg)
                        except Exception:
                            logger.debug("Failed to broadcast to %s", card.name, exc_info=True)

        self.push_screen(InputModal("Broadcast to all: "), _on_input)

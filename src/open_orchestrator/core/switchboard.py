"""Legacy card-grid switchboard (kept behind ``--legacy-cards``).

Sprint 024 introduced the control plane as the default UI
(:mod:`open_orchestrator.core.control_plane_view`).  This module remains
for users on the deprecated ``--legacy-cards`` migration path — it will
be removed in the next minor release.
"""

from __future__ import annotations

import asyncio
import logging

from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Static

from open_orchestrator.core import status_policy
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

__all__ = [
    "Card",
    "CardGrid",
    "ConfirmModal",
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
# Card grid (legacy single-pass renderer; per-card CardWidget removed in S024)
# ---------------------------------------------------------------------------


class CardGrid(Static):
    """Single-pass card-grid widget (legacy view)."""

    DEFAULT_CSS = """
    CardGrid { width: 100%; height: 1fr; overflow-y: auto; padding: 1 1; }
    """

    def render(self) -> Text | Columns:
        app: SwitchboardApp = self.app  # type: ignore[assignment]
        if not app._cards:
            return Text.from_markup(
                "\n\n\n"
                "  [bold white]Open Orchestrator (legacy view)[/bold white]\n\n"
                "  No active worktrees.\n\n"
                "  [dim]Press [bold]n[/bold] for new, [bold]q[/bold] to quit[/dim]\n"
                "  [yellow dim]Drop --legacy-cards for the control plane.[/yellow dim]\n"
            )
        panels = []
        for i, card in enumerate(app._cards):
            selected = i == app._selected
            border = "white" if selected else COLORS["card_border"]
            panels.append(
                Panel(
                    _render_card(card, app._tick),
                    width=CARD_WIDTH + 2,
                    padding=(0, 1),
                    border_style=border,
                )
            )
        return Columns(panels, padding=(0, 1))


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


class SwitchboardApp(App[None]):
    """Textual app replacing the curses switchboard."""

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }
    #header {
        dock: top;
        width: 1fr;
        height: 1;
        layout: horizontal;
        background: $panel;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    #header-title {
        width: auto;
        height: 1;
    }
    #header-stats {
        width: 1fr;
        height: 1;
        text-align: right;
    }
    #bottom-bar {
        dock: bottom;
        width: 1fr;
        height: auto;
    }
    #toast {
        width: 1fr;
        height: 1;
        background: $primary;
        color: $text;
        text-style: bold;
        display: none;
    }
    #toast.visible {
        display: block;
    }
    #footer {
        width: 1fr;
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
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

    _FOOTER = (
        " [yellow]legacy[/yellow] \u00b7 [bold]\u2191\u2193\u2190\u2192[/bold] nav \u00b7 "
        "[bold]Enter[/bold] patch \u00b7 [bold]n[/bold] new \u00b7 "
        "[bold]S[/bold] ship \u00b7 [bold]q[/bold] quit"
    )

    def _build_footer(self) -> str:
        return self._FOOTER

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
        self._new_wt_tool: str = "claude"
        self._new_wt_session_type: str = "worktree"

    def compose(self) -> ComposeResult:
        with Container(id="header"):
            yield Static(" SWITCHBOARD", id="header-title")
            yield Static(id="header-stats")
        yield CardGrid(id="card-grid")
        with Container(id="bottom-bar"):
            yield Static(id="toast")
            yield Static(self._build_footer(), id="footer")

    def on_mount(self) -> None:
        # Apply the active theme palette before the first frame renders
        self._apply_theme()
        # Apply custom background color from env var or config
        self._apply_background_color()
        self.set_interval(TICK_MS / 1000.0, self._on_tick)
        self.set_interval(HEAVY_EVERY * TICK_MS / 1000.0, self._heavy_refresh)
        # Defer first refresh until size is known
        self.call_after_refresh(self._heavy_refresh)

    def _apply_theme(self) -> None:
        try:
            from open_orchestrator.core.theme import get_active_palette

            self.theme = get_active_palette().textual_theme
        except Exception:
            logger.debug("Theme apply skipped", exc_info=True)

    def _apply_background_color(self) -> None:
        """Legacy view skips custom backgrounds; control plane handles theming."""
        return

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
        """Parallel async pane + diff polling (legacy)."""
        self._heavy_refresh_count += 1
        if not self._tracker.has_changed_since(self._last_generation) and self._heavy_refresh_count > 1:
            self._update_header()
            self.query_one("#card-grid", CardGrid).refresh()
            return
        self._last_generation = self._tracker.get_generation()

        self._cards, self._file_map = await _build_cards_async(self._tracker, self._wt_manager)
        if self._heavy_refresh_count % 10 == 0:
            self._tracker.cleanup_orphans([c.name for c in self._cards])
        self._cached_statuses = {s.worktree_name: s for s in self._tracker.get_all_statuses()}
        if self._cards:
            self._selected = min(self._selected, len(self._cards) - 1)
        self._update_header()
        self.query_one("#card-grid", CardGrid).refresh()

    def _update_header(self) -> None:
        cards = self._cards
        active = sum(1 for c in cards if status_policy.ui_bucket(c.status) == "active")
        waiting = sum(1 for c in cards if status_policy.ui_bucket(c.status) == "waiting")
        total = len(cards)
        idle = total - active - waiting
        working_color = STATUS_COLORS["working"]
        idle_color = STATUS_COLORS["idle"]
        parts = [f"{total}", f"[{working_color}]\u25cf{active}[/{working_color}]", f"[{idle_color}]\u25cb{idle}[/{idle_color}]"]
        if waiting:
            parts.append(f"[{STATUS_COLORS['waiting']}]\u26a0{waiting}[/{STATUS_COLORS['waiting']}]")
        self.query_one("#header-stats", Static).update("  ".join(parts) + " ")

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

    def _show_toast(self, message: str, variant: str = "info") -> None:
        toast = self.query_one("#toast", Static)
        color = COLORS.get(f"toast_{variant}", COLORS["toast_info"])
        toast.update(Text(f" {message}"))
        toast.styles.background = color
        toast.styles.color = "#ffffff" if variant in ("error", "warning") else COLORS["text_primary"]
        toast.add_class("visible")
        self.set_timer(3.0 if variant != "error" else 5.0, lambda: toast.remove_class("visible"))

    def action_patch_in(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]
        if not card.tmux_session or not self._tmux.session_exists(card.tmux_session):
            self._show_toast(f"No tmux session for '{card.name}'", variant="warning")
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
        if not installed:
            self._show_toast("No AI tools found (claude, opencode, droid)", variant="error")
            return
        self._new_wt_tool = installed[0]
        self._do_create_worktree()

    async def _run_shell_bg(self, cmd: list[str], toast_msg: str, *, clamp: bool = False) -> None:
        """Run a shell command in the background without suspending the UI."""
        self._show_toast(toast_msg)
        try:
            cwd = str(self._wt_manager.git_root) if self._wt_manager else None
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
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
        cmd = ["owt", "new", task, "--yes", "--ai-tool", ai_tool]
        if self._new_wt_session_type == "branch":
            cmd.append("--in-place")
            toast = "Creating branch session..."
        else:
            toast = "Creating worktree..."
        self.run_worker(self._run_shell_bg(cmd, toast))

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
        self._confirm_and_shell(f"Merge '{card.name}'?", ["owt", "merge", card.name])

    def action_show_files(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]
        if card.overlap_count <= 0 or not card.overlap_names:
            return
        self._show_toast(
            f"Overlap: {card.name} ({card.overlap_count} files) with {', '.join(card.overlap_names)}",
            variant="warning",
        )

    def action_show_info(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]
        info = (
            f"{card.name} \u00b7 {card.branch} \u00b7 {card.status.value} \u00b7 {card.ai_tool} \u00b7 {card.diff_stat or 'n/a'}"
        )
        self._show_toast(info, variant="info")

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

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
import collections.abc
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from open_orchestrator.config import AITool
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

# Status light characters and colors (Rich markup)
STATUS_LIGHTS: dict[str, tuple[str, str]] = {
    "working": ("\u25cf", "green"),       # ● green
    "idle": ("\u25cb", "white"),           # ○ white
    "blocked": ("\u26a0", "yellow"),       # ⚠ yellow
    "waiting": ("\u26a0", "yellow"),       # ⚠ yellow
    "completed": ("\u2713", "cyan"),       # ✓ cyan
    "error": ("\u25cf", "red"),            # ● red
    "unknown": ("?", "white"),
}

CARD_WIDTH = 30
CARD_HEIGHT = 6
SWITCHBOARD_SESSION = "owt-switchboard"
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧"
TICK_MS = 200
HEAVY_EVERY = 10  # _build_cards runs every HEAVY_EVERY ticks (2s)
RECHECKABLE_STATUSES = {AIActivityStatus.WORKING, AIActivityStatus.WAITING, AIActivityStatus.BLOCKED, AIActivityStatus.IDLE}
HOOK_FRESHNESS_SECONDS = 10  # Trust hook-set status if updated within this window
HOOK_CAPABLE_TOOLS = {AITool.CLAUDE.value, AITool.DROID.value}  # Scraper must not downgrade WORKING → WAITING
HOOK_TRUST_MAX_SECONDS = 300  # After 5 min with no hook update, let scraper recover stale WORKING

# Pre-compiled regex patterns for pane status detection
# Must match actual permission prompts, NOT agent thinking text like "Allow me to..."
_BLOCKED_RE = re.compile(
    r"\(y/N\)|\(Y/n\)|Do you want to proceed|Press Enter to continue",
    re.IGNORECASE,
)
# Stricter "Allow" check — only match "Allow <Tool>:" or "Allow <Tool> /" patterns
_ALLOW_PROMPT_RE = re.compile(
    r"Allow\s+(Read|Write|Edit|Bash|Glob|Grep|Agent|WebFetch|WebSearch|NotebookEdit|mcp_)",
)
# Lines to skip — Claude Code status bar is always visible and contains
# words like "permissions" that would false-trigger BLOCKED detection.
_STATUS_BAR_RE = re.compile(
    r"ctx:\s*\d+%|bypass permissions|shift\+tab|permissions\s+on",
    re.IGNORECASE,
)
_PROMPT_RE = re.compile(
    r"^>\s*$|^❯\s*$|What would you like|How can I help",
    re.IGNORECASE,
)
# High-confidence idle signal — agent was interrupted/stopped, never appears during thinking
_INTERRUPTED_RE = re.compile(
    r"Interrupted|What should Claude do instead",
    re.IGNORECASE,
)


@dataclass
class Card:
    """A switchboard card representing one worktree/agent."""

    name: str
    status: AIActivityStatus
    branch: str
    ai_tool: str
    task: str | None
    elapsed: str
    tmux_session: str | None
    flash_until: int = 0  # tick number until which to show A_REVERSE flash
    overlap_count: int = 0  # number of files overlapping with other worktrees
    overlap_names: list[str] | None = None  # worktree names with overlap
    diff_stat: str = ""  # e.g. "+142 -37"


def _format_elapsed(status: WorktreeAIStatus) -> str:
    """Format time elapsed since last update."""
    if not status.updated_at:
        return ""
    delta = datetime.now() - status.updated_at
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    hours = total_seconds // 3600
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def _detect_pane_status(tmux_session: str | None) -> tuple[AIActivityStatus, bool] | None:
    """Detect agent status by capturing tmux pane content.

    Returns (status, high_confidence) or None if inconclusive.
    high_confidence=True means the signal is unambiguous (e.g. "Interrupted" text)
    and should override hook trust guards.
    """
    if not tmux_session:
        return None
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", tmux_session, "-p", "-J"],
            capture_output=True, text=True, check=True, timeout=2,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    lines = result.stdout.rstrip("\n").split("\n")
    if not lines:
        return None

    # Get last non-empty lines for analysis
    tail = []
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            tail.append(stripped)
        if len(tail) >= 8:
            break
    if not tail:
        return None

    # Filter out Claude Code status bar lines before analysis
    content_lines = [line for line in tail if not _STATUS_BAR_RE.search(line)]
    content_text = "\n".join(reversed(content_lines)) if content_lines else ""

    if content_text and (_BLOCKED_RE.search(content_text) or _ALLOW_PROMPT_RE.search(content_text)):
        return AIActivityStatus.BLOCKED, True

    # Check for high-confidence idle signals (Interrupted, etc.)
    has_interrupted = bool(content_text and _INTERRUPTED_RE.search(content_text))

    # If idle prompt (❯) is visible → WAITING. Otherwise → WORKING.
    for line in content_lines[:5]:
        if _PROMPT_RE.search(line):
            return AIActivityStatus.WAITING, has_interrupted

    # No prompt visible — agent is doing something
    return AIActivityStatus.WORKING, False


def _get_diff_info(worktree_path: str, branch: str) -> tuple[list[str], str]:
    """Get modified files AND diff stat in a single git call.

    Uses `git diff --numstat` which yields both file names and line counts.
    Returns (modified_files, diff_stat_str).
    """
    if not os.path.isdir(worktree_path):
        return [], ""
    try:
        for base in ("main", "master", "develop"):
            result = subprocess.run(
                ["git", "diff", "--numstat", f"{base}...{branch}"],
                capture_output=True, text=True, cwd=worktree_path, timeout=5,
            )
            if result.returncode == 0:
                files: list[str] = []
                total_ins = 0
                total_dels = 0
                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t", 2)
                    if len(parts) >= 3:
                        ins, dels, name = parts
                        files.append(name)
                        if ins != "-":
                            total_ins += int(ins)
                        if dels != "-":
                            total_dels += int(dels)
                stat_parts = []
                if total_ins:
                    stat_parts.append(f"+{total_ins}")
                if total_dels:
                    stat_parts.append(f"-{total_dels}")
                return files, " ".join(stat_parts)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return [], ""


def _compute_overlaps(
    cards: list[Card], file_map: dict[str, list[str]],
) -> None:
    """Compute pairwise file overlaps and annotate cards."""
    for i, card in enumerate(cards):
        my_files = set(file_map.get(card.name, []))
        if not my_files:
            continue
        overlap_names = []
        overlap_files: set[str] = set()
        for j, other in enumerate(cards):
            if i == j:
                continue
            other_files = set(file_map.get(other.name, []))
            common = my_files & other_files
            if common:
                overlap_names.append(other.name)
                overlap_files |= common
        card.overlap_count = len(overlap_files)
        card.overlap_names = overlap_names if overlap_names else None


async def _build_cards_async(
    tracker: StatusTracker,
    wt_manager: WorktreeManager | None = None,
) -> tuple[list[Card], dict[str, list[str]]]:
    """Build card list from git worktrees enriched with status data.

    Uses git worktrees as the source of truth (same as ``owt list``),
    enriched with status DB data for activity tracking. Runs tmux pane
    captures and git diff calls concurrently via asyncio.to_thread.

    Returns (cards, file_map) where file_map maps worktree names to modified files.
    """
    # Git worktrees are the source of truth — same as `owt list`.
    # Fall back to status DB entries if git is unavailable (e.g. in tests).
    try:
        if wt_manager is None:
            wt_manager = WorktreeManager()
        worktrees = [wt for wt in wt_manager.list_all() if not wt.is_main]
    except Exception:
        worktrees = []

    status_map = {s.worktree_name: s for s in tracker.get_all_statuses()}

    if worktrees:
        # Merge: git worktrees enriched with status DB
        statuses: list[WorktreeAIStatus] = []
        for wt in worktrees:
            s = status_map.get(wt.name)
            if s is None:
                s = WorktreeAIStatus(
                    worktree_name=wt.name,
                    worktree_path=str(wt.path),
                    branch=wt.branch,
                    activity_status=AIActivityStatus.IDLE,
                )
            statuses.append(s)
    else:
        # Fallback: use status DB directly (test environments, no git repo)
        statuses = list(status_map.values())

    now = datetime.now()

    # Schedule parallel I/O: pane detection + diff info
    tasks: list[collections.abc.Awaitable[object]] = []
    task_meta: list[tuple[str, int]] = []  # ("pane"|"diff", status_index)

    for i, s in enumerate(statuses):
        recently_updated = (
            s.updated_at
            and (now - s.updated_at).total_seconds() < HOOK_FRESHNESS_SECONDS
        )
        if not recently_updated and s.activity_status in RECHECKABLE_STATUSES:
            tasks.append(asyncio.to_thread(_detect_pane_status, s.tmux_session))
            task_meta.append(("pane", i))

    for i, s in enumerate(statuses):
        if os.path.isdir(s.worktree_path):
            tasks.append(asyncio.to_thread(_get_diff_info, s.worktree_path, s.branch))
            task_meta.append(("diff", i))

    results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    pane_results: dict[int, tuple[AIActivityStatus, bool] | None] = {}
    diff_results: dict[int, tuple[list[str], str]] = {}
    for (kind, idx), result in zip(task_meta, results):
        if isinstance(result, BaseException):
            continue
        if kind == "pane":
            pane_results[idx] = result  # type: ignore[assignment]
        else:
            diff_results[idx] = result  # type: ignore[assignment]

    # Process results and build cards
    cards = []
    file_map: dict[str, list[str]] = {}

    for i, s in enumerate(statuses):
        # Apply pane detection
        detection = pane_results.get(i)
        if detection is not None:
            detected, high_confidence = detection
            if detected != s.activity_status:
                time_since_update = (now - s.updated_at).total_seconds() if s.updated_at else float("inf")
                hook_guarded = (
                    not high_confidence
                    and s.ai_tool in HOOK_CAPABLE_TOOLS
                    and s.activity_status == AIActivityStatus.WORKING
                    and detected == AIActivityStatus.WAITING
                    and time_since_update < HOOK_TRUST_MAX_SECONDS
                )
                if not hook_guarded:
                    s.activity_status = detected
                    s.updated_at = now
                    tracker.set_status(s)

        # Apply diff results
        diff_stat = ""
        if i in diff_results:
            mod_files, diff_stat = diff_results[i]
            if mod_files != s.modified_files:
                s.modified_files = mod_files
                tracker.set_status(s)
            file_map[s.worktree_name] = mod_files
        else:
            file_map[s.worktree_name] = s.modified_files

        cards.append(Card(
            name=s.worktree_name,
            status=s.activity_status,
            branch=s.branch,
            ai_tool=s.ai_tool,
            task=s.current_task,
            elapsed=_format_elapsed(s),
            tmux_session=s.tmux_session,
            diff_stat=diff_stat,
        ))

    _compute_overlaps(cards, file_map)
    return cards, file_map


def _build_cards(tracker: StatusTracker) -> tuple[list[Card], dict[str, list[str]]]:
    """Sync wrapper for _build_cards_async (used by tests)."""
    return asyncio.run(_build_cards_async(tracker))


# ---------------------------------------------------------------------------
# Textual UI
# ---------------------------------------------------------------------------

SHELL_TIMEOUT = 120  # seconds — max time for owt ship/merge/delete/new


def _render_card(card: Card, tick: int) -> str:
    """Render a card as Rich markup text."""
    w = CARD_WIDTH - 4  # inner width (minus border padding)
    status_key = card.status.value
    light_char, color = STATUS_LIGHTS.get(status_key, ("?", "white"))
    if card.status == AIActivityStatus.WORKING:
        light_char = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]

    name_trunc = card.name[:w]
    status_label = status_key.upper()
    elapsed_str = f"{card.elapsed:>5}" if card.elapsed else ""

    branch_short = card.branch.split("/")[-1] if "/" in card.branch else card.branch
    branch_trunc = branch_short[:w]

    tool_label = card.ai_tool[:12]
    diff_str = card.diff_stat or ""

    # Status line
    status_line = f"[{color} bold]{light_char}[/{color} bold] {status_label}"
    pad = w - len(status_label) - 2 - len(elapsed_str)
    status_line += " " * max(0, pad) + elapsed_str

    # Tool + diff line
    tool_pad = w - len(tool_label) - len(diff_str)
    tool_line = tool_label + " " * max(1, tool_pad) + f"[dim]{diff_str}[/dim]" if diff_str else tool_label

    # Task line
    if card.overlap_count > 0:
        overlap_tag = f"[yellow bold][! {card.overlap_count} overlap][/yellow bold]"
        task_part = ""
        if card.task:
            remaining = w - len(f"[! {card.overlap_count} overlap] ")
            task_part = " " + card.task[:max(0, remaining)]
        task_line = overlap_tag + task_part
    else:
        task_str = card.task or "\u2014"
        task_line = task_str[:w]

    return "\n".join([
        f"[bold]{name_trunc}[/bold]",
        status_line,
        branch_trunc,
        tool_line,
        task_line,
    ])


class InputModal(ModalScreen[str | None]):
    """Modal screen for text input (send, new, broadcast)."""

    DEFAULT_CSS = """
    InputModal {
        align: center middle;
    }
    #input-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: heavy $accent;
        background: $surface;
    }
    #input-dialog Label {
        margin-bottom: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Container(id="input-dialog"):
            yield Label(self._prompt)
            yield Input(id="modal-input")

    def on_mount(self) -> None:
        self.query_one("#modal-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if value else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Modal screen for y/N confirmation."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    #confirm-dialog {
        width: 50;
        height: auto;
        padding: 1 2;
        border: heavy $accent;
        background: $surface;
    }
    #confirm-dialog Label {
        margin-bottom: 1;
    }
    #confirm-dialog .buttons {
        layout: horizontal;
        height: auto;
    }
    #confirm-dialog .buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("y", "yes", "Yes", show=False),
        Binding("n", "no", "No", show=False),
        Binding("escape", "no", "Cancel", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm-dialog"):
            yield Label(self._message)
            with Container(classes="buttons"):
                yield Button("Yes (y)", id="yes", variant="error")
                yield Button("No (n)", id="no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class DetailModal(ModalScreen[None]):
    """Modal screen for detail panels (info, overlap)."""

    DEFAULT_CSS = """
    DetailModal {
        align: center middle;
    }
    #detail-panel {
        width: 70;
        max-height: 80%;
        padding: 1 2;
        border: heavy $accent;
        background: $surface;
        overflow-y: auto;
    }
    #detail-panel .modal-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #detail-panel .modal-hint {
        margin-top: 1;
        text-style: dim;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(self, title: str, lines: list[str]) -> None:
        super().__init__()
        self._title = title
        self._lines = lines

    def compose(self) -> ComposeResult:
        with Container(id="detail-panel"):
            yield Static(self._title, classes="modal-title")
            yield Static("\n".join(self._lines), classes="modal-body")
            yield Static("Press Escape to close", classes="modal-hint")

    def on_key(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class CardGrid(Static):
    """Single widget that renders all cards in a wrapping grid via Rich Columns."""

    DEFAULT_CSS = """
    CardGrid {
        width: 100%;
        height: 1fr;
        overflow-y: auto;
        padding: 1 1;
    }
    """

    def render(self) -> object:
        app: SwitchboardApp = self.app  # type: ignore[assignment]
        if not app._cards:
            return "No active worktrees. Press [bold][n][/bold] to create one, [bold][q][/bold] to quit."

        panels = []
        for i, card in enumerate(app._cards):
            selected = i == app._selected
            flashing = app._tick < card.flash_until
            content = _render_card(card, app._tick)
            border_style = "bold cyan" if selected else ("reverse" if flashing else "dim")
            panels.append(Panel(
                content,
                width=CARD_WIDTH + 2,
                border_style=border_style,
            ))

        return Columns(panels, padding=(1, 1))


class SwitchboardApp(App[None]):
    """Textual app replacing the curses switchboard."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #header {
        dock: top;
        width: 1fr;
        height: 1;
        layout: horizontal;
        background: $foreground;
        color: $background;
        text-style: bold;
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
        height: 0;
        background: $warning;
        color: $background;
        text-style: bold;
    }
    #toast.visible {
        height: 1;
    }
    #footer {
        width: 1fr;
        height: 1;
        background: $foreground;
        color: $background;
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

    _footer_text = (
        " \\[arrows] nav  \\[Enter] patch  \\[s] send  \\[a] all  "
        "\\[n] new  \\[S] ship  \\[f] files  \\[i] info  \\[q] quit"
    )

    def __init__(self) -> None:
        super().__init__()
        self._tracker = StatusTracker()
        self._tmux = TmuxManager()
        try:
            self._wt_manager: WorktreeManager | None = WorktreeManager()
        except Exception:
            self._wt_manager = None
        self._cards: list[Card] = []
        self._file_map: dict[str, list[str]] = {}
        self._cached_statuses: dict[str, WorktreeAIStatus] = {}
        self._selected = 0
        self._tick = 0
        self._prev_statuses: dict[str, AIActivityStatus] = {}
        self._cols = 4

    def compose(self) -> ComposeResult:
        with Container(id="header"):
            yield Static(" SWITCHBOARD", id="header-title")
            yield Static(id="header-stats")
        yield CardGrid(id="card-grid")
        with Container(id="bottom-bar"):
            yield Static(id="toast")
            yield Static(self._footer_text, id="footer")

    def on_mount(self) -> None:
        self.set_interval(TICK_MS / 1000.0, self._on_tick)
        self.set_interval(HEAVY_EVERY * TICK_MS / 1000.0, self._heavy_refresh)
        # Defer first refresh until size is known
        self.call_after_refresh(self._heavy_refresh)

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
        """Heavy refresh: parallel async pane + diff polling."""
        self._cards, self._file_map = await _build_cards_async(self._tracker, self._wt_manager)

        # Cache statuses for light-tick elapsed updates
        self._cached_statuses = {
            s.worktree_name: s
            for s in self._tracker.get_all_statuses()
        }

        # Flash on status transitions
        current_names = {c.name for c in self._cards}
        for name in list(self._prev_statuses):
            if name not in current_names:
                del self._prev_statuses[name]
        for card in self._cards:
            prev = self._prev_statuses.get(card.name)
            if prev is not None and prev != card.status:
                card.flash_until = self._tick + 3
            self._prev_statuses[card.name] = card.status

        # Clamp selection
        if self._cards:
            self._selected = min(self._selected, len(self._cards) - 1)

        self._update_header()
        self.query_one("#card-grid", CardGrid).refresh()

    def _update_header(self) -> None:
        """Update the header stats (right side). Title is static."""
        cards = self._cards
        active = sum(1 for c in cards if c.status == AIActivityStatus.WORKING)
        waiting = sum(1 for c in cards if c.status in (AIActivityStatus.WAITING, AIActivityStatus.BLOCKED))
        total = len(cards)
        idle = total - active - waiting
        overlaps = sum(1 for c in cards if c.overlap_count > 0)

        parts = [f"{total} lines", f"\u25cf{active} active"]
        if waiting:
            parts.append(f"\u26a0{waiting} waiting")
        parts.append(f"\u25cb{idle}")
        if overlaps:
            parts.append(f"!{overlaps} overlap")
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

    def _show_toast(self, message: str) -> None:
        """Show a temporary warning bar above the footer for 3 seconds."""
        toast = self.query_one("#toast", Static)
        toast.update(Text(f" {message}"))
        toast.add_class("visible")
        self.set_timer(3.0, self._hide_toast)

    def _hide_toast(self) -> None:
        toast = self.query_one("#toast", Static)
        toast.remove_class("visible")

    def action_patch_in(self) -> None:
        if not self._cards:
            return
        card = self._cards[self._selected]
        if not card.tmux_session or not self._tmux.session_exists(card.tmux_session):
            self._show_toast(f"No tmux session for '{card.name}'")
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
                    pass

        self.push_screen(InputModal(f"Send to {card.name}: "), _on_input)

    def action_new_worktree(self) -> None:
        def _on_input(task: str | None) -> None:
            if task:
                with self.suspend():
                    subprocess.run(["owt", "new", task], check=False, timeout=SHELL_TIMEOUT)

        self.push_screen(InputModal("Task description: "), _on_input)

    def _confirm_and_shell(self, prompt: str, cmd: list[str]) -> None:
        """Confirm, shell out, then clamp selection."""
        if not self._cards:
            return

        def _on_confirm(yes: bool | None) -> None:
            if yes:
                with self.suspend():
                    subprocess.run(cmd, check=False, timeout=SHELL_TIMEOUT)
                self._selected = min(self._selected, max(0, len(self._cards) - 2))

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

        my_files = set(self._file_map.get(card.name, []))
        overlap_files: dict[str, list[str]] = {}
        for other_name, other_files_list in self._file_map.items():
            if other_name == card.name:
                continue
            for f in my_files & set(other_files_list):
                overlap_files.setdefault(f, []).append(other_name)

        lines = [
            f"  {f_path} \u2190 {', '.join(wt_names)}"
            for f_path, wt_names in sorted(overlap_files.items())
        ]
        self.push_screen(DetailModal(
            f"Overlap: {card.name} ({card.overlap_count} files)",
            lines,
        ))

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
                    capture_output=True, text=True, cwd=wt_path, timeout=5,
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
                            pass

        self.push_screen(InputModal("Broadcast to all: "), _on_input)


# ---------------------------------------------------------------------------
# Tmux session management (unchanged)
# ---------------------------------------------------------------------------


def _is_inside_switchboard_session() -> bool:
    """Check if we're already running inside the switchboard tmux session."""
    if "TMUX" not in os.environ:
        return False
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#S"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip() == SWITCHBOARD_SESSION
    except subprocess.CalledProcessError:
        return False


def _resolve_worktree_from_session(session_name: str) -> str | None:
    """Given a tmux session name like 'owt-foo', return the worktree name 'foo'."""
    prefix = "owt-"
    if session_name.startswith(prefix):
        return session_name[len(prefix):]
    return None


def _install_switchboard_keys() -> None:
    """Install global tmux keybindings for switchboard navigation.

    Alt+s: switch back to the switchboard session
    Alt+c: create a new worktree (runs owt new in a popup)
    Alt+m: merge current worktree
    Alt+d: delete current worktree
    """
    # Unbind Alt+b if previously set (was conflicting with terminal shortcuts)
    subprocess.run(
        ["tmux", "unbind-key", "-n", "M-b"],
        check=False, capture_output=True,
    )

    # Alt+c: create new worktree via popup (tmux >= 3.2) or new window
    major, minor = TmuxManager.get_tmux_version()
    if (major, minor) >= (3, 2):
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-c",
             "display-popup", "-E", "-w", "80%", "-h", "50%", "owt new"],
            check=False, capture_output=True,
        )
    else:
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-c",
             "new-window", "-n", "new-worktree", "owt new"],
            check=False, capture_output=True,
        )

    # Alt+s: switch back to the switchboard session (s = switchboard)
    subprocess.run(
        ["tmux", "bind-key", "-n", "M-s",
         "switch-client", "-t", SWITCHBOARD_SESSION],
        check=False, capture_output=True,
    )

    # Alt+m: merge the current worktree
    merge_script = (
        "wt_name=$(tmux display-message -p '#S' | sed 's/^owt-//'); "
        "if [ -n \"$wt_name\" ] && [ \"$wt_name\" != 'owt-switchboard' ]; then "
        "  tmux switch-client -t owt-switchboard; "
        "  owt merge \"$wt_name\"; "
        "fi"
    )
    if (major, minor) >= (3, 2):
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-m",
             "display-popup", "-E", "-w", "80%", "-h", "50%",
             f"bash -c {_shell_quote(merge_script)}"],
            check=False, capture_output=True,
        )
    else:
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-m",
             "new-window", "-n", "merge",
             f"bash -c {_shell_quote(merge_script)}"],
            check=False, capture_output=True,
        )

    # Alt+d: delete the current worktree
    delete_script = (
        "wt_name=$(tmux display-message -p '#S' | sed 's/^owt-//'); "
        "if [ -n \"$wt_name\" ] && [ \"$wt_name\" != 'owt-switchboard' ]; then "
        "  tmux switch-client -t owt-switchboard; "
        "  owt delete \"$wt_name\" --yes; "
        "fi"
    )
    if (major, minor) >= (3, 2):
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-d",
             "display-popup", "-E", "-w", "80%", "-h", "50%",
             f"bash -c {_shell_quote(delete_script)}"],
            check=False, capture_output=True,
        )
    else:
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-d",
             "new-window", "-n", "delete",
             f"bash -c {_shell_quote(delete_script)}"],
            check=False, capture_output=True,
        )


def _shell_quote(s: str) -> str:
    """Quote a string for shell embedding in tmux commands."""
    import shlex
    return shlex.quote(s)


def launch_switchboard() -> None:
    """Launch the switchboard UI.

    The switchboard runs in its own tmux session. This allows:
    - Enter to switch to an agent session (switchboard stays alive)
    - Alt+s from any agent session to switch back to the switchboard
    - q to exit completely (kills the session, returns to terminal)

    If already inside the switchboard session, runs the Textual app directly.
    If outside tmux, creates the session and attaches.
    If inside another tmux session, switches to the switchboard session.
    """
    if _is_inside_switchboard_session():
        # We're already in the switchboard session — run Textual directly
        app = SwitchboardApp()
        app.run()
        return

    tmux = TmuxManager()

    # Create the switchboard session if it doesn't exist
    if not tmux.session_exists(SWITCHBOARD_SESSION):
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", SWITCHBOARD_SESSION,
             "-n", "switchboard", "owt"],
            check=False,
        )

    # Install global tmux keybindings (Alt+s to return, Alt+c to create, etc.)
    _install_switchboard_keys()

    if tmux.is_inside_tmux():
        # Switch to the switchboard session
        subprocess.run(
            ["tmux", "switch-client", "-t", SWITCHBOARD_SESSION],
            check=False,
        )
    else:
        # Attach to the switchboard session from bare terminal
        subprocess.run(
            ["tmux", "attach-session", "-t", SWITCHBOARD_SESSION],
            check=False,
        )

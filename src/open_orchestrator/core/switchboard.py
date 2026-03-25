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
import logging
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
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static

from open_orchestrator.config import AITool
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.theme import COLORS, STATUS_COLORS
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

logger = logging.getLogger(__name__)

# Status light characters and colors (Rich markup)
STATUS_LIGHTS: dict[str, tuple[str, str]] = {
    "working": ("\u25cf", STATUS_COLORS["working"]),
    "idle": ("\u25cb", STATUS_COLORS["idle"]),
    "blocked": ("\u26a0", STATUS_COLORS["blocked"]),
    "waiting": ("\u26a0", STATUS_COLORS["waiting"]),
    "completed": ("\u2713", STATUS_COLORS["completed"]),
    "error": ("\u25cf", STATUS_COLORS["error"]),
    "unknown": ("?", STATUS_COLORS["unknown"]),
}

CARD_WIDTH = 30
CARD_HEIGHT = 4
SWITCHBOARD_SESSION = "owt-switchboard"
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧"
SPINNER_COLORS = ["#666666", "#888888", "#aaaaaa", "#888888"]
TICK_MS = 150
HEAVY_EVERY = 14  # _build_cards runs every HEAVY_EVERY ticks (~2.1s at 150ms)
RECHECKABLE_STATUSES = {AIActivityStatus.WORKING, AIActivityStatus.WAITING, AIActivityStatus.BLOCKED, AIActivityStatus.IDLE}
HOOK_FRESHNESS_SECONDS = 10  # Trust hook-set status if updated within this window
HOOK_CAPABLE_TOOLS = {AITool.CLAUDE.value, AITool.DROID.value}  # Scraper must not downgrade WORKING → WAITING
HOOK_TRUST_MAX_SECONDS = 15  # After 15s with no hook update, let scraper recover stale WORKING

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
# Box-drawing separator lines (U+2500–U+257F) carry no semantic info
_SEPARATOR_RE = re.compile(r"^[\u2500-\u257F\s]{5,}$")
_PROMPT_RE = re.compile(
    r"^[>❯›»\)]\s*$|^\$\s*$|What would you like|How can I help",
    re.IGNORECASE,
)
# High-confidence working signal — Claude Code tool execution headers
_TOOL_HEADER_RE = re.compile(
    r"^(Read|Write|Edit|Bash|Glob|Grep|Agent|WebFetch|WebSearch|NotebookEdit)\s*[:/]",
)
# High-confidence idle signal — agent was interrupted/stopped, never appears during thinking
_INTERRUPTED_RE = re.compile(
    r"Interrupted|What should Claude do instead",
    re.IGNORECASE,
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _tmux_session_exists_raw(session_name: str) -> bool:
    """Check tmux session existence via raw subprocess (safe for asyncio.to_thread)."""
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True, timeout=2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


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
    content_lines = [
        line for line in tail
        if not _STATUS_BAR_RE.search(line) and not _SEPARATOR_RE.match(line)
    ]
    content_text = "\n".join(reversed(content_lines)) if content_lines else ""

    # BLOCKED detection: only scan the last 2 non-empty content lines.
    # Old permission prompts that scrolled up should NOT trigger BLOCKED.
    blocked_window = content_lines[:2]
    blocked_text = "\n".join(blocked_window)
    if blocked_text and (_BLOCKED_RE.search(blocked_text) or _ALLOW_PROMPT_RE.search(blocked_text)):
        return AIActivityStatus.BLOCKED, True

    # TOOL_HEADER detection in last 2 content lines → high-confidence WORKING.
    for line in content_lines[:2]:
        if _TOOL_HEADER_RE.search(line):
            return AIActivityStatus.WORKING, True

    # Check for high-confidence idle signals (Interrupted, etc.)
    has_interrupted = bool(content_text and _INTERRUPTED_RE.search(content_text))

    # WAITING detection: prompt char on the VERY LAST non-empty content line.
    # Strip ANSI escape sequences and trailing whitespace before checking.
    if content_lines:
        last_line = _ANSI_RE.sub("", content_lines[0]).strip()
        if len(last_line) < 15 and _PROMPT_RE.search(last_line):
            return AIActivityStatus.WAITING, has_interrupted

    # No prompt visible — agent is doing something
    return AIActivityStatus.WORKING, False


# Files that are commonly touched by all worktrees and should not count as
# meaningful overlaps (lock files, package manifests, init modules, etc.).
_OVERLAP_IGNORE_NAMES: frozenset[str] = frozenset({
    "pyproject.toml", "uv.lock", "package-lock.json", "yarn.lock",
    "poetry.lock", "Cargo.lock", "Cargo.toml", "requirements.txt",
    "requirements-dev.txt", "__init__.py", "CLAUDE.md", ".env", ".env.example",
})


def _get_diff_info(worktree_path: str, branch: str) -> tuple[list[str], str]:
    """Get modified files AND diff stat in a single git call.

    Uses ``git merge-base`` to find the common ancestor with the upstream
    branch, then diffs from that ancestor to HEAD so we only see commits
    that belong to this worktree.  Falls back to raw three-dot diff if
    ``merge-base`` is unavailable.

    Returns (modified_files, diff_stat_str).
    """
    if not os.path.isdir(worktree_path):
        return [], ""
    try:
        for base in ("main", "master", "develop"):
            # Find the common ancestor for accurate diffs
            mb_result = subprocess.run(
                ["git", "merge-base", base, "HEAD"],
                capture_output=True, text=True, cwd=worktree_path, timeout=5,
            )
            if mb_result.returncode != 0:
                continue
            merge_base = mb_result.stdout.strip()
            if not merge_base:
                continue

            result = subprocess.run(
                ["git", "diff", "--numstat", f"{merge_base}...HEAD"],
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
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("git diff failed for worktree: %s", e)
    return [], ""


def _filter_overlap_files(files: set[str]) -> set[str]:
    """Remove noise files that should not trigger overlap warnings."""
    return {f for f in files if os.path.basename(f) not in _OVERLAP_IGNORE_NAMES}


def _compute_overlaps(
    cards: list[Card], file_map: dict[str, list[str]],
) -> None:
    """Compute pairwise file overlaps and annotate cards.

    Only meaningful source files are considered — lock files, manifests, and
    other commonly-modified noise files are excluded.
    """
    # Pre-compute filtered file sets to avoid O(n^2) _filter_overlap_files calls
    filtered_map = {
        card.name: _filter_overlap_files(set(file_map.get(card.name, [])))
        for card in cards
    }
    for i, card in enumerate(cards):
        my_files = filtered_map[card.name]
        if not my_files:
            continue
        overlap_names = []
        overlap_files: set[str] = set()
        for j, other in enumerate(cards):
            if i == j:
                continue
            other_files = filtered_map[other.name]
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
        valid_names = {wt.name for wt in worktrees}
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
        # Auto-prune orphaned status entries (worktree deleted outside OWT)
        orphan_names = set(status_map.keys()) - valid_names
        if orphan_names:
            try:
                tracker.cleanup_orphans(list(valid_names))
                logger.debug("Pruned %d orphaned status entries: %s", len(orphan_names), orphan_names)
            except Exception as e:
                logger.debug("Orphan cleanup failed: %s", e)
    else:
        # Fallback: use status DB directly (test environments, no git repo)
        statuses = list(status_map.values())

    now = datetime.now()

    # Schedule parallel I/O: session checks + pane detection + diff info
    tasks: list[collections.abc.Awaitable[object]] = []
    task_meta: list[tuple[str, int]] = []  # ("session"|"pane"|"diff", status_index)

    # Session existence checks (parallel)
    for i, s in enumerate(statuses):
        if s.tmux_session:
            tasks.append(asyncio.to_thread(_tmux_session_exists_raw, s.tmux_session))
            task_meta.append(("session", i))

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
    stale_sessions: set[int] = set()
    for (kind, idx), result in zip(task_meta, results):
        if isinstance(result, BaseException):
            continue
        if kind == "session":
            if not result:  # session doesn't exist
                stale_sessions.add(idx)
        elif kind == "pane":
            pane_results[idx] = result  # type: ignore[assignment]
        else:
            diff_results[idx] = result  # type: ignore[assignment]

    # Clear stale tmux sessions before processing
    for idx in stale_sessions:
        s = statuses[idx]
        s.tmux_session = None
        if s.activity_status in (AIActivityStatus.WORKING, AIActivityStatus.BLOCKED):
            s.activity_status = AIActivityStatus.WAITING
        s.updated_at = now
        tracker.set_status(s)
        # Discard any pane detection for this stale entry
        pane_results.pop(idx, None)

    # Process results and build cards
    cards = []
    file_map: dict[str, list[str]] = {}

    for i, s in enumerate(statuses):
        # Apply pane detection (skip entries with stale sessions)
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
    """Render a card as Rich markup text.

    Compact 3-line layout:
      [light] name              elapsed
      branch | tool | stats
      task description     [!N]
    """
    w = CARD_WIDTH - 2  # inner width (minus panel border)
    status_key = card.status.value
    light_char, color = STATUS_LIGHTS.get(status_key, ("?", "white"))
    if card.status == AIActivityStatus.WORKING:
        light_char = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
        color = SPINNER_COLORS[tick // 2 % len(SPINNER_COLORS)]

    # Line 1: status light + name + elapsed (right-aligned)
    name_trunc = card.name[: w - 8]
    elapsed_str = card.elapsed or ""
    line1_left = f"[{color}]{light_char}[/{color}] [bold]{name_trunc}[/bold]"
    visible_left = 2 + len(name_trunc)
    pad1 = w - visible_left - len(elapsed_str)
    line1 = line1_left + " " * max(1, pad1) + f"[dim]{elapsed_str}[/dim]"

    # Line 2: branch | tool | diff stats (dim)
    branch_short = card.branch.split("/")[-1] if "/" in card.branch else card.branch
    meta_parts: list[str] = [branch_short[:12]]
    if card.ai_tool:
        meta_parts.append(card.ai_tool[:8])
    if card.diff_stat:
        diff_display = card.diff_stat.replace("+", "\u2191").replace("-", "\u2193")
        meta_parts.append(diff_display)
    meta_str = " | ".join(meta_parts)
    line2 = f"[dim]{meta_str[:w]}[/dim]"

    # Line 3: task + overlap badge
    if card.overlap_count > 0:
        overlap_badge = f" [yellow bold][!{card.overlap_count}][/yellow bold]"
        badge_visible_len = len(f" [!{card.overlap_count}]")
        task_str = card.task or "\u2014"
        task_trunc = task_str[: w - badge_visible_len]
        line3 = f"{task_trunc}{overlap_badge}"
    else:
        task_str = card.task or "\u2014"
        line3 = f"{task_str[:w]}"

    return "\n".join([line1, line2, line3])


class InputModal(ModalScreen[str | None]):
    """Modal screen for text input (send, new, broadcast)."""

    DEFAULT_CSS = f"""
    InputModal {{
        align: center middle;
    }}
    #input-dialog {{
        width: 55;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        border: none;
        background: {COLORS["surface"]};
    }}
    #input-dialog Input {{
        border: none;
        background: transparent;
        border-left: tall {COLORS["input_border"]};
        padding: 0 0 0 1;
        margin: 1 0;
        min-height: 2;
    }}
    #input-dialog Input:focus {{
        border: none;
        border-left: tall {COLORS["input_border"]};
    }}
    #input-dialog Label {{
        margin: 0;
    }}
    .modal-hint {{
        margin: 0;
        text-style: dim;
        color: {COLORS["text_secondary"]};
    }}
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Container(id="input-dialog"):
            yield Label(self._prompt)
            yield Input(id="modal-input")
            yield Static("[dim]Enter[/dim] submit | [dim]Esc[/dim] cancel", classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one("#modal-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if value else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Modal screen for y/N confirmation."""

    DEFAULT_CSS = f"""
    ConfirmModal {{
        align: center middle;
    }}
    #confirm-dialog {{
        width: auto;
        min-width: 40;
        max-width: 70;
        height: auto;
        padding: 1 2;
        border: none;
        background: {COLORS["surface"]};
    }}
    #confirm-dialog Label {{
        margin: 0;
    }}
    .modal-hint {{
        margin-top: 1;
        text-style: dim;
        color: {COLORS["text_secondary"]};
    }}
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
            yield Static("[dim]Y[/dim] yes | [dim]N[/dim] no | [dim]Esc[/dim] cancel", classes="modal-hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class DetailModal(ModalScreen[None]):
    """Modal screen for detail panels (info, overlap)."""

    DEFAULT_CSS = f"""
    DetailModal {{
        align: center middle;
    }}
    #detail-panel {{
        width: 70;
        max-width: 90%;
        max-height: 80%;
        padding: 2 3;
        border: none;
        background: {COLORS["surface"]};
        overflow-y: auto;
    }}
    #detail-panel .modal-title {{
        text-style: bold;
        margin-bottom: 1;
    }}
    #detail-panel .modal-hint {{
        margin-top: 1;
        text-style: dim;
    }}
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
            yield Static("[dim]Esc[/dim] close", classes="modal-hint")

    def on_key(self, event: Key) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


@dataclass
class SelectOption:
    """An option in the searchable select modal."""

    value: str
    label: str
    description: str = ""
    category: str = ""


class SearchableSelectModal(ModalScreen[str | None]):
    """Modal with search input and categorized selectable list.

    Usage example::

        options = [
            SelectOption(value="wt-auth", label="auth-flow", description="COMPLETED", category="Ready"),
            SelectOption(value="wt-api", label="api-refactor", description="WORKING", category="In Progress"),
        ]
        def on_selected(value: str | None) -> None:
            if value:
                # value is the SelectOption.value of the chosen item
                ...
        self.push_screen(SearchableSelectModal("Pick a worktree", options), on_selected)
    """

    DEFAULT_CSS = f"""
    SearchableSelectModal {{
        align: center middle;
    }}
    #select-dialog {{
        width: 55;
        max-width: 90%;
        height: auto;
        max-height: 60%;
        padding: 1 2;
        border: none;
        background: {COLORS["surface"]};
    }}
    #select-title-row {{
        layout: horizontal;
        height: 1;
        margin-bottom: 1;
    }}
    #select-title {{
        width: 1fr;
        text-style: bold;
    }}
    #select-esc-hint {{
        width: auto;
        color: {COLORS["text_secondary"]};
    }}
    #select-search {{
        margin-bottom: 1;
        border: none;
        background: transparent;
        border-left: tall {COLORS["input_border"]};
        padding: 0 0 0 1;
    }}
    #select-search:focus {{
        border: none;
        border-left: tall {COLORS["input_border"]};
    }}
    #select-list {{
        height: auto;
        max-height: 20;
        overflow-y: auto;
    }}
    .select-category {{
        color: white;
        text-style: bold;
        margin-top: 1;
    }}
    .select-item {{
        padding: 0 1;
        height: 1;
    }}
    .select-item.highlighted {{
        background: {COLORS["surface_4dp"]};
        text-style: bold;
    }}
    .select-hint {{
        margin-top: 1;
        color: {COLORS["text_secondary"]};
        text-style: dim;
    }}
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "select_item", "Select", show=False),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
    ]

    def __init__(
        self,
        title: str,
        options: list[SelectOption],
        *,
        search_placeholder: str = "Search...",
    ) -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._search_placeholder = search_placeholder
        self._filtered: list[SelectOption] = list(options)
        self._highlight_index = 0

    def compose(self) -> ComposeResult:
        with Container(id="select-dialog"):
            with Container(id="select-title-row"):
                yield Static(self._title, id="select-title")
                yield Static("esc", id="select-esc-hint")
            yield Input(placeholder=self._search_placeholder, id="select-search")
            yield Container(id="select-list")  # populated dynamically
            yield Static(
                "[dim][bold]1-9[/bold] quick pick | "
                "[bold]\u2191\u2193[/bold] navigate | "
                "[bold]Enter[/bold] select | "
                "[bold]Esc[/bold] cancel[/dim]",
                classes="select-hint",
            )

    def on_mount(self) -> None:
        self._rebuild_list()
        self.query_one("#select-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "select-search":
            query = event.value.strip()
            # Number shortcut: if single digit, select that item directly
            if query.isdigit() and 1 <= int(query) <= len(self._options):
                idx = int(query) - 1
                self.dismiss(self._options[idx].value)
                return
            query_lower = query.lower()
            if query_lower:
                self._filtered = [
                    opt
                    for opt in self._options
                    if query_lower in opt.label.lower()
                    or query_lower in opt.category.lower()
                    or query_lower in opt.description.lower()
                ]
            else:
                self._filtered = list(self._options)
            self._highlight_index = 0
            self._rebuild_list()

    def _rebuild_list(self) -> None:
        """Rebuild the option list, grouping by category."""
        container = self.query_one("#select-list", Container)
        container.remove_children()

        # Group by category (preserving insertion order)
        categories: dict[str, list[SelectOption]] = {}
        for opt in self._filtered:
            cat = opt.category or ""
            categories.setdefault(cat, []).append(opt)

        idx = 0
        for cat_name, opts in categories.items():
            if cat_name:
                container.mount(Static(cat_name, classes="select-category"))
            for opt in opts:
                # Show 1-indexed number for quick selection
                num = idx + 1
                num_hint = f"[dim]{num}.[/dim] " if num <= 9 else "   "
                label = opt.label
                if opt.description:
                    label = f"{opt.label}  [dim]{opt.description}[/dim]"
                item = Static(f"  {num_hint}{label}", classes="select-item")
                item.data_index = idx  # type: ignore[attr-defined]
                if idx == self._highlight_index:
                    item.add_class("highlighted")
                container.mount(item)
                idx += 1

    def _update_highlight(self, new_index: int) -> None:
        """Move highlight by toggling CSS classes (avoids full remount)."""
        items = self.query(".select-item")
        if not items:
            return
        old = self._highlight_index
        self._highlight_index = new_index
        if 0 <= old < len(items):
            items[old].remove_class("highlighted")
        if 0 <= new_index < len(items):
            items[new_index].add_class("highlighted")

    def action_move_up(self) -> None:
        if self._filtered and self._highlight_index > 0:
            self._update_highlight(self._highlight_index - 1)

    def action_move_down(self) -> None:
        if self._filtered and self._highlight_index < len(self._filtered) - 1:
            self._update_highlight(self._highlight_index + 1)

    def action_select_item(self) -> None:
        if self._filtered and 0 <= self._highlight_index < len(self._filtered):
            self.dismiss(self._filtered[self._highlight_index].value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
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
            selected = i == app._selected
            content = _render_card(card, app._tick)
            flash_remaining = card.flash_until - app._tick
            if flash_remaining > 3:
                border_style = "bold white reverse"
            elif flash_remaining > 0:
                border_style = "bold white"
            elif selected:
                border_style = "white"
            else:
                border_style = COLORS["card_border"]
            panels.append(Panel(
                content,
                width=CARD_WIDTH + 2,
                padding=(0, 1),
                border_style=border_style,
            ))

        return Columns(panels, padding=(0, 1))


class SwitchboardApp(App[None]):
    """Textual app replacing the curses switchboard."""

    CSS = f"""
    Screen {{
        layout: vertical;
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

    def __init__(self) -> None:
        super().__init__()
        self._tracker = StatusTracker()
        self._tmux = TmuxManager()
        try:
            self._wt_manager: WorktreeManager | None = WorktreeManager()
        except Exception:
            self._wt_manager = None
        # Pre-build cards synchronously so the first render already has content
        # (eliminates "No active worktrees" flash). asyncio.run() is safe here
        # because Textual's event loop hasn't started yet.
        try:
            self._cards, self._file_map = _build_cards(self._tracker)
        except Exception:
            self._cards, self._file_map = [], {}
        # Pre-cache statuses so elapsed times render on first frame
        self._cached_statuses: dict[str, WorktreeAIStatus] = {
            s.worktree_name: s for s in self._tracker.get_all_statuses()
        }
        self._selected = 0
        self._tick = 0
        self._prev_statuses: dict[str, AIActivityStatus] = {}
        self._cols = 4
        self._heavy_refresh_count = 0
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
        self._heavy_refresh_count += 1
        self._cards, self._file_map = await _build_cards_async(self._tracker, self._wt_manager)

        # Periodic orphan cleanup (~every 20s)
        # Pass current card names as valid — entries not in this list are pruned.
        # When no cards exist, all status entries are orphans and get cleaned up.
        if self._heavy_refresh_count % 10 == 0:
            valid_names = [c.name for c in self._cards]
            self._tracker.cleanup_orphans(valid_names)

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
            pass

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
            # Stale session — offer to delete or recreate
            def _on_delete_confirm(yes: bool | None) -> None:
                if yes:
                    self.run_worker(self._run_shell_bg(
                        ["owt", "delete", card.name, "--yes"],
                        f"Deleting '{card.name}'...", clamp=True,
                    ))
                else:
                    # Offer to recreate
                    def _on_recreate(yes2: bool | None) -> None:
                        if yes2:
                            self.run_worker(self._run_shell_bg(
                                ["owt", "new", card.branch, "--yes"],
                                f"Recreating '{card.name}'...",
                            ))

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
                    pass

        self.push_screen(InputModal(f"Send to {card.name}: "), _on_input)

    def action_new_worktree(self) -> None:
        def _on_input(task: str | None) -> None:
            if not task:
                return
            self._new_wt_task = task
            # Generate branch name and confirm
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
        # Check for AI tools
        from open_orchestrator.core.agent_detector import detect_installed_agents

        installed = detect_installed_agents()
        if len(installed) == 0:
            self._show_toast("No AI tools found (claude, opencode, droid)", variant="error")
            return
        elif len(installed) == 1:
            self._new_wt_tool = installed[0]
            self._do_create_worktree()
        else:
            # Multiple tools — show picker
            options = [
                SelectOption(
                    value=t.value,
                    label=t.value,
                    category="Detected",
                )
                for t in installed
            ]

            def _on_tool(tool_value: str | None) -> None:
                if not tool_value:
                    return
                self._new_wt_tool = AITool(tool_value)
                self._do_create_worktree()

            self.push_screen(
                SearchableSelectModal("Select AI tool", options),
                _on_tool,
            )

    async def _run_shell_bg(self, cmd: list[str], toast_msg: str, *, clamp: bool = False) -> None:
        """Run a shell command in the background without suspending the UI.

        Shows a toast while running, refreshes cards on completion, and
        reports success or failure via toast.
        """
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
        """Create worktree via owt new subprocess (reliable, handles all setup)."""
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

        # If multiple completed worktrees exist and the selected card isn't
        # completed, show a SearchableSelectModal so the user can pick which
        # completed worktree to merge.
        completed = [
            c for c in self._cards
            if c.status == AIActivityStatus.COMPLETED
        ]
        if len(completed) > 1 and card.status != AIActivityStatus.COMPLETED:
            options = [
                SelectOption(
                    value=c.name,
                    label=c.name,
                    description=c.diff_stat or "",
                    category="Completed",
                )
                for c in completed
            ]

            def _on_pick(name: str | None) -> None:
                if name:
                    self._confirm_and_shell(
                        f"Merge '{name}'?", ["owt", "merge", name],
                    )

            self.push_screen(
                SearchableSelectModal("Merge which worktree?", options),
                _on_pick,
            )
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

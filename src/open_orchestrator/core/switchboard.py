"""
Switchboard: curses-based card grid for multi-agent orchestration.

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

import curses
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.tmux_manager import TmuxManager
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

# Status light characters and colors
STATUS_LIGHTS: dict[str, tuple[str, int]] = {
    "working": ("\u25cf", curses.COLOR_GREEN),      # ● green
    "idle": ("\u25cb", curses.COLOR_WHITE),          # ○ white
    "blocked": ("\u26a0", curses.COLOR_YELLOW),      # ⚠ yellow
    "waiting": ("\u26a0", curses.COLOR_YELLOW),      # ⚠ yellow
    "completed": ("\u2713", curses.COLOR_CYAN),      # ✓ cyan
    "error": ("\u25cf", curses.COLOR_RED),            # ● red
    "unknown": ("?", curses.COLOR_WHITE),
}

CARD_WIDTH = 30
CARD_HEIGHT = 6
SWITCHBOARD_SESSION = "owt-switchboard"
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧"
TICK_MS = 200
HEAVY_EVERY = 10  # _build_cards() runs every HEAVY_EVERY ticks (2s)
RECHECKABLE_STATUSES = {AIActivityStatus.WORKING, AIActivityStatus.WAITING, AIActivityStatus.BLOCKED, AIActivityStatus.IDLE}
HOOK_FRESHNESS_SECONDS = 10  # Trust hook-set status if updated within this window

# Pre-compiled regex patterns for pane status detection
_BLOCKED_RE = re.compile(
    r"Allow\s|\(y/N\)|\(Y/n\)|approve|deny|Do you want to|Press Enter to",
    re.IGNORECASE,
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


def _detect_pane_status(tmux_session: str | None) -> AIActivityStatus | None:
    """Detect agent status by capturing tmux pane content.

    Looks for prompt indicators that signal the agent is waiting for input
    or blocked on a permission/confirmation prompt.

    Detection strategy:
    - BLOCKED: permission/confirmation prompts (highest priority)
    - WAITING: idle prompt (❯ or >) visible, optionally confirmed by status bar
    - WORKING: active output indicators (spinner, streaming)
    - None: inconclusive — caller keeps existing status
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

    if content_text and _BLOCKED_RE.search(content_text):
        return AIActivityStatus.BLOCKED

    # If idle prompt (❯) is visible → WAITING. Otherwise → WORKING.
    for line in content_lines[:5]:
        if _PROMPT_RE.search(line):
            return AIActivityStatus.WAITING

    # No prompt visible — agent is doing something
    return AIActivityStatus.WORKING


def _get_diff_info(worktree_path: str, branch: str) -> tuple[list[str], str]:
    """Get modified files AND diff stat in a single git call.

    Uses `git diff --numstat` which yields both file names and line counts.
    Returns (modified_files, diff_stat_str).
    """
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


def _build_cards(tracker: StatusTracker) -> tuple[list[Card], dict[str, list[str]]]:
    """Build card list from current status data.

    On each refresh, captures tmux pane content to detect whether
    agents are actually working, waiting for input, or blocked on
    a permission prompt — then updates the persisted status.

    Returns (cards, file_map) where file_map maps worktree names to modified files.
    """
    tracker.reload()
    statuses = tracker.get_all_statuses()

    cards = []
    file_map: dict[str, list[str]] = {}
    now = datetime.now()
    for s in statuses:
        # If status was recently updated (by hooks), trust it and skip pane scraping.
        # Hooks push real-time status; pane scraping is the fallback for tools without hooks.
        recently_updated = (
            s.updated_at
            and (now - s.updated_at).total_seconds() < HOOK_FRESHNESS_SECONDS
        )
        if not recently_updated and s.activity_status in RECHECKABLE_STATUSES:
            detected = _detect_pane_status(s.tmux_session)
            if detected and detected != s.activity_status:
                s.activity_status = detected
                s.updated_at = now
                tracker.set_status(s)

        # Compute modified files + diff stats (single git call)
        mod_files, diff_stat = _get_diff_info(s.worktree_path, s.branch)
        if mod_files != s.modified_files:
            s.modified_files = mod_files
            tracker.set_status(s)
        file_map[s.worktree_name] = mod_files

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


def _draw_card(
    win: curses.window, y: int, x: int, card: Card,
    selected: bool, color_pairs: dict[str, int], tick: int = 0,
) -> None:
    """Draw a single card at the given position."""
    max_y, max_x = win.getmaxyx()

    # Don't draw if out of bounds
    if y + CARD_HEIGHT > max_y or x + CARD_WIDTH > max_x:
        return

    flashing = tick < card.flash_until
    flash_attr = curses.A_REVERSE if flashing else 0

    top_attr = (curses.A_BOLD | curses.A_REVERSE) if selected else flash_attr
    bottom_attr = flash_attr
    title_attr = (curses.A_BOLD | curses.A_REVERSE) if selected else (curses.A_BOLD | flash_attr)

    # Top border
    win.addstr(y, x, "\u250c" + "\u2500" * (CARD_WIDTH - 2) + "\u2510", top_attr)

    # Card title line
    name_trunc = card.name[:CARD_WIDTH - 6]
    win.addstr(y, x + 2, f" {name_trunc} ", title_attr)

    # Status line — use spinner for working agents
    status_key = card.status.value
    light_char, _ = STATUS_LIGHTS.get(status_key, ("?", curses.COLOR_WHITE))
    if card.status == AIActivityStatus.WORKING:
        light_char = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
    pair = color_pairs.get(status_key, 0)
    status_label = status_key.upper()
    elapsed_str = f"{card.elapsed:>6}" if card.elapsed else ""
    win.addstr(y + 1, x, "\u2502" + " " * (CARD_WIDTH - 2) + "\u2502")
    win.addstr(y + 1, x + 2, f"{light_char} ", curses.color_pair(pair) | curses.A_BOLD)
    win.addstr(y + 1, x + 4, f"{status_label:<10}")
    win.addstr(y + 1, x + 16, f"{elapsed_str:>{CARD_WIDTH - 19}}")
    win.addstr(y + 1, x + CARD_WIDTH - 1, "\u2502")

    # Branch line
    branch_short = card.branch.split("/")[-1] if "/" in card.branch else card.branch
    branch_trunc = branch_short[:CARD_WIDTH - 4]
    win.addstr(y + 2, x, "\u2502" + " " * (CARD_WIDTH - 2) + "\u2502")
    win.addstr(y + 2, x + 2, branch_trunc)
    win.addstr(y + 2, x + CARD_WIDTH - 1, "\u2502")

    # AI tool + diff stat line
    win.addstr(y + 3, x, "\u2502" + " " * (CARD_WIDTH - 2) + "\u2502")
    tool_label = card.ai_tool[:12]
    win.addstr(y + 3, x + 2, tool_label)
    if card.diff_stat:
        stat_x = x + CARD_WIDTH - len(card.diff_stat) - 2
        if stat_x > x + len(tool_label) + 2:
            win.addstr(y + 3, stat_x, card.diff_stat, curses.A_DIM)
    win.addstr(y + 3, x + CARD_WIDTH - 1, "\u2502")

    # Task line (with overlap warning)
    win.addstr(y + 4, x, "\u2502" + " " * (CARD_WIDTH - 2) + "\u2502")
    if card.overlap_count > 0:
        overlap_tag = f"[! {card.overlap_count} overlap]"
        overlap_pair = color_pairs.get("blocked", 0)
        win.addstr(y + 4, x + 2, overlap_tag, curses.color_pair(overlap_pair) | curses.A_BOLD)
        remaining = CARD_WIDTH - 4 - len(overlap_tag) - 1
        if remaining > 0 and card.task:
            win.addstr(y + 4, x + 2 + len(overlap_tag) + 1, card.task[:remaining])
    else:
        task_str = card.task or "\u2014"
        task_trunc = task_str[:CARD_WIDTH - 4]
        win.addstr(y + 4, x + 2, task_trunc)
    win.addstr(y + 4, x + CARD_WIDTH - 1, "\u2502")

    # Bottom border
    win.addstr(y + 5, x, "\u2514" + "\u2500" * (CARD_WIDTH - 2) + "\u2518", bottom_attr)


def _draw_header(win: curses.window, cards: list[Card]) -> None:
    """Draw the header bar."""
    max_x = win.getmaxyx()[1]

    active = sum(1 for c in cards if c.status == AIActivityStatus.WORKING)
    waiting = sum(1 for c in cards if c.status in (AIActivityStatus.WAITING, AIActivityStatus.BLOCKED))
    total = len(cards)
    idle = total - active - waiting
    overlaps = sum(1 for c in cards if c.overlap_count > 0)

    title = "SWITCHBOARD"
    parts = [f"{total} lines", f"\u25cf{active} active"]
    if waiting:
        parts.append(f"\u26a0{waiting} waiting")
    parts.append(f"\u25cb{idle}")
    if overlaps:
        parts.append(f"!{overlaps} overlap")
    stats = "  ".join(parts)

    # Draw header — pad to full width so A_REVERSE fills the line
    header = f"  {title:<30} {stats:>{max_x - 34}}"
    header = header.ljust(max_x - 1)
    win.addstr(0, 0, header[:max_x - 1], curses.A_BOLD | curses.A_REVERSE)


def _draw_footer(win: curses.window) -> None:
    """Draw the footer with key bindings."""
    max_y, max_x = win.getmaxyx()
    keys = "[arrows] nav [Enter] patch [s] send [a] all [n] new [S] ship [f] files [i] info [q] quit"
    footer = f"  {keys}"
    footer = footer.ljust(max_x - 1)
    try:
        win.addstr(max_y - 1, 0, footer[:max_x - 1], curses.A_REVERSE)
    except curses.error:
        pass


def _prompt_input(win: curses.window, prompt: str) -> str | None:
    """Show a prompt at the bottom and get user input."""
    max_y, max_x = win.getmaxyx()
    curses.echo()
    curses.curs_set(1)
    # Disable timeout so getstr() blocks until Enter
    win.timeout(-1)
    win.nodelay(False)
    try:
        win.addstr(max_y - 1, 0, " " * (max_x - 1))
        win.addstr(max_y - 1, 0, prompt[:max_x - 1])
        win.refresh()
        response = win.getstr(max_y - 1, len(prompt), max_x - len(prompt) - 1)
        return response.decode("utf-8").strip() if response else None
    except curses.error:
        return None
    finally:
        curses.noecho()
        curses.curs_set(0)
        # Restore fast tick for animations
        win.nodelay(True)
        win.timeout(TICK_MS)


def _show_modal(
    win: curses.window, title: str, lines: list[str], width: int = 60,
) -> None:
    """Draw a centered modal panel and wait for any key to dismiss."""
    max_y, max_x = win.getmaxyx()
    panel_w = min(width, max_x - 4)
    panel_h = min(len(lines) + 4, max_y - 4)
    sy = (max_y - panel_h) // 2
    sx = (max_x - panel_w) // 2

    win.attron(curses.A_REVERSE)
    for row in range(panel_h):
        win.addstr(sy + row, sx, " " * panel_w)
    win.attroff(curses.A_REVERSE)

    win.addstr(sy, sx + 1, title[:panel_w - 2], curses.A_BOLD | curses.A_REVERSE)
    for i, line in enumerate(lines):
        if i + 2 >= panel_h - 1:
            break
        win.addstr(sy + 2 + i, sx + 1, line[:panel_w - 2], curses.A_REVERSE)

    win.addstr(sy + panel_h - 1, sx + 1, " Press any key to close ", curses.A_REVERSE | curses.A_DIM)
    win.refresh()
    win.timeout(-1)
    win.getch()
    win.nodelay(True)
    win.timeout(TICK_MS)


def _show_overlap_detail(
    win: curses.window, card: Card, file_map: dict[str, list[str]],
) -> None:
    """Show file overlap detail using precomputed file_map."""
    my_files = set(file_map.get(card.name, []))
    overlap_files: dict[str, list[str]] = {}
    for other_name, other_files_list in file_map.items():
        if other_name == card.name:
            continue
        for f in my_files & set(other_files_list):
            overlap_files.setdefault(f, []).append(other_name)

    lines = []
    for f_path, wt_names in sorted(overlap_files.items()):
        lines.append(f"  {f_path} \u2190 {', '.join(wt_names)}")

    _show_modal(win, f" Overlap: {card.name} ({card.overlap_count} files) ", lines)


def _show_detail_panel(win: curses.window, card: Card, tracker: StatusTracker) -> None:
    """Show detail panel with git stats, commits, and last AI message."""
    status = tracker.get_status(card.name)
    wt_path = status.worktree_path if status else ""

    commits: list[str] = []
    if wt_path:
        try:
            result = subprocess.run(
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

    _show_modal(win, f" Detail: {card.name} ", lines, width=70)


def _reinit_curses(stdscr: curses.window) -> curses.window:
    """Re-initialize curses after shelling out."""
    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    stdscr.keypad(True)
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(TICK_MS)
    return stdscr


SHELL_TIMEOUT = 120  # seconds — max time for owt ship/merge/delete/new


def _shell_out(
    stdscr: curses.window, cmd: list[str], timeout: int = SHELL_TIMEOUT,
) -> curses.window:
    """Shell out to a command, handling timeout and Ctrl+C gracefully.

    Always returns a re-initialized curses window, even on failure.
    """
    curses.endwin()
    print(f"  (Ctrl+C to cancel, {timeout}s timeout)\n")
    try:
        subprocess.run(cmd, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"\n  Timed out after {timeout}s. Returning to switchboard.")
    except KeyboardInterrupt:
        print("\n  Cancelled. Returning to switchboard.")
    return _reinit_curses(stdscr)


def _run_switchboard(stdscr: curses.window) -> None:
    """Main switchboard loop."""
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.nodelay(True)
    stdscr.timeout(TICK_MS)

    # Initialize color pairs
    curses.start_color()
    curses.use_default_colors()
    color_pairs: dict[str, int] = {}
    for i, (status_key, (_, color)) in enumerate(STATUS_LIGHTS.items(), start=1):
        curses.init_pair(i, color, -1)
        color_pairs[status_key] = i

    tracker = StatusTracker()
    tmux = TmuxManager()
    selected = 0
    tick = 0
    cards: list[Card] = []
    prev_statuses: dict[str, AIActivityStatus] = {}
    cached_statuses: dict[str, WorktreeAIStatus] = {}  # from last heavy refresh
    cached_file_map: dict[str, list[str]] = {}

    while True:
        # Heavy refresh (tmux pane capture) only every HEAVY_EVERY ticks
        if tick % HEAVY_EVERY == 0:
            cards, cached_file_map = _build_cards(tracker)

            # Cache status objects for light-tick elapsed updates
            cached_statuses = {
                s.worktree_name: s
                for s in tracker.get_all_statuses()
            }

            # Detect status transitions → set flash_until; prune stale entries
            current_names = {card.name for card in cards}
            for name in list(prev_statuses):
                if name not in current_names:
                    del prev_statuses[name]
            for card in cards:
                prev = prev_statuses.get(card.name)
                if prev is not None and prev != card.status:
                    card.flash_until = tick + 3  # flash for ~600ms (3 ticks)
                prev_statuses[card.name] = card.status

        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()

        _draw_header(stdscr, cards)

        if not cards:
            msg = "No active worktrees. Press [n] to create one, [q] to quit."
            stdscr.addstr(max_y // 2, max(0, (max_x - len(msg)) // 2), msg)
        else:
            # Calculate grid
            cols = max(1, (max_x - 2) // (CARD_WIDTH + 2))
            start_y = 2
            start_x = 1

            # Re-compute elapsed times on every tick (uses cached status, no I/O)
            for card in cards:
                wt_status = cached_statuses.get(card.name)
                if wt_status:
                    card.elapsed = _format_elapsed(wt_status)

            for i, card in enumerate(cards):
                row = i // cols
                col = i % cols
                y = start_y + row * (CARD_HEIGHT + 1)
                x = start_x + col * (CARD_WIDTH + 2)

                if y + CARD_HEIGHT < max_y - 1:
                    _draw_card(stdscr, y, x, card, selected == i, color_pairs, tick)

        _draw_footer(stdscr)
        stdscr.refresh()
        tick += 1

        # Handle input
        try:
            key = stdscr.getch()
        except curses.error:
            key = -1

        if key == -1:
            continue

        if not cards and key != ord("n") and key != ord("q"):
            continue

        num_cards = len(cards)
        cols = max(1, (max_x - 2) // (CARD_WIDTH + 2)) if cards else 1

        if key == curses.KEY_DOWN:
            selected = min(selected + cols, num_cards - 1)
        elif key == curses.KEY_UP:
            selected = max(selected - cols, 0)
        elif key == curses.KEY_RIGHT:
            selected = min(selected + 1, num_cards - 1)
        elif key == curses.KEY_LEFT:
            selected = max(selected - 1, 0)
        elif key == ord("\n") and cards:
            # Patch in: switch tmux client to the agent's session
            # The switchboard session stays alive — Alt+s comes back here
            card = cards[selected]
            if card.tmux_session and tmux.session_exists(card.tmux_session):
                subprocess.run(
                    ["tmux", "switch-client", "-t", card.tmux_session],
                    check=False,
                )
                # Don't return — keep the switchboard running so Alt+s can come back
        elif key == ord("s") and cards:
            # Send message to agent
            card = cards[selected]
            msg = _prompt_input(stdscr, f"Send to {card.name}: ")
            if msg and card.tmux_session:
                try:
                    tmux.send_keys_to_pane(card.tmux_session, msg)
                except Exception:
                    pass
        elif key == ord("n"):
            # New worktree
            task = _prompt_input(stdscr, "Task description: ")
            if task:
                stdscr = _shell_out(stdscr, ["owt", "new", task])
        elif key == ord("d") and cards:
            # Drop (delete) worktree + status
            card = cards[selected]
            confirm = _prompt_input(stdscr, f"Delete '{card.name}'? (y/N): ")
            if confirm and confirm.lower() == "y":
                stdscr = _shell_out(stdscr, ["owt", "delete", card.name, "--yes"])
                # Fallback cleanup if owt delete failed
                if tracker.get_status(card.name):
                    tracker.remove_status(card.name)
                    if card.tmux_session:
                        try:
                            tmux.kill_session(card.tmux_session)
                        except Exception:
                            pass
                selected = min(selected, max(0, num_cards - 2))
        elif key == ord("S") and cards:
            # Ship: commit + merge + delete (one-shot completion)
            card = cards[selected]
            confirm = _prompt_input(stdscr, f"Ship '{card.name}'? (commit+merge+delete) (y/N): ")
            if confirm and confirm.lower() == "y":
                stdscr = _shell_out(stdscr, ["owt", "ship", card.name, "--yes"])
                selected = min(selected, max(0, num_cards - 2))
        elif key == ord("m") and cards:
            # Merge worktree
            card = cards[selected]
            confirm = _prompt_input(stdscr, f"Merge '{card.name}'? (y/N): ")
            if confirm and confirm.lower() == "y":
                stdscr = _shell_out(stdscr, ["owt", "merge", card.name])
                selected = min(selected, max(0, num_cards - 2))
        elif key == ord("f") and cards:
            # Show file overlap detail for selected card
            card = cards[selected]
            if card.overlap_count > 0 and card.overlap_names:
                _show_overlap_detail(stdscr, card, cached_file_map)
        elif key == ord("i") and cards:
            # Show detail panel for selected card
            card = cards[selected]
            _show_detail_panel(stdscr, card, tracker)
        elif key == ord("a") and cards:
            # Broadcast message to all agents
            msg = _prompt_input(stdscr, "Broadcast to all: ")
            if msg:
                for card in cards:
                    if card.tmux_session:
                        try:
                            tmux.send_keys_to_pane(card.tmux_session, msg)
                            tracker.record_command(card.name, msg)
                        except Exception:
                            pass
        elif key == ord("q"):
            return


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

    If already inside the switchboard session, runs the curses app directly.
    If outside tmux, creates the session and attaches.
    If inside another tmux session, switches to the switchboard session.
    """
    if _is_inside_switchboard_session():
        # We're already in the switchboard session — run curses directly
        curses.wrapper(_run_switchboard)
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

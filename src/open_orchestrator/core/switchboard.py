"""
Switchboard: curses-based card grid for multi-agent orchestration.

The switchboard is the command center for Open Orchestrator. It displays
all active worktrees as cards with status lights, and provides keyboard
shortcuts to navigate, patch into sessions, send messages, and manage
worktrees — all from one screen.

Metaphor: Like a telephone switchboard operator managing multiple lines.
"""

from __future__ import annotations

import curses
import subprocess
from dataclasses import dataclass

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


def _format_elapsed(status: WorktreeAIStatus) -> str:
    """Format time elapsed since last update."""
    if not status.updated_at:
        return ""
    from datetime import datetime

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


def _build_cards(tracker: StatusTracker) -> list[Card]:
    """Build card list from current status data."""
    tracker.reload()
    statuses = tracker.get_all_statuses()

    cards = []
    for s in statuses:
        cards.append(Card(
            name=s.worktree_name,
            status=s.activity_status,
            branch=s.branch,
            ai_tool=s.ai_tool,
            task=s.current_task,
            elapsed=_format_elapsed(s),
            tmux_session=s.tmux_session,
        ))

    return cards


def _draw_card(win: curses.window, y: int, x: int, card: Card, selected: bool, color_pairs: dict[str, int]) -> None:
    """Draw a single card at the given position."""
    max_y, max_x = win.getmaxyx()

    # Don't draw if out of bounds
    if y + CARD_HEIGHT > max_y or x + CARD_WIDTH > max_x:
        return

    border_attr = curses.A_BOLD | curses.A_REVERSE if selected else 0
    title_attr = curses.A_BOLD | curses.A_REVERSE if selected else curses.A_BOLD

    # Top border
    win.addstr(y, x, "\u250c" + "\u2500" * (CARD_WIDTH - 2) + "\u2510", border_attr)

    # Card title line
    name_trunc = card.name[:CARD_WIDTH - 6]
    win.addstr(y, x + 2, f" {name_trunc} ", title_attr)

    # Status line
    status_key = card.status.value if isinstance(card.status, AIActivityStatus) else str(card.status)
    light_char, _ = STATUS_LIGHTS.get(status_key, ("?", curses.COLOR_WHITE))
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

    # AI tool line
    win.addstr(y + 3, x, "\u2502" + " " * (CARD_WIDTH - 2) + "\u2502")
    win.addstr(y + 3, x + 2, card.ai_tool[:CARD_WIDTH - 4])
    win.addstr(y + 3, x + CARD_WIDTH - 1, "\u2502")

    # Task line
    task_str = card.task or "\u2014"
    task_trunc = task_str[:CARD_WIDTH - 4]
    win.addstr(y + 4, x, "\u2502" + " " * (CARD_WIDTH - 2) + "\u2502")
    win.addstr(y + 4, x + 2, task_trunc)
    win.addstr(y + 4, x + CARD_WIDTH - 1, "\u2502")

    # Bottom border
    win.addstr(y + 5, x, "\u2514" + "\u2500" * (CARD_WIDTH - 2) + "\u2518", border_attr)


def _draw_header(win: curses.window, cards: list[Card]) -> None:
    """Draw the header bar."""
    max_x = win.getmaxyx()[1]

    active = sum(1 for c in cards if c.status == AIActivityStatus.WORKING)
    total = len(cards)
    idle = total - active

    title = "SWITCHBOARD"
    stats = f"{total} lines  \u25cf{active} active \u25cb{idle}"

    # Draw header — pad to full width so A_REVERSE fills the line
    header = f"  {title:<30} {stats:>{max_x - 34}}"
    header = header.ljust(max_x - 1)
    win.addstr(0, 0, header[:max_x - 1], curses.A_BOLD | curses.A_REVERSE)


def _draw_footer(win: curses.window) -> None:
    """Draw the footer with key bindings."""
    max_y, max_x = win.getmaxyx()
    footer = "  [\u2191\u2193\u2190\u2192] navigate  [Enter] patch in  [s] send msg  [n] new  [d] drop  [m] merge  [q] quit"
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


def _run_switchboard(stdscr: curses.window) -> None:
    """Main switchboard loop."""
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.nodelay(True)
    stdscr.timeout(2000)  # Refresh every 2s

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

    while True:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()

        cards = _build_cards(tracker)

        _draw_header(stdscr, cards)

        if not cards:
            msg = "No active worktrees. Press [n] to create one, [q] to quit."
            stdscr.addstr(max_y // 2, max(0, (max_x - len(msg)) // 2), msg)
        else:
            # Calculate grid
            cols = max(1, (max_x - 2) // (CARD_WIDTH + 2))
            start_y = 2
            start_x = 1

            for i, card in enumerate(cards):
                row = i // cols
                col = i % cols
                y = start_y + row * (CARD_HEIGHT + 1)
                x = start_x + col * (CARD_WIDTH + 2)

                if y + CARD_HEIGHT < max_y - 1:
                    _draw_card(stdscr, y, x, card, selected == i, color_pairs)

        _draw_footer(stdscr)
        stdscr.refresh()

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
            # Patch in: switch to tmux session
            card = cards[selected]
            if card.tmux_session:
                curses.endwin()
                if tmux.is_inside_tmux():
                    subprocess.run(["tmux", "switch-client", "-t", card.tmux_session], check=False)
                else:
                    subprocess.run(["tmux", "attach-session", "-t", card.tmux_session], check=False)
                return
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
                curses.endwin()
                subprocess.run(["owt", "new", task], check=False)
                # Re-enter curses
                stdscr = curses.initscr()
                curses.noecho()
                curses.cbreak()
                stdscr.keypad(True)
                curses.curs_set(0)
                stdscr.nodelay(True)
                stdscr.timeout(2000)
        elif key == ord("d") and cards:
            # Drop (delete) worktree + status
            card = cards[selected]
            confirm = _prompt_input(stdscr, f"Delete '{card.name}'? (y/N): ")
            if confirm and confirm.lower() == "y":
                curses.endwin()
                # Try owt delete (handles worktree + tmux + status)
                result = subprocess.run(["owt", "delete", card.name, "--yes"], check=False, capture_output=True)
                if result.returncode != 0:
                    # Worktree may not exist — clean up status entry directly
                    tracker.remove_status(card.name)
                    # Also try killing tmux session
                    if card.tmux_session:
                        try:
                            tmux.kill_session(card.tmux_session)
                        except Exception:
                            pass
                stdscr = curses.initscr()
                curses.noecho()
                curses.cbreak()
                stdscr.keypad(True)
                curses.curs_set(0)
                stdscr.nodelay(True)
                stdscr.timeout(2000)
                selected = min(selected, max(0, num_cards - 2))
        elif key == ord("m") and cards:
            # Merge worktree
            card = cards[selected]
            confirm = _prompt_input(stdscr, f"Merge '{card.name}'? (y/N): ")
            if confirm and confirm.lower() == "y":
                curses.endwin()
                subprocess.run(["owt", "merge", card.name], check=False)
                stdscr = curses.initscr()
                curses.noecho()
                curses.cbreak()
                stdscr.keypad(True)
                curses.curs_set(0)
                stdscr.nodelay(True)
                stdscr.timeout(2000)
                selected = min(selected, max(0, num_cards - 2))
        elif key == ord("q"):
            return


def launch_switchboard() -> None:
    """Launch the switchboard UI."""
    curses.wrapper(_run_switchboard)

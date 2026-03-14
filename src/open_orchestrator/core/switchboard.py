"""
Switchboard: curses-based card grid for multi-agent orchestration.

The switchboard is the command center for Open Orchestrator. It displays
all active worktrees as cards with status lights, and provides keyboard
shortcuts to navigate, patch into sessions, send messages, and manage
worktrees — all from one screen.

The switchboard runs in its own tmux session ("owt-switchboard"). When
you patch into an agent session (Enter), the switchboard stays alive.
Alt+s from any agent session switches back to the switchboard. Press q
to exit completely back to the terminal.

Metaphor: Like a telephone switchboard operator managing multiple lines.
"""

from __future__ import annotations

import curses
import os
import re
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
SWITCHBOARD_SESSION = "owt-switchboard"


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


def _detect_pane_status(tmux_session: str | None) -> AIActivityStatus | None:
    """Detect agent status by capturing tmux pane content.

    Looks for prompt indicators that signal the agent is waiting for input
    or blocked on a permission/confirmation prompt.
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

    tail_text = "\n".join(reversed(tail))

    # Patterns that indicate the agent is waiting for user input
    # Claude Code: shows ">" prompt, or "❯" prompt when idle
    # Also: "What would you like to do?" / input prompts
    waiting_patterns = [
        r"^>\s*$",                           # Claude Code empty prompt
        r"^❯\s*$",                           # Alternative prompt
        r"\$\s*$",                            # Shell prompt (agent exited)
        r"What would you like",              # Claude asking for input
        r"How can I help",                   # Claude greeting
    ]

    # Patterns that indicate blocked on permission/confirmation
    blocked_patterns = [
        r"Allow\s",                          # Permission prompt
        r"\(y/N\)",                           # Yes/No confirmation
        r"\(Y/n\)",                           # Yes/No confirmation
        r"approve|deny|permit",              # Permission language
        r"Do you want to",                   # Confirmation prompt
        r"Press Enter to",                   # Waiting for keypress
    ]

    last_line = tail[0]  # Most recent non-empty line

    for pattern in blocked_patterns:
        if re.search(pattern, tail_text, re.IGNORECASE):
            return AIActivityStatus.BLOCKED

    for pattern in waiting_patterns:
        if re.search(pattern, last_line, re.IGNORECASE):
            return AIActivityStatus.WAITING

    return None


def _build_cards(tracker: StatusTracker) -> list[Card]:
    """Build card list from current status data.

    On each refresh, captures tmux pane content to detect whether
    agents are actually working, waiting for input, or blocked on
    a permission prompt — then updates the persisted status.
    """
    tracker.reload()
    statuses = tracker.get_all_statuses()

    cards = []
    for s in statuses:
        # Detect live pane status when the stored status says WORKING
        if s.activity_status == AIActivityStatus.WORKING:
            detected = _detect_pane_status(s.tmux_session)
            if detected and detected != s.activity_status:
                s.activity_status = detected
                s.updated_at = __import__("datetime").datetime.now()
                tracker.set_status(s)

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
    waiting = sum(1 for c in cards if c.status in (AIActivityStatus.WAITING, AIActivityStatus.BLOCKED))
    total = len(cards)
    idle = total - active - waiting

    title = "SWITCHBOARD"
    parts = [f"{total} lines", f"\u25cf{active} active"]
    if waiting:
        parts.append(f"\u26a0{waiting} waiting")
    parts.append(f"\u25cb{idle}")
    stats = "  ".join(parts)

    # Draw header — pad to full width so A_REVERSE fills the line
    header = f"  {title:<30} {stats:>{max_x - 34}}"
    header = header.ljust(max_x - 1)
    win.addstr(0, 0, header[:max_x - 1], curses.A_BOLD | curses.A_REVERSE)


def _draw_footer(win: curses.window) -> None:
    """Draw the footer with key bindings."""
    max_y, max_x = win.getmaxyx()
    footer = "  [\u2191\u2193\u2190\u2192] navigate  [Enter] patch in  [s] send  [n] new  [S] ship  [d] drop  [m] merge  [q] quit"
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


def _reinit_curses(stdscr: curses.window) -> curses.window:
    """Re-initialize curses after shelling out."""
    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    stdscr.keypad(True)
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(2000)
    return stdscr


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
                curses.endwin()
                subprocess.run(["owt", "new", task], check=False)
                stdscr = _reinit_curses(stdscr)
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
                stdscr = _reinit_curses(stdscr)
                selected = min(selected, max(0, num_cards - 2))
        elif key == ord("S") and cards:
            # Ship: commit + merge + delete (one-shot completion)
            card = cards[selected]
            confirm = _prompt_input(stdscr, f"Ship '{card.name}'? (commit+merge+delete) (y/N): ")
            if confirm and confirm.lower() == "y":
                curses.endwin()
                subprocess.run(["owt", "ship", card.name, "--yes"], check=False)
                stdscr = _reinit_curses(stdscr)
                selected = min(selected, max(0, num_cards - 2))
        elif key == ord("m") and cards:
            # Merge worktree
            card = cards[selected]
            confirm = _prompt_input(stdscr, f"Merge '{card.name}'? (y/N): ")
            if confirm and confirm.lower() == "y":
                curses.endwin()
                subprocess.run(["owt", "merge", card.name], check=False)
                stdscr = _reinit_curses(stdscr)
                selected = min(selected, max(0, num_cards - 2))
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

    Alt+b: switch back to the switchboard session
    Alt+c: create a new worktree (runs owt new in a popup)
    Alt+s: ship current worktree (commit + merge + delete)
    Alt+m: merge current worktree
    Alt+d: delete current worktree
    """
    # Alt+b: switch to the switchboard (b = board)
    subprocess.run(
        ["tmux", "bind-key", "-n", "M-b",
         "switch-client", "-t", SWITCHBOARD_SESSION],
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

    # Alt+s: ship the current worktree (commit + merge + delete)
    # Derives worktree name from the current tmux session name (owt-<name>)
    ship_script = (
        "wt_name=$(tmux display-message -p '#S' | sed 's/^owt-//'); "
        "if [ -n \"$wt_name\" ] && [ \"$wt_name\" != 'owt-switchboard' ]; then "
        "  tmux switch-client -t owt-switchboard; "
        "  owt ship \"$wt_name\" --yes; "
        "fi"
    )
    if (major, minor) >= (3, 2):
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-s",
             "display-popup", "-E", "-w", "80%", "-h", "50%",
             f"bash -c {_shell_quote(ship_script)}"],
            check=False, capture_output=True,
        )
    else:
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-s",
             "new-window", "-n", "ship",
             f"bash -c {_shell_quote(ship_script)}"],
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

    # Install Alt+s / Alt+c keybindings
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

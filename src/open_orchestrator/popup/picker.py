"""Popup picker for on-demand worktree pane creation.

This script is invoked by tmux display-popup (prefix+n) inside a workspace
session. It presents a curses-based interactive picker for selecting AI agents,
then writes the result as JSON to a temp file for `owt pane add --from-popup`.

Styled after dmux's agent picker: arrow keys to navigate, space to toggle,
Enter to launch, ESC to cancel.
"""

import curses
import json
import shutil
import sys
from typing import Any

# Theme colors are defined in open_orchestrator.core.theme.COLORS.
# Curses cannot use hex values directly; _get_theme_curses_color() maps
# COLORS["accent"] (#00d7d7) to curses.COLOR_CYAN.

# Agent definitions: (display_name, abbreviation, binary_name)
AGENTS = [
    ("Claude Code", "cc", "claude"),
    ("OpenCode", "oc", "opencode"),
    ("Codex", "cx", "codex"),
    ("Gemini CLI", "gc", "gemini"),
    ("Aider", "ai", "aider"),
    ("Amp", "am", "amp"),
    ("Kilo Code", "kc", "kilo-code"),
    ("Droid", "dr", "droid"),
]


def detect_installed() -> list[tuple[str, str, str, bool]]:
    """Return agents with installation status."""
    result = []
    for name, abbrev, binary in AGENTS:
        installed = shutil.which(binary) is not None
        result.append((name, abbrev, binary, installed))
    return result


def _get_theme_curses_color() -> int:
    """Get the curses color constant for the accent theme.

    Maps theme accent (#00d7d7) to the nearest curses constant.
    See open_orchestrator.core.theme.COLORS for canonical values.
    """
    return curses.COLOR_CYAN


def _init_colors() -> None:
    """Initialize shared color pairs used by all picker screens."""
    curses.use_default_colors()
    accent_color = _get_theme_curses_color()
    curses.init_pair(1, accent_color, -1)          # Title / active row
    curses.init_pair(2, curses.COLOR_GREEN, -1)     # Selected marker
    curses.init_pair(3, curses.COLOR_WHITE, -1)     # Unselected marker
    curses.init_pair(4, curses.COLOR_BLACK, -1)     # Dim text (footer)
    curses.init_pair(5, curses.COLOR_RED, -1)       # Not installed


def run_picker(stdscr: curses.window) -> dict[str, Any] | None:
    """Run the interactive agent picker."""
    curses.curs_set(0)  # Hide cursor
    _init_colors()

    agents = detect_installed()
    installed_agents = [a for a in agents if a[3]]

    if not installed_agents:
        stdscr.clear()
        stdscr.addstr(2, 2, "No AI agents found!", curses.color_pair(5) | curses.A_BOLD)
        stdscr.addstr(4, 2, "Install one of: claude, codex, gemini, aider, amp")
        stdscr.addstr(6, 2, "Press any key to exit...")
        stdscr.refresh()
        stdscr.getch()
        return None

    cursor = 0
    selected: set[int] = set()

    while True:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()

        # Title
        title = "Select Agent(s)"
        stdscr.addstr(1, 2, title, curses.color_pair(1) | curses.A_BOLD)

        # Subtitle
        subtitle = "Select one or more agents, then press Enter to launch"
        count_str = f"Selected: {len(selected)}/{len(installed_agents)}"
        stdscr.addstr(3, 4, subtitle, curses.A_DIM)
        # Right-align the count
        count_x = max(max_x - len(count_str) - 2, len(subtitle) + 6)
        if count_x + len(count_str) < max_x:
            stdscr.addstr(3, count_x, count_str, curses.color_pair(1))

        # Agent list
        for i, (name, abbrev, binary, installed) in enumerate(installed_agents):
            y = 5 + i
            if y >= max_y - 3:
                break

            is_selected = i in selected
            is_cursor = i == cursor

            # Marker
            if is_selected:
                marker = "\u2022"  # bullet
                marker_attr = curses.color_pair(2) | curses.A_BOLD
            else:
                marker = "\u25cb"  # open circle
                marker_attr = curses.color_pair(3)

            # Row styling
            if is_cursor:
                name_attr = curses.color_pair(1) | curses.A_BOLD
                abbrev_attr = curses.color_pair(1)
            else:
                name_attr = curses.A_NORMAL
                abbrev_attr = curses.A_DIM

            try:
                stdscr.addstr(y, 4, marker, marker_attr)
                name_text = f" {name}"[:max_x - 7] if len(name) + 7 > max_x else f" {name}"
                stdscr.addstr(y, 6, name_text, name_attr)
                abbrev_x = 6 + len(name_text) + 1
                if abbrev_x + len(abbrev) < max_x:
                    stdscr.addstr(y, abbrev_x, abbrev, abbrev_attr)
            except curses.error:
                pass

        # Footer
        footer_y = max_y - 2
        if footer_y > 5 + len(installed_agents):
            footer = "\u2191\u2193 navigate \u00b7 Space toggle \u00b7 Enter launch \u00b7 ESC cancel"
            footer_x = max(2, (max_x - len(footer)) // 2)
            if footer_x + len(footer) < max_x:
                stdscr.addstr(footer_y, footer_x, footer, curses.A_DIM)

        stdscr.refresh()

        # Input
        key = stdscr.getch()

        if key == 27:  # ESC
            return None
        elif key == curses.KEY_UP or key == ord("k"):
            cursor = (cursor - 1) % len(installed_agents)
        elif key == curses.KEY_DOWN or key == ord("j"):
            cursor = (cursor + 1) % len(installed_agents)
        elif key == ord(" "):  # Space toggle
            if cursor in selected:
                selected.discard(cursor)
            else:
                selected.add(cursor)
        elif key in (curses.KEY_ENTER, 10, 13):  # Enter
            if not selected:
                # Nothing toggled — launch what cursor is pointing at
                selected.add(cursor)
            chosen = [installed_agents[i] for i in sorted(selected)]
            return {
                "agents": [
                    {"name": name, "abbrev": abbrev, "binary": binary}
                    for name, abbrev, binary, _ in chosen
                ]
            }


def get_branch_name(stdscr: curses.window) -> str | None:
    """Prompt user for a branch name after agent selection."""
    curses.curs_set(1)  # Show cursor for text input
    _init_colors()
    stdscr.clear()
    max_y, max_x = stdscr.getmaxyx()

    stdscr.addstr(1, 2, "Branch Name", curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(3, 4, "Enter branch name for the new worktree:", curses.A_DIM)

    # Input field
    input_y = 5
    input_x = 4
    stdscr.addstr(input_y, input_x, "> ")

    # Footer
    footer_y = max_y - 2
    footer = "Enter confirm · ESC cancel"
    footer_x = max(2, (max_x - len(footer)) // 2)
    if footer_x + len(footer) < max_x:
        stdscr.addstr(footer_y, footer_x, footer, curses.A_DIM)

    stdscr.refresh()

    branch = ""
    cursor_pos = 0
    field_x = input_x + 2  # after "> "

    while True:
        # Redraw input line
        stdscr.move(input_y, field_x)
        stdscr.clrtoeol()
        stdscr.addstr(input_y, field_x, branch)
        stdscr.move(input_y, field_x + cursor_pos)
        stdscr.refresh()

        key = stdscr.getch()

        if key == 27:  # ESC
            return None
        elif key in (curses.KEY_ENTER, 10, 13):
            stripped = branch.strip()
            return stripped if stripped else None
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if cursor_pos > 0:
                branch = branch[:cursor_pos - 1] + branch[cursor_pos:]
                cursor_pos -= 1
        elif key == curses.KEY_LEFT:
            cursor_pos = max(0, cursor_pos - 1)
        elif key == curses.KEY_RIGHT:
            cursor_pos = min(len(branch), cursor_pos + 1)
        elif 32 <= key <= 126:  # Printable characters
            max_len = max_x - field_x - 2
            if len(branch) < max_len:
                branch = branch[:cursor_pos] + chr(key) + branch[cursor_pos:]
                cursor_pos += 1


def _picker_flow(stdscr: curses.window) -> dict[str, Any] | None:
    """Run the full picker flow: agent selection → branch name input."""
    result = run_picker(stdscr)
    if result is None:
        return None

    branch = get_branch_name(stdscr)
    if branch is None:
        return None

    first_agent = result["agents"][0]
    return {
        "branch": branch,
        "ai_tool": first_agent["binary"],
        "agents": result["agents"],
    }


def main() -> None:
    """Run the popup picker and write result JSON to the output file."""
    if len(sys.argv) < 2:
        print("Usage: owt-popup <output-json-path>")
        sys.exit(1)

    output_path = sys.argv[1]

    output = curses.wrapper(_picker_flow)
    if output is None:
        sys.exit(1)

    with open(output_path, "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()

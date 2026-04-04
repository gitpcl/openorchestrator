"""Switchboard tmux session lifecycle management.

Extracted from switchboard.py to keep file sizes manageable.
Provides functions for launching the switchboard in tmux, installing
global keybindings, and resolving worktree names from session names.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

from open_orchestrator.core.tmux_manager import TmuxManager

logger = logging.getLogger(__name__)

SWITCHBOARD_SESSION = "owt-switchboard"


def detect_terminal_background() -> str | None:
    """Detect terminal background color using OSC 11 query.

    Sends the OSC 11 escape sequence (``\\033]11;?\\033\\\\``) and parses
    the terminal's response (``\\033]11;rgb:RRRR/GGGG/BBBB\\033\\\\``).
    Works with iTerm2, Kitty, Alacritty, WezTerm, macOS Terminal, and
    most modern terminal emulators.

    Returns hex color string (e.g. ``#1a1b2e``) or None if detection fails.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None

    try:
        import select
        import termios
        import tty
    except ImportError:
        logger.debug("termios/tty not available (non-Unix platform)")
        return None

    try:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            # Send OSC 11 query
            os.write(sys.stdout.fileno(), b"\033]11;?\033\\")

            # Read response with timeout
            response = b""
            while select.select([sys.stdin], [], [], 0.15)[0]:
                chunk = os.read(fd, 64)
                if not chunk:
                    break
                response += chunk
                # Stop once we see the string terminator
                if b"\033\\" in response or b"\x07" in response:
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        decoded = response.decode("latin-1", errors="replace")
        return _parse_osc11_response(decoded)
    except Exception:
        logger.debug("Terminal background detection failed", exc_info=True)
        return None


def _parse_osc11_response(response: str) -> str | None:
    """Parse OSC 11 response into hex color.

    Response format: ``\\033]11;rgb:RRRR/GGGG/BBBB\\033\\\\``
    where R/G/B are 2 or 4 hex digits per channel.
    """
    match = re.search(r"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", response)
    if not match:
        return None

    r_raw, g_raw, b_raw = match.group(1), match.group(2), match.group(3)

    def _to_byte(channel: str) -> int:
        if len(channel) <= 2:
            return int(channel, 16)
        # 4-digit channel: take high byte (e.g. "1a1a" → 0x1a)
        return int(channel[:2], 16)

    r, g, b = _to_byte(r_raw), _to_byte(g_raw), _to_byte(b_raw)
    return f"#{r:02x}{g:02x}{b:02x}"


def _is_inside_switchboard_session() -> bool:
    """Check if we're already running inside the switchboard tmux session."""
    if "TMUX" not in os.environ:
        return False
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#S"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() == SWITCHBOARD_SESSION
    except subprocess.CalledProcessError:
        return False


def _resolve_worktree_from_session(session_name: str) -> str | None:
    """Given a tmux session name like 'owt-foo', return the worktree name 'foo'."""
    prefix = "owt-"
    if session_name.startswith(prefix):
        return session_name[len(prefix) :]
    return None


def _shell_quote(s: str) -> str:
    """Quote a string for shell embedding in tmux commands."""
    import shlex

    return shlex.quote(s)


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
        check=False,
        capture_output=True,
    )

    # Alt+c: create new worktree via popup (tmux >= 3.2) or new window
    major, minor = TmuxManager.get_tmux_version()
    if (major, minor) >= (3, 2):
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-c", "display-popup", "-E", "-w", "80%", "-h", "50%", "owt new"],
            check=False,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-c", "new-window", "-n", "new-worktree", "owt new"],
            check=False,
            capture_output=True,
        )

    # Alt+s: switch back to the switchboard session (s = switchboard)
    subprocess.run(
        ["tmux", "bind-key", "-n", "M-s", "switch-client", "-t", SWITCHBOARD_SESSION],
        check=False,
        capture_output=True,
    )

    # Alt+m: merge the current worktree
    merge_script = (
        "wt_name=$(tmux display-message -p '#S' | sed 's/^owt-//'); "
        'if [ -n "$wt_name" ] && [ "$wt_name" != \'owt-switchboard\' ]; then '
        "  tmux switch-client -t owt-switchboard; "
        '  owt merge "$wt_name"; '
        "fi"
    )
    if (major, minor) >= (3, 2):
        subprocess.run(
            [
                "tmux",
                "bind-key",
                "-n",
                "M-m",
                "display-popup",
                "-E",
                "-w",
                "80%",
                "-h",
                "50%",
                f"bash -c {_shell_quote(merge_script)}",
            ],
            check=False,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-m", "new-window", "-n", "merge", f"bash -c {_shell_quote(merge_script)}"],
            check=False,
            capture_output=True,
        )

    # Alt+d: delete the current worktree
    delete_script = (
        "wt_name=$(tmux display-message -p '#S' | sed 's/^owt-//'); "
        'if [ -n "$wt_name" ] && [ "$wt_name" != \'owt-switchboard\' ]; then '
        "  tmux switch-client -t owt-switchboard; "
        '  owt delete "$wt_name" --yes; '
        "fi"
    )
    if (major, minor) >= (3, 2):
        subprocess.run(
            [
                "tmux",
                "bind-key",
                "-n",
                "M-d",
                "display-popup",
                "-E",
                "-w",
                "80%",
                "-h",
                "50%",
                f"bash -c {_shell_quote(delete_script)}",
            ],
            check=False,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["tmux", "bind-key", "-n", "M-d", "new-window", "-n", "delete", f"bash -c {_shell_quote(delete_script)}"],
            check=False,
            capture_output=True,
        )


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
        # Background was detected before tmux and passed via OWT_BACKGROUND
        from open_orchestrator.core.switchboard import SwitchboardApp

        app = SwitchboardApp(detected_bg=os.environ.get("OWT_BACKGROUND"))
        app.run()
        return

    # Detect terminal background NOW, before entering tmux
    bg = detect_terminal_background()

    tmux = TmuxManager()

    # Create the switchboard session if it doesn't exist
    if not tmux.session_exists(SWITCHBOARD_SESSION):
        # Propagate detected background via tmux set-environment (avoids shell quoting)
        if bg:
            subprocess.run(
                ["tmux", "set-environment", "-g", "OWT_BACKGROUND", bg],
                capture_output=True,
                check=False,
            )
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", SWITCHBOARD_SESSION, "-n", "switchboard", "owt"],
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

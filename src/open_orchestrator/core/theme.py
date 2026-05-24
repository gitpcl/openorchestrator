"""Theme resolution for Open Orchestrator UI.

Detection (OSC 11 + ``$COLORFGBG``), active-palette state, and the public
``get_palette`` / ``set_active_palette`` / ``status_color`` API. Palette
*data* — the ``ThemePalette`` dataclass, the four concrete palettes, the
``PALETTES`` map, the legacy ``COLORS`` / ``STATUS_*`` dicts, and the ANSI
Rich color tables — lives in :mod:`core.theme_palettes`. Palette names
are re-exported from here for backwards compatibility.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys

from open_orchestrator.core.theme_palettes import (
    ANSI_STATUS_BORDER_COLORS,
    ANSI_STATUS_COLORS,
    COLORS,
    DARK_ANSI_PALETTE,
    DARK_PALETTE,
    LIGHT_ANSI_PALETTE,
    LIGHT_PALETTE,
    PALETTES,
    STATUS_BORDER_COLORS,
    STATUS_COLORS,
    VALID_THEME_NAMES,
    ThemePalette,
    refresh_legacy_dicts,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ANSI_STATUS_BORDER_COLORS",
    "ANSI_STATUS_COLORS",
    "COLORS",
    "DARK_ANSI_PALETTE",
    "DARK_PALETTE",
    "LIGHT_ANSI_PALETTE",
    "LIGHT_PALETTE",
    "PALETTES",
    "STATUS_BORDER_COLORS",
    "STATUS_COLORS",
    "VALID_THEME_NAMES",
    "ThemePalette",
    "detect_terminal_theme",
    "get_active_palette",
    "get_palette",
    "reset_detection_cache",
    "set_active_palette",
    "status_color",
]


# ---------------------------------------------------------------------------
# Terminal background detection
# ---------------------------------------------------------------------------


# Cache the detection result for the process lifetime so we don't re-query
# the terminal on every UI refresh.
_DETECTED_THEME: str | None = None


def _luminance(r: int, g: int, b: int) -> float:
    """ITU-R BT.709 luminance formula in [0, 1]."""
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _detect_via_osc11(timeout_ms: int = 200) -> str | None:
    """Query the terminal background via OSC 11.

    Sends ``ESC ] 11 ; ? ESC \\``, parses ``rgb:RRRR/GGGG/BBBB`` response,
    classifies as ``dark`` (luminance <= 0.5) or ``light`` (> 0.5).

    Returns None if the terminal does not respond within ``timeout_ms``.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None

    try:
        import select
        import termios
        import tty
    except ImportError:  # pragma: no cover - non-POSIX
        return None

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except (termios.error, OSError):
        return None

    try:
        tty.setcbreak(fd, termios.TCSANOW)
        sys.stdout.write("\x1b]11;?\x1b\\")
        sys.stdout.flush()

        timeout_s = timeout_ms / 1000.0
        ready, _, _ = select.select([fd], [], [], timeout_s)
        if not ready:
            return None
        chunks: list[str] = []
        deadline = timeout_s
        # Read until terminator (ESC \ or BEL) or timeout
        while True:
            ready, _, _ = select.select([fd], [], [], deadline)
            if not ready:
                break
            char = os.read(fd, 32).decode("utf-8", errors="replace")
            if not char:
                break
            chunks.append(char)
            if "\x1b\\" in char or "\x07" in char:
                break
            deadline = 0.05
        response = "".join(chunks)
    except (OSError, termios.error):
        return None
    finally:
        with contextlib.suppress(termios.error, OSError):
            termios.tcsetattr(fd, termios.TCSANOW, old_settings)

    return _parse_osc11_response(response)


def _parse_osc11_response(response: str) -> str | None:
    """Parse an OSC 11 reply into 'dark' or 'light'."""
    match = re.search(
        r"rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})",
        response,
    )
    if match is None:
        return None
    # Normalize each channel to 0-255 (responses can be 8/16-bit)
    components: list[int] = []
    for raw in match.groups():
        value = int(raw, 16)
        if len(raw) == 4:
            value = value >> 8
        elif len(raw) == 2:
            value = value
        elif len(raw) == 1:
            value = value * 17
        components.append(min(255, max(0, value)))
    r, g, b = components
    return "light" if _luminance(r, g, b) > 0.5 else "dark"


def _detect_via_colorfgbg() -> str | None:
    """Fallback: read ``$COLORFGBG`` and classify by background index.

    Format is ``fg;bg`` where bg is a numeric color index. Indices 0-6 and 8
    are dark; 7 and 9-15 are light.
    """
    raw = os.environ.get("COLORFGBG")
    if not raw:
        return None
    parts = raw.split(";")
    if len(parts) < 2:
        return None
    try:
        bg = int(parts[-1])
    except ValueError:
        return None
    if 0 <= bg <= 6 or bg == 8:
        return "dark"
    if bg == 7 or 9 <= bg <= 15:
        return "light"
    return None


def detect_terminal_theme() -> str:
    """Detect the terminal background and return ``'dark'`` or ``'light'``.

    Tries OSC 11 first (200ms timeout), then ``$COLORFGBG``, then defaults
    to ``'dark'``. Result is cached so repeated calls are free.
    """
    global _DETECTED_THEME
    if _DETECTED_THEME is not None:
        return _DETECTED_THEME
    detected = _detect_via_osc11()
    if detected is None:
        detected = _detect_via_colorfgbg()
    if detected is None:
        detected = "dark"
    _DETECTED_THEME = detected
    return detected


def reset_detection_cache() -> None:
    """Clear the cached detection result. Used by tests."""
    global _DETECTED_THEME
    _DETECTED_THEME = None


# ---------------------------------------------------------------------------
# Active palette resolution
# ---------------------------------------------------------------------------


_ACTIVE_PALETTE: ThemePalette = DARK_PALETTE


def get_palette(name: str | None = None) -> ThemePalette:
    """Resolve a palette by name.

    Args:
        name: One of ``auto``, ``dark``, ``light``, ``dark-ansi``,
            ``light-ansi``, or None (treated as ``auto``).

    Returns:
        The matching ThemePalette. ``auto`` runs detection.
    """
    if name is None or name == "auto":
        detected = detect_terminal_theme()
        return PALETTES.get(detected, DARK_PALETTE)
    if name not in PALETTES:
        raise ValueError(f"Unknown theme '{name}'. Valid: {', '.join(VALID_THEME_NAMES)}")
    return PALETTES[name]


def set_active_palette(name: str | None = None) -> ThemePalette:
    """Set and return the process-wide active palette.

    Updates the legacy ``COLORS`` / ``STATUS_COLORS`` / ``STATUS_BORDER_COLORS``
    dicts in place so existing consumers reflect the new palette without
    code changes.
    """
    global _ACTIVE_PALETTE
    palette = get_palette(name)
    _ACTIVE_PALETTE = palette
    refresh_legacy_dicts(palette)
    return palette


def get_active_palette() -> ThemePalette:
    """Return the currently active palette (defaults to dark)."""
    return _ACTIVE_PALETTE


def status_color(status: str) -> str:
    """Return an ANSI color name suitable for Rich markup for a status."""
    return ANSI_STATUS_COLORS.get(status, "white")

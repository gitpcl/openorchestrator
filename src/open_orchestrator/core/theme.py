"""Centralized theme system for Open Orchestrator UI.

Sprint 020 introduces a multi-palette system with terminal background
detection so the switchboard, CLI, and curses popup all adapt to the
user's terminal colors.

Four palette variants are provided:

- ``dark``      — Material Design 2 dark theme (legacy default)
- ``light``     — Material Design 2 light theme
- ``dark-ansi`` — pure ANSI color names (inherit terminal palette, dark)
- ``light-ansi``— pure ANSI color names (inherit terminal palette, light)

The active palette is selected by ``get_palette(name)``. With ``name='auto'``
(or ``None``), terminal background is detected via OSC 11 with a
``$COLORFGBG`` fallback, and the matching palette is returned. The result
is cached for the process lifetime.

The legacy ``COLORS`` / ``STATUS_COLORS`` / ``STATUS_BORDER_COLORS`` dicts
remain available for backward compatibility — they now resolve from the
active palette dynamically.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, fields

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ThemePalette — semantic color keys (25+ slots, identical across variants)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThemePalette:
    """Semantic color palette consumed by all UI surfaces.

    Hex variants use Material Design tones; ANSI variants use Rich/curses
    color names so the terminal renders them with its own palette.

    All four palettes (dark, light, dark-ansi, light-ansi) MUST share the
    exact same field set so consumers can switch palettes at runtime
    without missing keys.
    """

    name: str = "dark"
    is_ansi: bool = False

    # Surfaces
    background: str = "#121212"
    surface: str = "#2E2E2E"
    surface_1dp: str = "#1E1E1E"
    surface_2dp: str = "#222222"
    surface_3dp: str = "#242424"
    surface_4dp: str = "#272727"
    surface_6dp: str = "#2C2C2C"
    surface_8dp: str = "#2E2E2E"
    surface_12dp: str = "#333333"
    surface_16dp: str = "#353535"
    surface_24dp: str = "#383838"
    header_bg: str = "#1E1E1E"

    # Borders
    border_subtle: str = "#2C2C2C"
    border_inactive: str = "#3E3E3E"
    border_active: str = "#5E5E5E"
    card_border: str = "#3E3E3E"
    input_border: str = "#757575"

    # Text
    text_primary: str = "#DEDEDE"
    text_secondary: str = "#999999"
    text_disabled: str = "#616161"

    # Status
    status_working: str = "#81C784"
    status_idle: str = "#90A4AE"
    status_blocked: str = "#FFB74D"
    status_error: str = "#CF6679"
    status_completed: str = "#80CBC4"
    status_unknown: str = "#757575"

    # Toasts
    toast_info: str = "#64B5F6"
    toast_success: str = "#81C784"
    toast_warning: str = "#FFB74D"
    toast_error: str = "#CF6679"

    # Textual app theme name (textual-dark / textual-light / textual-ansi)
    textual_theme: str = "textual-dark"

    def to_dict(self) -> dict[str, str]:
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name not in ("name", "is_ansi") and isinstance(getattr(self, f.name), str)
        }


# ---------------------------------------------------------------------------
# Concrete palettes
# ---------------------------------------------------------------------------


DARK_PALETTE = ThemePalette(
    name="dark",
    is_ansi=False,
    # Surfaces — Material Design 2 dark theme elevation overlays
    background="#121212",
    surface="#2E2E2E",
    surface_1dp="#1E1E1E",
    surface_2dp="#222222",
    surface_3dp="#242424",
    surface_4dp="#272727",
    surface_6dp="#2C2C2C",
    surface_8dp="#2E2E2E",
    surface_12dp="#333333",
    surface_16dp="#353535",
    surface_24dp="#383838",
    header_bg="#1E1E1E",
    # Borders
    border_subtle="#2C2C2C",
    border_inactive="#3E3E3E",
    border_active="#5E5E5E",
    card_border="#3E3E3E",
    input_border="#757575",
    # Text — Material white at opacity levels
    text_primary="#DEDEDE",
    text_secondary="#999999",
    text_disabled="#616161",
    # Status — desaturated 200/300 tones for dark surfaces
    status_working="#81C784",
    status_idle="#90A4AE",
    status_blocked="#FFB74D",
    status_error="#CF6679",
    status_completed="#80CBC4",
    status_unknown="#757575",
    # Toasts
    toast_info="#64B5F6",
    toast_success="#81C784",
    toast_warning="#FFB74D",
    toast_error="#CF6679",
    textual_theme="textual-dark",
)


LIGHT_PALETTE = ThemePalette(
    name="light",
    is_ansi=False,
    # Surfaces — Material Design 2 light theme
    background="#FAFAFA",
    surface="#FFFFFF",
    surface_1dp="#F5F5F5",
    surface_2dp="#EEEEEE",
    surface_3dp="#E0E0E0",
    surface_4dp="#DDDDDD",
    surface_6dp="#D5D5D5",
    surface_8dp="#CFCFCF",
    surface_12dp="#BDBDBD",
    surface_16dp="#9E9E9E",
    surface_24dp="#757575",
    header_bg="#F5F5F5",
    # Borders
    border_subtle="#E0E0E0",
    border_inactive="#BDBDBD",
    border_active="#757575",
    card_border="#BDBDBD",
    input_border="#9E9E9E",
    # Text — Material black at opacity levels
    text_primary="#212121",
    text_secondary="#616161",
    text_disabled="#9E9E9E",
    # Status — saturated 700/800 tones for light surfaces
    status_working="#2E7D32",
    status_idle="#546E7A",
    status_blocked="#EF6C00",
    status_error="#C62828",
    status_completed="#00796B",
    status_unknown="#757575",
    # Toasts
    toast_info="#1976D2",
    toast_success="#2E7D32",
    toast_warning="#EF6C00",
    toast_error="#C62828",
    textual_theme="textual-light",
)


# ANSI palettes inherit terminal colors. Every value is a Rich/curses
# color name (no hex), so the terminal's own palette decides the look.

DARK_ANSI_PALETTE = ThemePalette(
    name="dark-ansi",
    is_ansi=True,
    # Surfaces — use 'default' which Textual maps to terminal background
    background="default",
    surface="default",
    surface_1dp="default",
    surface_2dp="default",
    surface_3dp="default",
    surface_4dp="default",
    surface_6dp="default",
    surface_8dp="default",
    surface_12dp="default",
    surface_16dp="default",
    surface_24dp="default",
    header_bg="default",
    # Borders
    border_subtle="dim",
    border_inactive="dim",
    border_active="white",
    card_border="dim",
    input_border="white",
    # Text — terminal's foreground
    text_primary="default",
    text_secondary="dim",
    text_disabled="bright_black",
    # Status — ANSI named colors
    status_working="green",
    status_idle="blue",
    status_blocked="yellow",
    status_error="red",
    status_completed="cyan",
    status_unknown="white",
    # Toasts
    toast_info="blue",
    toast_success="green",
    toast_warning="yellow",
    toast_error="red",
    textual_theme="textual-ansi",
)


LIGHT_ANSI_PALETTE = ThemePalette(
    name="light-ansi",
    is_ansi=True,
    background="default",
    surface="default",
    surface_1dp="default",
    surface_2dp="default",
    surface_3dp="default",
    surface_4dp="default",
    surface_6dp="default",
    surface_8dp="default",
    surface_12dp="default",
    surface_16dp="default",
    surface_24dp="default",
    header_bg="default",
    border_subtle="dim",
    border_inactive="dim",
    border_active="black",
    card_border="dim",
    input_border="black",
    text_primary="default",
    text_secondary="dim",
    text_disabled="bright_black",
    status_working="green",
    status_idle="blue",
    status_blocked="yellow",
    status_error="red",
    status_completed="cyan",
    status_unknown="black",
    toast_info="blue",
    toast_success="green",
    toast_warning="yellow",
    toast_error="red",
    textual_theme="textual-ansi",
)


PALETTES: dict[str, ThemePalette] = {
    "dark": DARK_PALETTE,
    "light": LIGHT_PALETTE,
    "dark-ansi": DARK_ANSI_PALETTE,
    "light-ansi": LIGHT_ANSI_PALETTE,
}


VALID_THEME_NAMES = ("auto", "dark", "light", "dark-ansi", "light-ansi")


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
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old_settings)
        except (termios.error, OSError):
            pass

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
    _refresh_legacy_dicts(palette)
    return palette


def get_active_palette() -> ThemePalette:
    """Return the currently active palette (defaults to dark)."""
    return _ACTIVE_PALETTE


# ---------------------------------------------------------------------------
# Legacy compatibility — COLORS / STATUS_COLORS / STATUS_BORDER_COLORS
# ---------------------------------------------------------------------------


def _build_colors_from_palette(p: ThemePalette) -> dict[str, str]:
    return {
        "background": p.background,
        "surface_1dp": p.surface_1dp,
        "surface_2dp": p.surface_2dp,
        "surface_3dp": p.surface_3dp,
        "surface_4dp": p.surface_4dp,
        "surface_6dp": p.surface_6dp,
        "surface_8dp": p.surface_8dp,
        "surface_12dp": p.surface_12dp,
        "surface_16dp": p.surface_16dp,
        "surface_24dp": p.surface_24dp,
        "border_subtle": p.border_subtle,
        "border_inactive": p.border_inactive,
        "border_active": p.border_active,
        "status_working": p.status_working,
        "status_idle": p.status_idle,
        "status_blocked": p.status_blocked,
        "status_error": p.status_error,
        "status_completed": p.status_completed,
        "status_unknown": p.status_unknown,
        "text_primary": p.text_primary,
        "text_secondary": p.text_secondary,
        "text_disabled": p.text_disabled,
        "surface": p.surface,
        "header_bg": p.header_bg,
        "input_border": p.input_border,
        "card_border": p.card_border,
        "toast_info": p.toast_info,
        "toast_success": p.toast_success,
        "toast_warning": p.toast_warning,
        "toast_error": p.toast_error,
    }


def _build_status_colors_from_palette(p: ThemePalette) -> dict[str, str]:
    return {
        "working": p.status_working,
        "idle": p.status_idle,
        "blocked": p.status_blocked,
        "waiting": p.status_blocked,
        "completed": p.status_completed,
        "error": p.status_error,
        "unknown": p.status_unknown,
    }


def _build_status_border_colors_from_palette(p: ThemePalette) -> dict[str, str]:
    return {
        "working": p.status_working,
        "idle": p.border_inactive,
        "blocked": p.status_error,
        "waiting": p.status_blocked,
        "completed": p.status_completed,
        "error": p.status_error,
        "unknown": p.border_inactive,
    }


# Mutable dicts so legacy consumers see updates after set_active_palette().
COLORS: dict[str, str] = _build_colors_from_palette(DARK_PALETTE)
STATUS_COLORS: dict[str, str] = _build_status_colors_from_palette(DARK_PALETTE)
STATUS_BORDER_COLORS: dict[str, str] = _build_status_border_colors_from_palette(DARK_PALETTE)


def _refresh_legacy_dicts(palette: ThemePalette) -> None:
    """Update the legacy COLORS dicts in place from the active palette."""
    COLORS.clear()
    COLORS.update(_build_colors_from_palette(palette))
    STATUS_COLORS.clear()
    STATUS_COLORS.update(_build_status_colors_from_palette(palette))
    STATUS_BORDER_COLORS.clear()
    STATUS_BORDER_COLORS.update(_build_status_border_colors_from_palette(palette))


# ---------------------------------------------------------------------------
# ANSI status colors for Rich CLI output (always terminal-friendly)
# ---------------------------------------------------------------------------

# These named colors inherit the terminal's palette, so Rich output adapts
# to whatever the user's terminal theme provides. They are independent of
# the active palette and used by CLI commands directly.

ANSI_STATUS_COLORS: dict[str, str] = {
    "working": "green",
    "idle": "blue",
    "blocked": "yellow",
    "waiting": "yellow",
    "completed": "cyan",
    "error": "red",
    "unknown": "white",
}

ANSI_STATUS_BORDER_COLORS: dict[str, str] = {
    "working": "green",
    "idle": "dim",
    "blocked": "red",
    "waiting": "yellow",
    "completed": "cyan",
    "error": "red",
    "unknown": "dim",
}


def status_color(status: str) -> str:
    """Return an ANSI color name suitable for Rich markup for a status."""
    return ANSI_STATUS_COLORS.get(status, "white")

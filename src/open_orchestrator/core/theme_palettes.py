"""Palette constants for the Open Orchestrator theme system.

Extracted from :mod:`core.theme` so the data and the resolution logic live in
separate files. ``theme.py`` keeps detection (OSC 11, $COLORFGBG), active
palette state, and the public ``get_palette`` / ``status_color`` API.

All four palettes share the same field set on :class:`ThemePalette`; runtime
code can flip palettes without missing keys. The legacy ``COLORS`` /
``STATUS_COLORS`` / ``STATUS_BORDER_COLORS`` dicts are also defined here and
mutated in place by :func:`refresh_legacy_dicts` so older call sites keep
working unmodified.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


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
# Legacy COLORS / STATUS_COLORS / STATUS_BORDER_COLORS — derived from a palette
# ---------------------------------------------------------------------------


def build_colors_from_palette(p: ThemePalette) -> dict[str, str]:
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


def build_status_colors_from_palette(p: ThemePalette) -> dict[str, str]:
    return {
        "working": p.status_working,
        "idle": p.status_idle,
        "blocked": p.status_blocked,
        "waiting": p.status_blocked,
        "completed": p.status_completed,
        "error": p.status_error,
        "unknown": p.status_unknown,
    }


def build_status_border_colors_from_palette(p: ThemePalette) -> dict[str, str]:
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
COLORS: dict[str, str] = build_colors_from_palette(DARK_PALETTE)
STATUS_COLORS: dict[str, str] = build_status_colors_from_palette(DARK_PALETTE)
STATUS_BORDER_COLORS: dict[str, str] = build_status_border_colors_from_palette(DARK_PALETTE)


def refresh_legacy_dicts(palette: ThemePalette) -> None:
    """Update the legacy COLORS dicts in place from the active palette."""
    COLORS.clear()
    COLORS.update(build_colors_from_palette(palette))
    STATUS_COLORS.clear()
    STATUS_COLORS.update(build_status_colors_from_palette(palette))
    STATUS_BORDER_COLORS.clear()
    STATUS_BORDER_COLORS.update(build_status_border_colors_from_palette(palette))


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

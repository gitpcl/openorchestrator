"""Centralized theme system for Open Orchestrator UI.

Color values follow Material Design 2 dark theme guidelines:
https://m2.material.io/design/color/dark-theme.html

Key principles:
- Background #121212, surfaces use white overlay at elevation-based opacity
- Text uses high-emphasis (87%), medium-emphasis (60%), disabled (38%) opacity
- Status colors are desaturated (200-weight tonal variants) for dark surfaces
- Error uses #CF6679 (desaturated) instead of bright red
"""

from __future__ import annotations

# Material Design 2 dark theme surface elevation overlays.
# Each level applies white (#FFFFFF) at the given opacity over #121212.
# 0dp = #121212, 1dp = #1E1E1E, 2dp = #222222, 3dp = #242424,
# 4dp = #272727, 6dp = #2C2C2C, 8dp = #2E2E2E, 12dp = #333333,
# 16dp = #353535, 24dp = #383838

# Single source of truth for all colors across the application.
# Used by: switchboard.py (Textual), tmux_manager.py, picker.py (curses)
COLORS = {
    # Core palette — Material Design 2 dark theme surfaces
    "background": "#121212",       # 0dp elevation (app background)
    "surface_1dp": "#1E1E1E",      # 1dp (card resting)
    "surface_2dp": "#222222",      # 2dp (button resting)
    "surface_3dp": "#242424",      # 3dp
    "surface_4dp": "#272727",      # 4dp (app bar resting)
    "surface_6dp": "#2C2C2C",      # 6dp (FAB, snackbar)
    "surface_8dp": "#2E2E2E",      # 8dp (modal, dialog)
    "surface_12dp": "#333333",     # 12dp (elevated card)
    "surface_16dp": "#353535",     # 16dp (nav drawer)
    "surface_24dp": "#383838",     # 24dp (top dialog)

    # Borders — white at low opacity on dark surfaces
    "border_subtle": "#2C2C2C",    # ~6dp overlay, barely visible
    "border_inactive": "#3E3E3E",  # ~9dp, muted separation
    "border_active": "#5E5E5E",    # Medium emphasis border

    # Status — desaturated (200-weight) for dark surfaces
    "status_working": "#81C784",   # Green 300 (Material)
    "status_idle": "#90A4AE",      # Blue Grey 300
    "status_blocked": "#FFB74D",   # Orange 300
    "status_error": "#CF6679",     # Material dark error
    "status_completed": "#80CBC4",  # Teal 200
    "status_unknown": "#757575",   # Grey 500

    # Text — Material white at opacity levels
    "text_primary": "#DEDEDE",     # High emphasis (87% white)
    "text_secondary": "#999999",   # Medium emphasis (60% white)
    "text_disabled": "#616161",    # Disabled (38% white)

    # Surfaces & UI chrome
    "surface": "#2E2E2E",         # 8dp — dialogs, modals
    "header_bg": "#1E1E1E",       # 1dp — top/bottom bars
    "input_border": "#757575",    # Medium emphasis for input accent
    "card_border": "#3E3E3E",     # Subtle card separation

    # Toast variants — desaturated for dark surfaces
    "toast_info": "#64B5F6",      # Blue 300
    "toast_success": "#81C784",   # Green 300
    "toast_warning": "#FFB74D",   # Orange 300
    "toast_error": "#CF6679",     # Material dark error
}

# Map status names to theme colors (used by switchboard STATUS_LIGHTS)
STATUS_COLORS: dict[str, str] = {
    "working": COLORS["status_working"],
    "idle": COLORS["status_idle"],
    "blocked": COLORS["status_blocked"],
    "waiting": COLORS["status_blocked"],
    "completed": COLORS["status_completed"],
    "error": COLORS["status_error"],
    "unknown": COLORS["status_unknown"],
}

# Map status to border colors for cards
STATUS_BORDER_COLORS: dict[str, str] = {
    "working": COLORS["status_working"],
    "idle": COLORS["border_inactive"],
    "blocked": COLORS["status_error"],
    "waiting": COLORS["status_blocked"],
    "completed": COLORS["status_completed"],
    "error": COLORS["status_error"],
    "unknown": COLORS["border_inactive"],
}

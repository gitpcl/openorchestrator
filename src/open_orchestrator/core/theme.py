"""Centralized theme system for Open Orchestrator UI."""

from __future__ import annotations

# Single source of truth for all colors across the application.
# Used by: switchboard.py (Textual), tmux_manager.py, picker.py (curses)
COLORS = {
    # Core palette
    "accent": "#00d7d7",
    "dark_primary": "#1e1e1e",
    "dark_secondary": "#262626",
    "dark_tertiary": "#3a3a3a",
    "border_inactive": "#444444",

    # Status
    "status_working": "#00d787",
    "status_idle": "#5f87af",
    "status_blocked": "#ffb347",
    "status_error": "#ff5555",
    "status_completed": "#00d7d7",
    "status_unknown": "#808080",

    # Text
    "text_primary": "#e8e8e8",
    "text_secondary": "#a8a8a8",
    "text_accent": "#00d7d7",

    # Surfaces & UI chrome
    "surface": "#2d2d2d",
    "header_bg": "#333333",
    "input_border": "#888888",
    "card_border": "#555555",

    # Toast variants
    "toast_info": "#5f87af",
    "toast_success": "#00d787",
    "toast_warning": "#ffb347",
    "toast_error": "#ff5555",
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

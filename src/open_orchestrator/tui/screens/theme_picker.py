"""Theme picker side panel.

Presents the 5 built-in themes as a selectable list with colored swatches.
Docks to the right side of the screen like Textual's HelpPanel.
Arrow keys navigate, Enter selects, ESC dismisses.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from open_orchestrator.config import THEMES


class ThemePickerPanel(Widget):
    """Side-panel theme picker. Docks right, matching the HelpPanel pattern."""

    DEFAULT_CSS = """
    ThemePickerPanel {
        split: right;
        width: 33%;
        min-width: 24;
        max-width: 40;
        border-left: vkey $foreground 30%;
        padding: 1 1;
        height: 1fr;
        layout: vertical;

        #theme-title {
            width: 100%;
            text-align: center;
            text-style: bold;
            color: $accent;
            margin-bottom: 1;
        }

        #theme-options {
            height: auto;
            max-height: 12;
        }

        #theme-footer {
            width: 100%;
            text-align: center;
            margin-top: 1;
            color: $text-muted;
        }
    }
    """

    BINDINGS = [
        ("escape", "dismiss_panel", "Close"),
    ]

    class ThemeSelected(Message):
        """Posted when a theme is selected."""

        def __init__(self, theme_name: str) -> None:
            super().__init__()
            self.theme_name = theme_name

    def compose(self) -> ComposeResult:
        yield Label("Theme", id="theme-title")
        option_list = OptionList(id="theme-options")
        for name, colors in THEMES.items():
            swatch = f"[{colors.accent}]\u2588\u2588[/] {name}"
            option_list.add_option(Option(swatch, id=name))
        yield option_list
        yield Label("Enter select \u00b7 ESC close", id="theme-footer")

    def on_mount(self) -> None:
        self.query_one("#theme-options", OptionList).focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        self.post_message(self.ThemeSelected(str(event.option_id)))

    def action_dismiss_panel(self) -> None:
        self.remove()


__all__ = ["ThemePickerPanel"]

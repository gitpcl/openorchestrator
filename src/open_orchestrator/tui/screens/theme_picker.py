"""Theme picker modal screen.

Presents the 5 built-in themes as a selectable list with colored swatches.
Arrow keys navigate, Enter selects, ESC cancels.
Styles defined via TCSS_TEMPLATE in app.py.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from open_orchestrator.config import THEMES


class ThemePickerScreen(ModalScreen[str | None]):
    """Modal theme picker. Returns theme name or None on cancel."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-dialog"):
            yield Label("Theme", id="theme-title")
            option_list = OptionList(id="theme-options")
            for name, colors in THEMES.items():
                swatch = f"[{colors.accent}]\u2588\u2588[/] {name}"
                option_list.add_option(Option(swatch, id=name))
            yield option_list
            yield Label("Enter select \u00b7 ESC cancel", id="theme-footer")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option_id))

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["ThemePickerScreen"]

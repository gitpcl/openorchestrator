"""Help overlay screen showing keybinding reference.

Styled after dmux's [?] help screen. Styles defined in styles.tcss.
"""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class HelpOverlayScreen(ModalScreen[None]):
    """Modal overlay showing keybinding help."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("question_mark", "close", "Close"),
        ("q", "close", "Close"),
    ]

    KEYBINDING_HELP = (
        "[b]n[/b]         New agent pane\n"
        "[b]x[/b]         Close selected pane\n"
        "[b]m[/b]         Merge selected branch\n"
        "[b]j[/b] / [b]↓[/b]     Navigate down\n"
        "[b]k[/b] / [b]↑[/b]     Navigate up\n"
        "[b]Enter[/b]     Focus selected pane\n"
        "[b]a[/b]         A/B comparison\n"
        "[b]?[/b]         This help\n"
        "[b]q[/b]         Quit"
    )

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Label("Keyboard Shortcuts", id="help-title")
            yield Static(self.KEYBINDING_HELP, id="help-content")
            yield Label("Press [b]?[/b] or [b]Esc[/b] to close", id="help-footer")

    def action_close(self) -> None:
        self.dismiss(None)


__all__ = ["HelpOverlayScreen"]

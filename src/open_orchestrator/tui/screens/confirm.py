"""Confirmation modal screen.

Generic yes/no confirmation dialog, styled after dmux's confirm-before pattern.
Styles defined in styles.tcss.
"""

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmScreen(ModalScreen[bool]):
    """Modal confirmation dialog.

    Returns True if confirmed, False if cancelled.
    """

    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, message: str, confirm_label: str = "Yes", cancel_label: str = "No") -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(self._message, id="confirm-message")
            with Center(id="confirm-buttons"):
                yield Button(self._confirm_label, variant="error", id="btn-confirm")
                yield Button(self._cancel_label, variant="default", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-confirm")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


__all__ = ["ConfirmScreen"]

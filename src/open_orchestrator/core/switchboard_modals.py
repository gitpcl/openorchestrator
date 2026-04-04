"""Switchboard modal screens for user interaction.

Extracted from switchboard.py to keep file sizes manageable.
Provides InputModal, ConfirmModal, DetailModal, and SearchableSelectModal.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static

from open_orchestrator.core.theme import COLORS


def _darken(hex_color: str, factor: float = 0.7) -> str:
    """Darken a hex color by a factor (0.0=black, 1.0=unchanged)."""
    hex_color = hex_color.lstrip("#")
    r = int(int(hex_color[0:2], 16) * factor)
    g = int(int(hex_color[2:4], 16) * factor)
    b = int(int(hex_color[4:6], 16) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _apply_modal_bg(modal: ModalScreen, dialog_id: str) -> None:  # type: ignore[type-arg]
    """Apply detected background to modal overlay and dialog."""
    bg = getattr(modal.app, "_bg_color", None)
    if not bg:
        return
    modal.styles.background = f"{bg} 60%"
    try:
        modal.query_one(f"#{dialog_id}").styles.background = _darken(bg, 0.85)
    except Exception:
        pass


class InputModal(ModalScreen[str | None]):
    """Modal screen for text input (send, new, broadcast)."""

    DEFAULT_CSS = f"""
    InputModal {{
        align: center middle;
    }}
    #input-dialog {{
        width: 55;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        border: none;
        background: {COLORS["surface"]};
    }}
    #input-dialog Input {{
        border: none;
        background: transparent;
        border-left: tall {COLORS["input_border"]};
        padding: 0 0 0 1;
        margin: 1 0;
        min-height: 2;
    }}
    #input-dialog Input:focus {{
        border: none;
        border-left: tall {COLORS["input_border"]};
    }}
    #input-dialog Label {{
        margin: 0;
    }}
    .modal-hint {{
        margin: 0;
        text-style: dim;
        color: {COLORS["text_secondary"]};
    }}
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        with Container(id="input-dialog"):
            yield Label(self._prompt)
            yield Input(id="modal-input")
            yield Static("[dim]Enter[/dim] submit | [dim]Esc[/dim] cancel", classes="modal-hint")

    def on_mount(self) -> None:
        _apply_modal_bg(self, "input-dialog")
        self.query_one("#modal-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if value else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Modal screen for y/N confirmation."""

    DEFAULT_CSS = f"""
    ConfirmModal {{
        align: center middle;
    }}
    #confirm-dialog {{
        width: auto;
        min-width: 40;
        max-width: 70;
        height: auto;
        padding: 1 2;
        border: none;
        background: {COLORS["surface"]};
    }}
    #confirm-dialog Label {{
        margin: 0;
    }}
    .modal-hint {{
        margin-top: 1;
        text-style: dim;
        color: {COLORS["text_secondary"]};
    }}
    """

    BINDINGS = [
        Binding("y", "yes", "Yes", show=False),
        Binding("n", "no", "No", show=False),
        Binding("escape", "no", "Cancel", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def on_mount(self) -> None:
        _apply_modal_bg(self, "confirm-dialog")

    def compose(self) -> ComposeResult:
        with Container(id="confirm-dialog"):
            yield Label(self._message)
            yield Static("[dim]Y[/dim] yes | [dim]N[/dim] no | [dim]Esc[/dim] cancel", classes="modal-hint")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class DetailModal(ModalScreen[None]):
    """Modal screen for detail panels (info, overlap)."""

    DEFAULT_CSS = f"""
    DetailModal {{
        align: center middle;
    }}
    #detail-panel {{
        width: 70;
        max-width: 90%;
        max-height: 80%;
        padding: 2 3;
        border: none;
        background: {COLORS["surface"]};
        overflow-y: auto;
    }}
    #detail-panel .modal-title {{
        text-style: bold;
        margin-bottom: 1;
    }}
    #detail-panel .modal-hint {{
        margin-top: 1;
        text-style: dim;
    }}
    """

    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(self, title: str, lines: list[str]) -> None:
        super().__init__()
        self._title = title
        self._lines = lines

    def on_mount(self) -> None:
        _apply_modal_bg(self, "detail-panel")

    def compose(self) -> ComposeResult:
        with Container(id="detail-panel"):
            yield Static(self._title, classes="modal-title")
            yield Static("\n".join(self._lines), classes="modal-body")
            yield Static("[dim]Esc[/dim] close", classes="modal-hint")

    def on_key(self, event: Key) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


@dataclass
class SelectOption:
    """An option in the searchable select modal."""

    value: str
    label: str
    description: str = ""
    category: str = ""


class SearchableSelectModal(ModalScreen[str | None]):
    """Modal with search input and categorized selectable list.

    Usage example::

        options = [
            SelectOption(value="wt-auth", label="auth-flow", description="COMPLETED", category="Ready"),
            SelectOption(value="wt-api", label="api-refactor", description="WORKING", category="In Progress"),
        ]
        def on_selected(value: str | None) -> None:
            if value:
                # value is the SelectOption.value of the chosen item
                ...
        self.push_screen(SearchableSelectModal("Pick a worktree", options), on_selected)
    """

    DEFAULT_CSS = f"""
    SearchableSelectModal {{
        align: center middle;
    }}
    #select-dialog {{
        width: 55;
        max-width: 90%;
        height: auto;
        max-height: 60%;
        padding: 1 2;
        border: none;
        background: {COLORS["surface"]};
    }}
    #select-title-row {{
        layout: horizontal;
        height: 1;
        margin-bottom: 1;
    }}
    #select-title {{
        width: 1fr;
        text-style: bold;
    }}
    #select-esc-hint {{
        width: auto;
        color: {COLORS["text_secondary"]};
    }}
    #select-search {{
        margin-bottom: 1;
        border: none;
        background: transparent;
        border-left: tall {COLORS["input_border"]};
        padding: 0 0 0 1;
    }}
    #select-search:focus {{
        border: none;
        border-left: tall {COLORS["input_border"]};
    }}
    #select-list {{
        height: auto;
        max-height: 20;
        overflow-y: auto;
    }}
    .select-category {{
        color: white;
        text-style: bold;
        margin-top: 1;
    }}
    .select-item {{
        padding: 0 1;
        height: 1;
    }}
    .select-item.highlighted {{
        background: {COLORS["surface_4dp"]};
        text-style: bold;
    }}
    .select-hint {{
        margin-top: 1;
        color: {COLORS["text_secondary"]};
        text-style: dim;
    }}
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "select_item", "Select", show=False),
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
    ]

    def __init__(
        self,
        title: str,
        options: list[SelectOption],
        *,
        search_placeholder: str = "Search...",
    ) -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._search_placeholder = search_placeholder
        self._filtered: list[SelectOption] = list(options)
        self._highlight_index = 0

    def compose(self) -> ComposeResult:
        with Container(id="select-dialog"):
            with Container(id="select-title-row"):
                yield Static(self._title, id="select-title")
                yield Static("esc", id="select-esc-hint")
            yield Input(placeholder=self._search_placeholder, id="select-search")
            yield Container(id="select-list")  # populated dynamically
            yield Static(
                "[dim][bold]1-9[/bold] quick pick | "
                "[bold]\u2191\u2193[/bold] navigate | "
                "[bold]Enter[/bold] select | "
                "[bold]Esc[/bold] cancel[/dim]",
                classes="select-hint",
            )

    def on_mount(self) -> None:
        _apply_modal_bg(self, "select-dialog")
        self._rebuild_list()
        self.query_one("#select-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "select-search":
            query = event.value.strip()
            # Number shortcut: if single digit, select that item directly
            if query.isdigit() and 1 <= int(query) <= len(self._options):
                idx = int(query) - 1
                self.dismiss(self._options[idx].value)
                return
            query_lower = query.lower()
            if query_lower:
                self._filtered = [
                    opt
                    for opt in self._options
                    if query_lower in opt.label.lower()
                    or query_lower in opt.category.lower()
                    or query_lower in opt.description.lower()
                ]
            else:
                self._filtered = list(self._options)
            self._highlight_index = 0
            self._rebuild_list()

    def _rebuild_list(self) -> None:
        """Rebuild the option list, grouping by category."""
        container = self.query_one("#select-list", Container)
        container.remove_children()

        # Group by category (preserving insertion order)
        categories: dict[str, list[SelectOption]] = {}
        for opt in self._filtered:
            cat = opt.category or ""
            categories.setdefault(cat, []).append(opt)

        idx = 0
        for cat_name, opts in categories.items():
            if cat_name:
                container.mount(Static(cat_name, classes="select-category"))
            for opt in opts:
                # Show 1-indexed number for quick selection
                num = idx + 1
                num_hint = f"[dim]{num}.[/dim] " if num <= 9 else "   "
                label = opt.label
                if opt.description:
                    label = f"{opt.label}  [dim]{opt.description}[/dim]"
                item = Static(f"  {num_hint}{label}", classes="select-item")
                item.data_index = idx  # type: ignore[attr-defined]
                if idx == self._highlight_index:
                    item.add_class("highlighted")
                container.mount(item)
                idx += 1

    def _update_highlight(self, new_index: int) -> None:
        """Move highlight by toggling CSS classes (avoids full remount)."""
        items = self.query(".select-item")
        if not items:
            return
        old = self._highlight_index
        self._highlight_index = new_index
        if 0 <= old < len(items):
            items[old].remove_class("highlighted")
        if 0 <= new_index < len(items):
            items[new_index].add_class("highlighted")

    def action_move_up(self) -> None:
        if self._filtered and self._highlight_index > 0:
            self._update_highlight(self._highlight_index - 1)

    def action_move_down(self) -> None:
        if self._filtered and self._highlight_index < len(self._filtered) - 1:
            self._update_highlight(self._highlight_index + 1)

    def action_select_item(self) -> None:
        if self._filtered and 0 <= self._highlight_index < len(self._filtered):
            self.dismiss(self._filtered[self._highlight_index].value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

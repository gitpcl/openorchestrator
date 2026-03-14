"""Mock switchboard for VHS demo — hardcoded cards, monochromatic palette."""

from __future__ import annotations

from rich.columns import Columns
from rich.panel import Panel
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Static

SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827"
CARD_WIDTH = 30

MOCK_CARDS = [
    {
        "name": "add-user-auth-jwt",
        "status": "working",
        "branch": "add-user-authentication-jwt",
        "tool": "claude",
        "task": "Implementing JWT auth",
        "elapsed": "12m",
        "diff": "+142 -37",
    },
    {
        "name": "write-api-docs",
        "status": "working",
        "branch": "write-api-documentation",
        "tool": "claude",
        "task": "Writing endpoint docs",
        "elapsed": "8m",
        "diff": "+89 -12",
    },
    {
        "name": "add-integ-tests",
        "status": "waiting",
        "branch": "add-integration-tests",
        "tool": "claude",
        "task": "Waiting for input",
        "elapsed": "3m",
        "diff": "+23 -5",
    },
    {
        "name": "fix-login-redirect",
        "status": "completed",
        "branch": "fix-login-redirect",
        "tool": "opencode",
        "task": "Done",
        "elapsed": "1h",
        "diff": "+17 -4",
    },
]

# Monochromatic palette — all white/dim tones
STATUS_LIGHTS = {
    "working": ("\u25cf", "white"),
    "idle": ("\u25cb", "dim"),
    "blocked": ("\u26a0", "white"),
    "waiting": ("\u26a0", "dim"),
    "completed": ("\u2713", "white"),
}


def _render_card(card: dict, tick: int) -> str:
    w = CARD_WIDTH - 4
    light_char, color = STATUS_LIGHTS.get(card["status"], ("?", "white"))
    if card["status"] == "working":
        light_char = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]

    name = card["name"][:w]
    status_label = card["status"].upper()
    elapsed = f"{card['elapsed']:>5}"
    branch = card["branch"][:w]
    tool = card["tool"]
    diff = card["diff"]
    task = card["task"][:w]

    status_line = f"[{color}]{light_char}[/{color}] {status_label}"
    pad = w - len(status_label) - 2 - len(elapsed)
    status_line += " " * max(0, pad) + elapsed

    tool_pad = w - len(tool) - len(diff)
    tool_line = tool + " " * max(1, tool_pad) + f"[dim]{diff}[/dim]"

    return "\n".join([
        f"[bold]{name}[/bold]",
        status_line,
        f"[dim]{branch}[/dim]",
        tool_line,
        f"[dim]{task}[/dim]",
    ])


class CardGrid(Static):
    DEFAULT_CSS = """
    CardGrid {
        width: 100%;
        height: 1fr;
        overflow-y: auto;
        padding: 1 1;
    }
    """

    def render(self) -> object:
        app: MockSwitchboard = self.app  # type: ignore[assignment]
        panels = []
        for i, card in enumerate(MOCK_CARDS):
            selected = i == app._selected
            content = _render_card(card, app._tick)
            border_style = "bold cyan" if selected else "dim"
            panels.append(Panel(content, width=CARD_WIDTH + 2, border_style=border_style))
        return Columns(panels, padding=(1, 1))


class MockSwitchboard(App[None]):
    CSS = """
    Screen {
        layout: vertical;
        background: #191724;
    }
    #header {
        dock: top; width: 1fr; height: 1;
        layout: horizontal;
        background: #e0def4; color: #191724;
        text-style: bold;
    }
    #header-title { width: auto; height: 1; }
    #header-stats { width: 1fr; height: 1; text-align: right; }
    #footer {
        dock: bottom; width: 1fr; height: 1;
        background: #e0def4; color: #191724;
    }
    """

    BINDINGS = [
        Binding("up", "navigate('up')", show=False),
        Binding("down", "navigate('down')", show=False),
        Binding("left", "navigate('left')", show=False),
        Binding("right", "navigate('right')", show=False),
        Binding("q", "quit", show=False),
    ]

    _footer_text = (
        " \\[arrows] nav  \\[Enter] patch  \\[s] send  \\[a] all  "
        "\\[n] new  \\[S] ship  \\[f] files  \\[i] info  \\[q] quit"
    )

    def __init__(self) -> None:
        super().__init__()
        self._selected = 0
        self._tick = 0

    def compose(self) -> ComposeResult:
        with Container(id="header"):
            yield Static(" SWITCHBOARD", id="header-title")
            yield Static(
                "4 lines  \u25cf2 active  \u26a01 waiting  \u25cb1 done ",
                id="header-stats",
            )
        yield CardGrid(id="card-grid")
        yield Static(self._footer_text, id="footer")

    def on_mount(self) -> None:
        self.set_interval(0.2, self._on_tick)

    def _on_tick(self) -> None:
        self._tick += 1
        self.query_one("#card-grid", CardGrid).refresh()

    def action_navigate(self, direction: str) -> None:
        n = len(MOCK_CARDS)
        if direction == "right":
            self._selected = min(self._selected + 1, n - 1)
        elif direction == "left":
            self._selected = max(self._selected - 1, 0)
        elif direction == "down":
            self._selected = min(self._selected + 4, n - 1)
        elif direction == "up":
            self._selected = max(self._selected - 4, 0)
        self.query_one("#card-grid", CardGrid).refresh()


if __name__ == "__main__":
    MockSwitchboard().run()

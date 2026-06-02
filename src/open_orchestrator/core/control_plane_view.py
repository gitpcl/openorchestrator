"""Textual screen rendering the control-plane.

Layout:

    ┌─────────────────────────────────────────────────────────┐
    │ HEADER (orchestration progress or project + counts)     │
    ├─────────────────────────────────────────────────────────┤
    │ NEEDS YOU       (hidden when empty)                     │
    │   row …                                                 │
    │ READY TO SHIP                                           │
    │   row …                                                 │
    │ IN FLIGHT                                               │
    │   row …                                                 │
    │ BACKGROUND      (≤10 events)                            │
    │   row …                                                 │
    ├─────────────────────────────────────────────────────────┤
    │ FOOTER (hotkey strip + toast slot)                      │
    └─────────────────────────────────────────────────────────┘

The view is intentionally dumb: it asks
``open_orchestrator.core.control_plane_sections`` for rows and asks
``open_orchestrator.core.control_plane_actions.ControlPlaneActions`` to
dispatch keys. No business logic lives here.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical, VerticalScroll
from textual.widgets import Static

from open_orchestrator.core.control_plane_actions import (
    ActionResult,
    ControlPlaneActions,
    ControlPlaneRuntime,
    build_start_args,
    start_work,
)
from open_orchestrator.core.control_plane_sections import (
    build_all_sections,
    compute_orchestration_header,
)
from open_orchestrator.core.status import StatusTracker, runtime_status_config
from open_orchestrator.core.switchboard_modals import (
    ConfirmModal,
    InputModal,
    SearchableSelectModal,
    SelectOption,
)
from open_orchestrator.models.control_plane import (
    ControlPlaneRow,
    RowAction,
    SectionKind,
)
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

if TYPE_CHECKING:
    from open_orchestrator.core.critic import CriticVerdict

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 2.0

SECTION_TITLES: dict[SectionKind, str] = {
    SectionKind.NEEDS_YOU: "NEEDS YOU",
    SectionKind.READY_TO_SHIP: "READY TO SHIP",
    SectionKind.IN_FLIGHT: "IN FLIGHT",
    SectionKind.BACKGROUND: "BACKGROUND",
}

SECTION_COLORS: dict[SectionKind, str] = {
    SectionKind.NEEDS_YOU: "bold red",
    SectionKind.READY_TO_SHIP: "bold green",
    SectionKind.IN_FLIGHT: "bold cyan",
    SectionKind.BACKGROUND: "bold dim",
}

# Order is the priority order — top-to-bottom.
SECTION_ORDER: tuple[SectionKind, ...] = (
    SectionKind.NEEDS_YOU,
    SectionKind.READY_TO_SHIP,
    SectionKind.IN_FLIGHT,
    SectionKind.BACKGROUND,
)


@dataclass
class _Focus:
    """Index pair (section_index, row_index) for keyboard focus."""

    section: int = 0
    row: int = 0


class SectionWidget(Static):
    """A single section header + list of rows."""

    DEFAULT_CSS = """
    SectionWidget {
        layout: vertical;
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    SectionWidget.empty {
        display: none;
    }
    """

    def __init__(self, kind: SectionKind, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.kind = kind
        self._rows: list[ControlPlaneRow] = []
        self._focused_row: int = -1

    def update_rows(self, rows: list[ControlPlaneRow], *, focused_row: int = -1) -> None:
        self._rows = rows
        self._focused_row = focused_row
        if not rows:
            self.add_class("empty")
        else:
            self.remove_class("empty")
        self.refresh()

    def render(self) -> Text:
        color = SECTION_COLORS[self.kind]
        title = SECTION_TITLES[self.kind]
        text = Text()
        text.append(f"▸ {title}", style=color)
        text.append(f"  ({len(self._rows)})\n", style="dim")
        for i, row in enumerate(self._rows):
            marker = "▶ " if i == self._focused_row else "  "
            line_style = "reverse" if i == self._focused_row else ""
            verbs = " ".join(f"[{a.value}]" for a in row.actions)
            line = f"{marker}{row.name:<24} {row.summary}"
            if verbs:
                line = f"{line}   {verbs}"
            text.append(line + "\n", style=line_style)
        return text

    @property
    def rows(self) -> list[ControlPlaneRow]:
        return self._rows


class ControlPlaneApp(App[None]):
    """Textual app that renders the control plane.

    Standalone application (separate from ``SwitchboardApp``) so the
    legacy card grid stays available behind ``--legacy-cards``.
    """

    CSS = """
    Screen { layout: vertical; background: $background; }
    #header { dock: top; height: 1; background: $panel; color: $text; text-style: bold; padding: 0 1; }
    #body { width: 1fr; height: 1fr; overflow-y: auto; }
    #footer { dock: bottom; height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    #toast { dock: bottom; height: 1; background: $primary; color: $text; padding: 0 1; display: none; }
    #toast.visible { display: block; }
    #review {
        dock: bottom;
        height: auto;
        max-height: 12;
        background: $panel;
        color: $text;
        padding: 0 1;
        display: none;
        border: tall $primary;
    }
    #review.visible { display: block; }
    """

    BINDINGS = [
        Binding("up", "focus_prev", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("j", "focus_next", "Next", show=False),
        Binding("k", "focus_prev", "Prev", show=False),
        Binding("n", "new", "New", show=False),
        Binding("s", "dispatch('s')", "Ship", show=False),
        Binding("r", "dispatch('r')", "Review", show=False),
        Binding("a", "dispatch('a')", "Attach", show=False),
        Binding("f", "dispatch('f')", "Fix", show=False),
        Binding("m", "dispatch('m')", "Merge", show=False),
        Binding("x", "dispatch('x')", "Dismiss", show=False),
        Binding("q", "quit", "Quit", show=False),
        Binding("escape", "close_review", "Close panel", show=False),
    ]

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        refresh_seconds: float = REFRESH_SECONDS,
    ) -> None:
        super().__init__()
        self._repo_root = (repo_root or Path.cwd()).resolve()
        self._refresh_seconds = refresh_seconds
        self._tracker = StatusTracker(runtime_status_config(self._repo_root))
        self._sections: dict[SectionKind, list[ControlPlaneRow]] = {k: [] for k in SECTION_ORDER}
        self._section_widgets: dict[SectionKind, SectionWidget] = {}
        self._focus = _Focus()
        self._actions: ControlPlaneActions = ControlPlaneActions(
            ControlPlaneRuntime(
                repo_root=str(self._repo_root),
                critic_lookup=self._lookup_critic,
            )
        )
        self._critic_cache: dict[str, object] = {}
        # Transient state for the multi-step "start work" (n) flow.
        self._new_task: str = ""
        self._new_mode: str = ""

    # ── lifecycle ─────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(" CONTROL PLANE", id="header")
        with VerticalScroll(id="body"):
            with Vertical():
                for kind in SECTION_ORDER:
                    widget = SectionWidget(kind, id=f"section-{kind.value}")
                    self._section_widgets[kind] = widget
                    yield widget
        with Container():
            yield Static("", id="toast")
            yield Static("", id="review")
            yield Static(self._build_footer(), id="footer")

    def on_mount(self) -> None:
        self.set_interval(self._refresh_seconds, self._tick)
        self.call_after_refresh(self._tick)

    def on_unmount(self) -> None:
        try:
            self._tracker.close()
        except Exception:  # noqa: BLE001
            logger.debug("Failed to close tracker", exc_info=True)

    # ── data refresh ─────────────────────────────────────────────────

    async def _tick(self) -> None:
        try:
            await asyncio.to_thread(self._rebuild_sections)
        except Exception:  # noqa: BLE001
            logger.debug("rebuild_sections failed", exc_info=True)
        self._update_header()
        self._render_sections()

    def _rebuild_sections(self) -> None:
        statuses = self._tracker.get_all_statuses()
        merge_queue = self._safe_merge_queue()
        conflicts = self._detect_conflicts(statuses)
        critic_verdicts = self._refresh_critic_verdicts(statuses)

        from open_orchestrator.core.critic import CriticAgent
        from open_orchestrator.core.dream import DreamDaemon
        from open_orchestrator.core.memory import MemoryManager

        try:
            dream = DreamDaemon(self._repo_root)
        except Exception:  # noqa: BLE001
            dream = None
        try:
            memory = MemoryManager(self._repo_root)
        except Exception:  # noqa: BLE001
            memory = None
        try:
            critic = CriticAgent(self._repo_root)
        except Exception:  # noqa: BLE001
            critic = None

        self._sections = build_all_sections(
            statuses=statuses,
            merge_queue=merge_queue,
            critic_verdicts=critic_verdicts,
            conflict_worktrees=conflicts,
            dream=dream,
            memory=memory,
            critic=critic,
        )

    def _safe_merge_queue(self) -> list[tuple[str, int, int]]:
        try:
            from open_orchestrator.core.merge import MergeManager

            mgr = MergeManager(self._repo_root)
            return mgr.plan_merge_order()
        except Exception:  # noqa: BLE001
            logger.debug("plan_merge_order failed", exc_info=True)
            return []

    def _detect_conflicts(self, statuses: list[WorktreeAIStatus]) -> list[str]:
        """Scan worktrees for in-progress merges (MERGE_HEAD / rebase-merge)."""
        conflicts: list[str] = []
        for s in statuses:
            if not s.worktree_path:
                continue
            wt_path = Path(s.worktree_path)
            if (wt_path / ".git" / "MERGE_HEAD").exists() or (wt_path / ".git" / "rebase-merge").exists():
                conflicts.append(s.worktree_name)
        return [c for c in conflicts if c]

    def _refresh_critic_verdicts(self, statuses: list[WorktreeAIStatus]) -> dict[str, CriticVerdict]:
        """Run lightweight critic.review_ship() for COMPLETED worktrees."""
        from open_orchestrator.core.critic import CriticAgent

        critic = CriticAgent(self._repo_root)
        cache: dict[str, CriticVerdict] = {}
        for s in statuses:
            if s.activity_status != AIActivityStatus.COMPLETED:
                continue
            if not s.worktree_name:
                continue
            try:
                cache[s.worktree_name] = critic.review_ship(s.worktree_name)
            except Exception:  # noqa: BLE001
                logger.debug("critic.review_ship(%s) failed", s.worktree_name, exc_info=True)
        self._critic_cache = cast("dict[str, object]", cache)
        return cache

    def _lookup_critic(self, worktree: str) -> CriticVerdict | None:
        return cast("CriticVerdict | None", self._critic_cache.get(worktree))

    # ── render ────────────────────────────────────────────────────────

    def _update_header(self) -> None:
        try:
            header_widget = self.query_one("#header", Static)
        except Exception:  # noqa: BLE001
            return

        orch_state = self._load_orchestrator_state()
        header_payload = compute_orchestration_header(orch_state)
        if header_payload is not None:
            header_widget.update(header_payload.line)
            return

        total_rows = sum(len(rows) for rows in self._sections.values())
        project = self._repo_root.name
        header_widget.update(f" {project} · {total_rows} rows · {datetime.now().strftime('%H:%M:%S')}")

    def _load_orchestrator_state(self) -> object | None:
        state_path = self._repo_root / ".owt" / "orchestrator.json"
        if not state_path.exists():
            return None
        try:
            from open_orchestrator.core.orchestrator import OrchestratorState

            return OrchestratorState.model_validate_json(state_path.read_text())
        except Exception:  # noqa: BLE001
            return None

    def _render_sections(self) -> None:
        all_rows = list(self._iter_rows())
        if not all_rows:
            self._focus = _Focus()
        else:
            current = self._current_row()
            if current is None:
                self._focus = _Focus()

        # Re-resolve which (section_index, row_index) corresponds to focus
        focused_idx = self._focused_global_index(all_rows)

        global_cursor = 0
        for _sec_idx, kind in enumerate(SECTION_ORDER):
            rows = self._sections.get(kind, [])
            widget = self._section_widgets.get(kind)
            if widget is None:
                continue
            inner_focus = -1
            if rows and global_cursor <= focused_idx < global_cursor + len(rows):
                inner_focus = focused_idx - global_cursor
            widget.update_rows(rows, focused_row=inner_focus)
            global_cursor += len(rows)
        self._update_footer()

    def _build_footer(self) -> str:
        """Footer hotkeys, tailored to the currently-focused row's actions.

        Always shows nav / new / quit; between them it lists only the verbs
        that actually apply to the focused row, so the UI teaches itself.
        """
        parts = ["[bold]↑↓[/bold] [dim]nav[/dim]", "[bold]n[/bold] [dim]new[/dim]"]
        row = self._current_row()
        if row is not None:
            for action in row.actions:
                parts.append(f"[bold]{action.value}[/bold] [dim]{action.label}[/dim]")
        parts.append("[bold]q[/bold] [dim]quit[/dim]")
        return "  |  ".join(parts)

    def _update_footer(self) -> None:
        try:
            self.query_one("#footer", Static).update(self._build_footer())
        except Exception:  # noqa: BLE001
            logger.debug("footer update skipped", exc_info=True)

    def _iter_rows(self) -> list[ControlPlaneRow]:
        result: list[ControlPlaneRow] = []
        for kind in SECTION_ORDER:
            result.extend(self._sections.get(kind, []))
        return result

    def _current_row(self) -> ControlPlaneRow | None:
        rows = self._sections.get(SECTION_ORDER[self._focus.section], [])
        if 0 <= self._focus.row < len(rows):
            return rows[self._focus.row]
        return None

    def _focused_global_index(self, all_rows: list[ControlPlaneRow]) -> int:
        cursor = 0
        for sec_idx, kind in enumerate(SECTION_ORDER):
            rows = self._sections.get(kind, [])
            if sec_idx == self._focus.section:
                return cursor + min(self._focus.row, max(0, len(rows) - 1))
            cursor += len(rows)
        return 0

    # ── navigation ────────────────────────────────────────────────────

    def action_focus_next(self) -> None:
        all_rows = self._iter_rows()
        if not all_rows:
            return
        idx = self._focused_global_index(all_rows)
        idx = min(idx + 1, len(all_rows) - 1)
        self._set_focus_from_global(idx)
        self._render_sections()

    def action_focus_prev(self) -> None:
        all_rows = self._iter_rows()
        if not all_rows:
            return
        idx = self._focused_global_index(all_rows)
        idx = max(idx - 1, 0)
        self._set_focus_from_global(idx)
        self._render_sections()

    def _set_focus_from_global(self, idx: int) -> None:
        cursor = 0
        for sec_idx, kind in enumerate(SECTION_ORDER):
            rows = self._sections.get(kind, [])
            if cursor <= idx < cursor + len(rows):
                self._focus = _Focus(section=sec_idx, row=idx - cursor)
                return
            cursor += len(rows)
        self._focus = _Focus()

    # ── dispatch ──────────────────────────────────────────────────────

    async def action_dispatch(self, key: str) -> None:
        row = self._current_row()
        if row is None:
            self._show_toast(f"No row to act on for '{key}'", variant="warn")
            return
        try:
            action = RowAction(key)
        except ValueError:
            self._show_toast(f"Unknown action: {key}", variant="warn")
            return
        if action not in row.actions:
            self._show_toast(f"'{key}' not available on '{row.name}'", variant="warn")
            return

        result: ActionResult = await self._actions.dispatch(row, action)
        self._handle_result(action, row, result)

    def _handle_result(self, action: RowAction, row: ControlPlaneRow, result: ActionResult) -> None:
        if action == RowAction.REVIEW and result.detail:
            self._show_review(result.detail)
        if result.handoff:
            self.exit()
            return
        variant = "info" if result.ok else "warn"
        self._show_toast(result.message, variant=variant)
        if result.ok and action in (RowAction.SHIP, RowAction.MERGE, RowAction.DISMISS):
            self.call_after_refresh(self._tick)

    # ── start work (n) ────────────────────────────────────────────────

    def action_new(self) -> None:
        """Start new work: collect a task, let the user pick the mode, confirm."""
        self.push_screen(InputModal("What do you want to work on?"), self._on_new_task)

    def _on_new_task(self, task: str | None) -> None:
        if not task:
            return
        self._new_task = task
        options = [
            SelectOption(
                value="single",
                label="One worktree + agent",
                description="owt new — a single task",
            ),
            SelectOption(
                value="plan",
                label="Multi-step plan",
                description="owt plan --start — decompose into a DAG and run it",
            ),
        ]
        self.push_screen(SearchableSelectModal("How should I run this?", options), self._on_new_mode)

    def _on_new_mode(self, mode: str | None) -> None:
        if not mode:
            return
        self._new_mode = mode
        args = build_start_args(self._new_task, mode)
        if args is None:
            self._show_toast(f"Cannot start mode '{mode}'", variant="warn")
            return
        preview = "owt " + " ".join(shlex.quote(a) for a in args)
        self.push_screen(
            ConfirmModal(f"Task: {self._new_task}\nRun:  {preview}\n\nStart?"),
            self._on_new_confirm,
        )

    def _on_new_confirm(self, yes: bool | None) -> None:
        if not yes:
            return
        self._show_toast("Starting…", variant="info")
        self.run_worker(self._do_start_work())

    async def _do_start_work(self) -> None:
        result = await start_work(self._new_task, self._new_mode, str(self._repo_root))
        self._show_toast(result.message, variant="info" if result.ok else "warn")
        if result.ok:
            self.call_after_refresh(self._tick)

    def action_close_review(self) -> None:
        try:
            review = self.query_one("#review", Static)
        except Exception:  # noqa: BLE001
            return
        review.update("")
        review.remove_class("visible")

    def _show_review(self, text: str) -> None:
        try:
            review = self.query_one("#review", Static)
        except Exception:  # noqa: BLE001
            return
        review.update(text)
        review.add_class("visible")

    def _show_toast(self, message: str, *, variant: str = "info") -> None:
        try:
            toast = self.query_one("#toast", Static)
        except Exception:  # noqa: BLE001
            return
        toast.update(Text(f" {message}"))
        toast.add_class("visible")
        self.set_timer(3.0 if variant == "info" else 5.0, lambda: toast.remove_class("visible"))

    # ── mouse / direct events (optional, very small) ──────────────────

    def on_key(self, event: events.Key) -> None:
        # Default bindings handle this; left as a placeholder for future
        # row-specific keys (e.g. number keys to jump to a row).
        del event

"""Control plane model layer.

Replaces the card-grid metaphor with a uniform row contract and a small
set of section kinds. Each row carries the actions that apply to it; the
view layer renders, and the action dispatcher resolves them.

This is the data layer Sprint 024 introduces — pure, immutable, easy to
unit-test without a Textual ``Pilot``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SectionKind(str, Enum):
    """One of the four prioritized sections in the control plane.

    Order is significant — sections render top-to-bottom in this order
    and that order *is* the priority signal to the user.
    """

    NEEDS_YOU = "needs_you"
    READY_TO_SHIP = "ready_to_ship"
    IN_FLIGHT = "in_flight"
    BACKGROUND = "background"


class RowAction(str, Enum):
    """Verb actions a row may expose.

    The single-letter key is the keyboard shortcut surfaced in the footer
    hotkey strip. The dispatcher resolves ``(SectionKind, key)`` to a
    coroutine — see :mod:`open_orchestrator.core.control_plane_actions`.
    """

    SHIP = "s"
    REVIEW = "r"
    ATTACH = "a"
    FIX = "f"
    MERGE = "m"
    DISMISS = "x"

    @property
    def label(self) -> str:
        return _ACTION_LABELS[self]


_ACTION_LABELS: dict[RowAction, str] = {
    RowAction.SHIP: "ship",
    RowAction.REVIEW: "review",
    RowAction.ATTACH: "attach",
    RowAction.FIX: "fix",
    RowAction.MERGE: "merge",
    RowAction.DISMISS: "dismiss",
}


class ControlPlaneRow(BaseModel):
    """A uniform row contract used by every section.

    Sections differ only in *which rows* they emit and *which actions*
    those rows expose. There is no per-section row class.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="Stable row identifier, used for focus tracking")
    section: SectionKind = Field(description="Section this row belongs to")
    name: str = Field(description="Primary label (worktree name, summary)")
    summary: str = Field(default="", description="One-line summary shown next to the name")
    detail: str = Field(default="", description="Optional expanded detail line")
    actions: tuple[RowAction, ...] = Field(default=(), description="Actions that apply to this row")
    meta: dict[str, str] = Field(default_factory=dict, description="Free-form metadata (worktree, branch, agent, …)")


class BackgroundEvent(BaseModel):
    """A single entry surfaced in the BACKGROUND section.

    Sources include the dream daemon, memory consolidation, and critic
    auto-passes — invisible work that should be acknowledged but not
    blocked on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime = Field(description="When the event happened")
    source: str = Field(description="dream | memory | critic (or extension)")
    summary: str = Field(description="One-line summary suitable for inline render")
    worktree_name: str | None = Field(default=None, description="Worktree association if scoped")

    @classmethod
    def stable_id(cls, source: str, timestamp: datetime, worktree_name: str | None = None) -> str:
        return f"bg:{source}:{int(timestamp.timestamp())}:{worktree_name or ''}"

    def to_row(self) -> ControlPlaneRow:
        """Render this event as a control-plane row in the BACKGROUND section."""
        when = self.timestamp.strftime("%H:%M")
        scope = f" · {self.worktree_name}" if self.worktree_name else ""
        return ControlPlaneRow(
            id=f"bg:{self.source}:{int(self.timestamp.timestamp())}:{self.worktree_name or ''}",
            section=SectionKind.BACKGROUND,
            name=f"{when} {self.source}{scope}",
            summary=self.summary,
            actions=(RowAction.DISMISS,),
            meta={"source": self.source},
        )


class OrchestrationHeader(BaseModel):
    """Header bar payload shown when an ``orchestrate`` run is active."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str
    feature_branch: str
    total: int
    completed: int
    running: int
    failed: int = 0

    @property
    def line(self) -> str:
        glyphs = "".join(["█"] * self.completed + ["·"] * max(0, self.total - self.completed))
        return (
            f"⟳ {self.goal} → {self.feature_branch}  "
            f"{self.completed}/{self.total} done · {self.running} running"
            f"{f' · {self.failed} failed' if self.failed else ''}  "
            f"[{glyphs}]"
        )

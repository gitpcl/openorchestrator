"""Control plane model layer.

Replaces the card-grid metaphor with a uniform row contract and a small
set of section kinds. Each row carries the actions that apply to it; the
view layer renders, and the action dispatcher resolves them.

This is the data layer Sprint 024 introduces — pure, immutable, easy to
unit-test without a Textual ``Pilot``.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SectionKind(str, Enum):
    """One of the three prioritized sections in the control plane.

    Order is significant — sections render top-to-bottom in this order
    and that order *is* the priority signal to the user.
    """

    NEEDS_YOU = "needs_you"
    READY_TO_SHIP = "ready_to_ship"
    IN_FLIGHT = "in_flight"


class RowAction(str, Enum):
    """Verb actions a row may expose.

    The single-letter key is the keyboard shortcut surfaced in the footer
    hotkey strip. The dispatcher resolves ``(SectionKind, key)`` to a
    coroutine — see :mod:`open_orchestrator.core.control_plane_actions`.
    """

    SHIP = "s"
    ATTACH = "a"
    FIX = "f"
    MERGE = "m"

    @property
    def label(self) -> str:
        return _ACTION_LABELS[self]


_ACTION_LABELS: dict[RowAction, str] = {
    RowAction.SHIP: "ship",
    RowAction.ATTACH: "attach",
    RowAction.FIX: "fix",
    RowAction.MERGE: "merge",
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

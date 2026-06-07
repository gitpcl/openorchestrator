"""Pure functions that build the three control-plane sections from runtime state.

Each builder takes already-fetched data and returns a list of
``ControlPlaneRow``. There is no widget code here — section builders are
fully unit-testable without a Textual ``Pilot``.

The order of returned rows *is* the on-screen order. Empty sections are
hidden by the view layer.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime

from open_orchestrator.models.control_plane import (
    ControlPlaneRow,
    RowAction,
    SectionKind,
)
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NEEDS YOU
# ---------------------------------------------------------------------------


def needs_you_section(
    statuses: Iterable[WorktreeAIStatus],
    *,
    conflict_worktrees: Iterable[str] = (),
) -> list[ControlPlaneRow]:
    """Build the NEEDS YOU section.

    Includes:
      - Worktrees with merge conflicts (passed in by caller)
      - Worktrees the status tracker reports as BLOCKED or ERROR
    """
    rows: list[ControlPlaneRow] = []
    conflicts = set(conflict_worktrees)

    # 1) Conflicts get top priority
    for name in sorted(conflicts):
        rows.append(
            ControlPlaneRow(
                id=f"needs:conflict:{name}",
                section=SectionKind.NEEDS_YOU,
                name=name,
                summary="merge conflict — needs manual resolution",
                actions=(RowAction.FIX, RowAction.ATTACH),
                meta={"worktree": name, "reason": "conflict"},
            )
        )

    # 2) Status-tracker BLOCKED/ERROR
    for s in statuses:
        if s.worktree_name in conflicts:
            continue
        if s.activity_status not in (AIActivityStatus.BLOCKED, AIActivityStatus.ERROR):
            continue
        summary = s.current_task or s.activity_status.value
        rows.append(
            ControlPlaneRow(
                id=f"needs:status:{s.worktree_name}",
                section=SectionKind.NEEDS_YOU,
                name=s.worktree_name,
                summary=f"{s.activity_status.value} — {summary}",
                actions=(RowAction.ATTACH,),
                meta={"worktree": s.worktree_name, "reason": s.activity_status.value, "branch": s.branch},
            )
        )

    return rows


# ---------------------------------------------------------------------------
# READY TO SHIP
# ---------------------------------------------------------------------------


def ready_to_ship_section(
    merge_queue: Iterable[tuple[str, int, int]],
) -> list[ControlPlaneRow]:
    """Build the READY TO SHIP section.

    Args:
        merge_queue: Output of ``MergeManager.plan_merge_order()`` —
            tuples of ``(worktree_name, commits_ahead, overlap_count)`` in
            optimal merge order.
    """
    rows: list[ControlPlaneRow] = []
    queue = list(merge_queue)
    for position, (name, ahead, overlaps) in enumerate(queue, start=1):
        downstream_hint = f"queued #{position}/{len(queue)}" if len(queue) > 1 else "ready"
        overlap_hint = f" · {overlaps} overlap" if overlaps else ""
        rows.append(
            ControlPlaneRow(
                id=f"ship:{name}",
                section=SectionKind.READY_TO_SHIP,
                name=name,
                summary=f"+{ahead} commits · {downstream_hint}{overlap_hint}",
                actions=(RowAction.SHIP, RowAction.ATTACH),
                meta={
                    "worktree": name,
                    "commits_ahead": str(ahead),
                    "overlaps": str(overlaps),
                    "position": str(position),
                },
            )
        )
    return rows


# ---------------------------------------------------------------------------
# IN FLIGHT
# ---------------------------------------------------------------------------


def in_flight_section(
    statuses: Iterable[WorktreeAIStatus],
    *,
    now: datetime | None = None,
) -> list[ControlPlaneRow]:
    """Build the IN FLIGHT section.

    Worktrees the status tracker reports as WORKING, sorted by most-recent
    activity first (so the longest-running agent appears last and is more
    likely to be the one needing attention).
    """
    now = now or datetime.now()
    candidates = [s for s in statuses if s.activity_status == AIActivityStatus.WORKING]
    candidates.sort(key=lambda s: s.updated_at, reverse=True)
    rows: list[ControlPlaneRow] = []
    for s in candidates:
        elapsed = _format_elapsed(now, s.updated_at)
        task = s.current_task or "(no task message)"
        rows.append(
            ControlPlaneRow(
                id=f"inflight:{s.worktree_name}",
                section=SectionKind.IN_FLIGHT,
                name=s.worktree_name,
                summary=f"{elapsed} · {s.ai_tool} · {task}",
                actions=(RowAction.ATTACH,),
                meta={
                    "worktree": s.worktree_name,
                    "branch": s.branch,
                    "tool": s.ai_tool,
                    "tmux_session": s.tmux_session or "",
                },
            )
        )
    return rows


# ---------------------------------------------------------------------------
# High-level orchestration helper
# ---------------------------------------------------------------------------


def build_all_sections(
    *,
    statuses: Iterable[WorktreeAIStatus],
    merge_queue: Iterable[tuple[str, int, int]] = (),
    conflict_worktrees: Iterable[str] = (),
) -> dict[SectionKind, list[ControlPlaneRow]]:
    """Build all three sections from the supplied runtime state.

    Side-effect-free — callers pass already-fetched runtime state.
    """
    statuses_list = list(statuses)

    needs = needs_you_section(
        statuses_list,
        conflict_worktrees=conflict_worktrees,
    )
    ship = ready_to_ship_section(merge_queue)
    flight = in_flight_section(statuses_list)

    return {
        SectionKind.NEEDS_YOU: needs,
        SectionKind.READY_TO_SHIP: ship,
        SectionKind.IN_FLIGHT: flight,
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _format_elapsed(now: datetime, then: datetime | None) -> str:
    if then is None:
        return "?"
    delta = max(0, int((now - then).total_seconds()))
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    return f"{delta // 86400}d"

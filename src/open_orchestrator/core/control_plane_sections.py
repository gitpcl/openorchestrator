"""Pure functions that build the four control-plane sections from runtime state.

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
from typing import TYPE_CHECKING

from open_orchestrator.models.control_plane import (
    BackgroundEvent,
    ControlPlaneRow,
    RowAction,
    SectionKind,
)
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

if TYPE_CHECKING:
    from open_orchestrator.core.critic import CriticAgent, CriticVerdict
    from open_orchestrator.core.dream import DreamDaemon
    from open_orchestrator.core.memory import MemoryManager
    from open_orchestrator.models.control_plane import OrchestrationHeader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NEEDS YOU
# ---------------------------------------------------------------------------


def needs_you_section(
    statuses: Iterable[WorktreeAIStatus],
    *,
    critic_verdicts: dict[str, CriticVerdict] | None = None,
    conflict_worktrees: Iterable[str] = (),
) -> list[ControlPlaneRow]:
    """Build the NEEDS YOU section.

    Includes:
      - Worktrees flagged by the critic with blocking findings
      - Worktrees with merge conflicts (passed in by caller)
      - Worktrees the status tracker reports as BLOCKED or ERROR
    """
    rows: list[ControlPlaneRow] = []
    critic_verdicts = critic_verdicts or {}
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

    # 2) Critic blockers
    for name, verdict in sorted(critic_verdicts.items()):
        if verdict.is_safe or name in conflicts:
            continue
        rows.append(
            ControlPlaneRow(
                id=f"needs:critic:{name}",
                section=SectionKind.NEEDS_YOU,
                name=name,
                summary=verdict.summary,
                actions=(RowAction.REVIEW, RowAction.ATTACH),
                meta={"worktree": name, "reason": "critic", "action": verdict.action},
            )
        )

    # 3) Status-tracker BLOCKED/ERROR
    for s in statuses:
        if s.worktree_name in conflicts:
            continue
        if s.worktree_name in critic_verdicts and not critic_verdicts[s.worktree_name].is_safe:
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
                actions=(RowAction.ATTACH, RowAction.REVIEW),
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
                actions=(RowAction.SHIP, RowAction.REVIEW, RowAction.ATTACH),
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
                actions=(RowAction.ATTACH, RowAction.REVIEW),
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
# BACKGROUND
# ---------------------------------------------------------------------------


def background_section(
    *,
    dream_events: Iterable[BackgroundEvent] = (),
    memory_events: Iterable[BackgroundEvent] = (),
    critic_events: Iterable[BackgroundEvent] = (),
    cap: int = 10,
) -> list[BackgroundEvent]:
    """Merge background event streams, newest first, capped.

    Returns ``BackgroundEvent`` (not ``ControlPlaneRow``) so callers can
    introspect timestamps; ``BackgroundEvent.to_row()`` converts.
    """
    merged: list[BackgroundEvent] = []
    merged.extend(dream_events)
    merged.extend(memory_events)
    merged.extend(critic_events)
    merged.sort(key=lambda e: e.timestamp, reverse=True)
    return merged[:cap]


def background_rows(events: Iterable[BackgroundEvent]) -> list[ControlPlaneRow]:
    """Convenience: pre-converted rows for view layers that want them."""
    return [event.to_row() for event in events]


# ---------------------------------------------------------------------------
# High-level orchestration helper
# ---------------------------------------------------------------------------


def build_all_sections(
    *,
    statuses: Iterable[WorktreeAIStatus],
    merge_queue: Iterable[tuple[str, int, int]] = (),
    critic_verdicts: dict[str, CriticVerdict] | None = None,
    conflict_worktrees: Iterable[str] = (),
    dream: DreamDaemon | None = None,
    memory: MemoryManager | None = None,
    critic: CriticAgent | None = None,
    background_cap: int = 10,
) -> dict[SectionKind, list[ControlPlaneRow]]:
    """Build all four sections from the supplied runtime state.

    Side-effect-free *except* for the optional event-source reads
    (dream/memory/critic), which touch the disk.
    """
    statuses_list = list(statuses)

    needs = needs_you_section(
        statuses_list,
        critic_verdicts=critic_verdicts,
        conflict_worktrees=conflict_worktrees,
    )
    ship = ready_to_ship_section(merge_queue)
    flight = in_flight_section(statuses_list)

    dream_events = _safe_recent_events(dream)
    memory_events = _safe_recent_events(memory)
    critic_events = _safe_recent_events(critic)
    bg_events = background_section(
        dream_events=dream_events,
        memory_events=memory_events,
        critic_events=critic_events,
        cap=background_cap,
    )

    return {
        SectionKind.NEEDS_YOU: needs,
        SectionKind.READY_TO_SHIP: ship,
        SectionKind.IN_FLIGHT: flight,
        SectionKind.BACKGROUND: background_rows(bg_events),
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


def _safe_recent_events(source: object) -> list[BackgroundEvent]:
    """Call ``.recent_events()`` on a source, swallow + log exceptions."""
    if source is None:
        return []
    fn = getattr(source, "recent_events", None)
    if fn is None:
        return []
    try:
        events = list(fn(limit=5))
    except Exception as exc:  # noqa: BLE001
        logger.debug("recent_events() failed on %s: %s", type(source).__name__, exc)
        return []
    return [e for e in events if isinstance(e, BackgroundEvent)]


def compute_orchestration_header(state: object | None) -> OrchestrationHeader | None:
    """Inspect an OrchestratorState (or None) and return a header payload."""
    from open_orchestrator.models.control_plane import OrchestrationHeader

    if state is None:
        return None
    try:
        tasks = list(state.tasks)  # type: ignore[attr-defined]
        goal = str(state.goal)  # type: ignore[attr-defined]
        feature_branch = str(state.feature_branch)  # type: ignore[attr-defined]
    except AttributeError:
        return None

    total = len(tasks)
    completed = sum(1 for t in tasks if str(getattr(t, "status", "")) in ("completed", "shipped"))
    running = sum(1 for t in tasks if str(getattr(t, "status", "")) == "running")
    failed = sum(1 for t in tasks if str(getattr(t, "status", "")) == "failed")
    return OrchestrationHeader(
        goal=goal,
        feature_branch=feature_branch,
        total=total,
        completed=completed,
        running=running,
        failed=failed,
    )

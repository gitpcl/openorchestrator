"""Centralized status semantics for AI activity.

All "is this status terminal / attention-needed / working / which bucket"
questions are answered here. Callers in ``commands/agent.py``, ``core/runtime.py``,
``core/batch.py``, ``core/status.py``, and ``core/switchboard.py`` import from
this module instead of re-deriving the predicates inline.

The two bucket helpers reflect a legitimate split that existed before
centralization:

- ``summary_bucket`` is backend-facing (used by ``StatusTracker.get_summary``).
  It groups ``WAITING`` with ``IDLE`` under ``"idle"`` because the agent is not
  actively consuming resources.
- ``ui_bucket`` is frontend-facing (used by the switchboard header). It groups
  ``WAITING`` with ``BLOCKED`` under ``"waiting"`` because both surface to the
  user as "needs attention".
"""

from __future__ import annotations

from typing import Literal

from open_orchestrator.models.status import AIActivityStatus

SummaryBucket = Literal["active", "idle", "blocked", "unknown"]
UIBucket = Literal["active", "waiting", "idle"]


def is_terminal(status: AIActivityStatus) -> bool:
    """True when the agent has finished and no further polling is needed.

    ``WAITING`` is terminal — the agent has stopped and is awaiting input or
    review. ``COMPLETED`` is terminal by definition. ``ERROR`` is terminal
    because the agent has stopped (callers still branch on it to pick the
    failure path).
    """
    return status in (
        AIActivityStatus.WAITING,
        AIActivityStatus.COMPLETED,
        AIActivityStatus.ERROR,
    )


def is_attention_needed(status: AIActivityStatus) -> bool:
    """True when human intervention is required."""
    return status in (AIActivityStatus.BLOCKED, AIActivityStatus.ERROR)


def is_working(status: AIActivityStatus) -> bool:
    """True when the agent is actively processing a task."""
    return status == AIActivityStatus.WORKING


def summary_bucket(status: AIActivityStatus) -> SummaryBucket:
    """Backend aggregation bucket for ``StatusTracker.get_summary``."""
    if status == AIActivityStatus.WORKING:
        return "active"
    if status in (
        AIActivityStatus.IDLE,
        AIActivityStatus.WAITING,
        AIActivityStatus.COMPLETED,
    ):
        return "idle"
    if status in (AIActivityStatus.BLOCKED, AIActivityStatus.ERROR):
        return "blocked"
    return "unknown"


def ui_bucket(status: AIActivityStatus) -> UIBucket:
    """Frontend aggregation bucket for switchboard header stats."""
    if status == AIActivityStatus.WORKING:
        return "active"
    if status in (AIActivityStatus.WAITING, AIActivityStatus.BLOCKED):
        return "waiting"
    return "idle"

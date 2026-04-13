"""Tests for the centralized status policy module."""

from __future__ import annotations

import pytest

from open_orchestrator.core import status_policy
from open_orchestrator.models.status import AIActivityStatus


@pytest.mark.parametrize(
    "status, expected",
    [
        (AIActivityStatus.IDLE, False),
        (AIActivityStatus.WORKING, False),
        (AIActivityStatus.BLOCKED, False),
        (AIActivityStatus.WAITING, True),
        (AIActivityStatus.COMPLETED, True),
        (AIActivityStatus.ERROR, True),
        (AIActivityStatus.UNKNOWN, False),
    ],
)
def test_is_terminal(status: AIActivityStatus, expected: bool) -> None:
    assert status_policy.is_terminal(status) is expected


@pytest.mark.parametrize(
    "status, expected",
    [
        (AIActivityStatus.IDLE, False),
        (AIActivityStatus.WORKING, False),
        (AIActivityStatus.BLOCKED, True),
        (AIActivityStatus.WAITING, False),
        (AIActivityStatus.COMPLETED, False),
        (AIActivityStatus.ERROR, True),
        (AIActivityStatus.UNKNOWN, False),
    ],
)
def test_is_attention_needed(status: AIActivityStatus, expected: bool) -> None:
    assert status_policy.is_attention_needed(status) is expected


@pytest.mark.parametrize(
    "status, expected",
    [
        (AIActivityStatus.IDLE, False),
        (AIActivityStatus.WORKING, True),
        (AIActivityStatus.BLOCKED, False),
        (AIActivityStatus.WAITING, False),
        (AIActivityStatus.COMPLETED, False),
        (AIActivityStatus.ERROR, False),
        (AIActivityStatus.UNKNOWN, False),
    ],
)
def test_is_working(status: AIActivityStatus, expected: bool) -> None:
    assert status_policy.is_working(status) is expected


@pytest.mark.parametrize(
    "status, expected",
    [
        (AIActivityStatus.IDLE, "idle"),
        (AIActivityStatus.WORKING, "active"),
        (AIActivityStatus.BLOCKED, "blocked"),
        (AIActivityStatus.WAITING, "idle"),
        (AIActivityStatus.COMPLETED, "idle"),
        (AIActivityStatus.ERROR, "blocked"),
        (AIActivityStatus.UNKNOWN, "unknown"),
    ],
)
def test_summary_bucket(status: AIActivityStatus, expected: str) -> None:
    assert status_policy.summary_bucket(status) == expected


@pytest.mark.parametrize(
    "status, expected",
    [
        (AIActivityStatus.IDLE, "idle"),
        (AIActivityStatus.WORKING, "active"),
        (AIActivityStatus.BLOCKED, "waiting"),
        (AIActivityStatus.WAITING, "waiting"),
        (AIActivityStatus.COMPLETED, "idle"),
        (AIActivityStatus.ERROR, "idle"),
        (AIActivityStatus.UNKNOWN, "idle"),
    ],
)
def test_ui_bucket(status: AIActivityStatus, expected: str) -> None:
    assert status_policy.ui_bucket(status) == expected


def test_every_status_covered_by_summary_bucket() -> None:
    """Regression guard: every enum member must map to a known bucket."""
    for status in AIActivityStatus:
        bucket = status_policy.summary_bucket(status)
        assert bucket in ("active", "idle", "blocked", "unknown")


def test_every_status_covered_by_ui_bucket() -> None:
    """Regression guard: every enum member must map to a known UI bucket."""
    for status in AIActivityStatus:
        bucket = status_policy.ui_bucket(status)
        assert bucket in ("active", "waiting", "idle")

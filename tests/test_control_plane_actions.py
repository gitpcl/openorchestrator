"""Sprint 024: tests for the action dispatcher."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from open_orchestrator.core import control_plane_actions as cpa
from open_orchestrator.core.control_plane_actions import (
    ActionResult,
    ControlPlaneActions,
    ControlPlaneRuntime,
    action_dismiss,
    action_review,
)
from open_orchestrator.core.critic import CriticFinding, CriticVerdict, Severity
from open_orchestrator.models.control_plane import ControlPlaneRow, RowAction, SectionKind


def _row(section: SectionKind, *actions: RowAction, **meta: str) -> ControlPlaneRow:
    return ControlPlaneRow(
        id=f"r:{section.value}",
        section=section,
        name="wt",
        summary="x",
        actions=tuple(actions),
        meta=meta,
    )


@pytest.mark.asyncio
async def test_resolve_returns_none_when_action_not_offered() -> None:
    runtime = ControlPlaneRuntime(repo_root="/tmp")
    actions = ControlPlaneActions(runtime)
    row = _row(SectionKind.READY_TO_SHIP, RowAction.SHIP)
    assert actions.resolve(row, RowAction.FIX) is None
    assert actions.resolve(row, "z") is None


@pytest.mark.asyncio
async def test_dispatch_unknown_action_returns_friendly_error() -> None:
    runtime = ControlPlaneRuntime(repo_root="/tmp")
    actions = ControlPlaneActions(runtime)
    row = _row(SectionKind.READY_TO_SHIP, RowAction.SHIP)
    result = await actions.dispatch(row, RowAction.FIX)
    assert result.ok is False
    assert "No action" in result.message


@pytest.mark.asyncio
async def test_review_uses_critic_lookup() -> None:
    verdict = CriticVerdict(
        action="ship",
        target="wt",
        findings=(CriticFinding(severity=Severity.BLOCKING, category="x", message="m"),),
    )
    runtime = ControlPlaneRuntime(
        repo_root="/tmp",
        critic_lookup=lambda wt: verdict if wt == "wt" else None,
    )
    row = _row(SectionKind.NEEDS_YOU, RowAction.REVIEW, worktree="wt")
    result = await action_review(row, runtime)
    assert result.ok
    assert "blocking" in result.detail.lower()


@pytest.mark.asyncio
async def test_attach_uses_backend_when_provided() -> None:
    called = {}

    async def fake_backend(wt: str) -> ActionResult:
        called["wt"] = wt
        return ActionResult(ok=True, message="attached", handoff=True)

    runtime = ControlPlaneRuntime(repo_root="/tmp", backend_attach=fake_backend)
    actions = ControlPlaneActions(runtime)
    row = _row(SectionKind.IN_FLIGHT, RowAction.ATTACH, worktree="wt7")
    result = await actions.dispatch(row, RowAction.ATTACH)
    assert called == {"wt": "wt7"}
    assert result.handoff is True


@pytest.mark.asyncio
async def test_dismiss_is_noop_success() -> None:
    runtime = ControlPlaneRuntime(repo_root="/tmp")
    row = _row(SectionKind.BACKGROUND, RowAction.DISMISS)
    result = await action_dismiss(row, runtime)
    assert result.ok
    assert "dismiss" in result.message.lower()


@pytest.mark.asyncio
async def test_handler_exception_becomes_action_error() -> None:
    async def bad(row, runtime):
        raise RuntimeError("boom")

    runtime = ControlPlaneRuntime(repo_root="/tmp")
    actions = ControlPlaneActions(
        runtime,
        table={(SectionKind.NEEDS_YOU, RowAction.REVIEW): bad},
    )
    row = _row(SectionKind.NEEDS_YOU, RowAction.REVIEW)
    result = await actions.dispatch(row, RowAction.REVIEW)
    assert result.ok is False
    assert "action error" in result.message


@pytest.mark.asyncio
async def test_ship_invokes_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ship goes through ``owt ship --yes`` — we monkeypatch the launcher."""
    captured: dict[str, list[str]] = {}

    async def fake_launch(*cmd, **kwargs):  # noqa: ANN001, ANN003
        captured["cmd"] = list(cmd)
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"ok\n", b""))
        proc.returncode = 0
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_launch)
    runtime = ControlPlaneRuntime(repo_root="/tmp")
    actions = ControlPlaneActions(runtime)
    row = _row(SectionKind.READY_TO_SHIP, RowAction.SHIP, worktree="wt-feat")
    result = await actions.dispatch(row, RowAction.SHIP)
    assert result.ok
    assert captured["cmd"][:4] == ["owt", "ship", "wt-feat", "--yes"]


@pytest.mark.asyncio
async def test_resolve_accepts_string_key() -> None:
    runtime = ControlPlaneRuntime(repo_root="/tmp")
    actions = ControlPlaneActions(runtime)
    row = _row(SectionKind.READY_TO_SHIP, RowAction.SHIP)
    handler = actions.resolve(row, "s")
    assert handler is not None
    assert handler is cpa.action_ship


# ── start work (n) ─────────────────────────────────────────────────────


def test_build_start_args_single() -> None:
    assert cpa.build_start_args("add auth", "single") == ["new", "add auth", "--yes"]


def test_build_start_args_plan() -> None:
    assert cpa.build_start_args("ship the dashboard", "plan") == ["plan", "ship the dashboard", "--start"]


def test_build_start_args_batch_requires_file() -> None:
    assert cpa.build_start_args("x", "batch") is None
    assert cpa.build_start_args("x", "batch", "plan.toml") == ["batch", "plan.toml"]


def test_build_start_args_unknown_mode() -> None:
    assert cpa.build_start_args("x", "nope") is None


@pytest.mark.asyncio
async def test_start_work_unknown_mode_returns_error() -> None:
    result = await cpa.start_work("x", "nope", "/tmp")
    assert result.ok is False
    assert "nope" in result.message


@pytest.mark.asyncio
async def test_start_work_single_invokes_owt_new(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    async def fake_launch(*cmd, **kwargs):  # noqa: ANN001, ANN003
        captured["cmd"] = list(cmd)
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"created\n", b""))
        proc.returncode = 0
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_launch)
    result = await cpa.start_work("add auth flow", "single", "/tmp")
    assert result.ok
    assert captured["cmd"][:4] == ["owt", "new", "add auth flow", "--yes"]

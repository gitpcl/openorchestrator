"""Action dispatcher for control-plane rows.

Maps ``(SectionKind, RowAction)`` (or just ``key``) to a callable that
performs the action against the supplied runtime. The dispatcher is the
single place where the UI hands off to subprocess / tmux / herdr — the
view layer only knows about rows and key presses.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess  # noqa: S404 — used with argv lists only, never shell=True
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from open_orchestrator.models.control_plane import ControlPlaneRow, RowAction, SectionKind

if TYPE_CHECKING:
    from open_orchestrator.core.critic import CriticVerdict

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    """Outcome of one dispatched action."""

    ok: bool
    message: str
    detail: str = ""
    handoff: bool = False


ActionCallable = Callable[["ControlPlaneRow", "ControlPlaneRuntime"], Awaitable[ActionResult]]


@dataclass
class ControlPlaneRuntime:
    """Bundle of dependencies an action may touch.

    Held by the view and passed to the dispatcher. Each action receives
    this and the row it was dispatched against.
    """

    repo_root: str
    backend_attach: Callable[[str], Awaitable[ActionResult]] | None = None
    critic_lookup: Callable[[str], CriticVerdict | None] | None = None
    editor: str = ""

    def __post_init__(self) -> None:
        if not self.editor:
            self.editor = os.environ.get("EDITOR", "")


async def _run_owt(args: list[str], cwd: str, *, timeout: float = 120.0) -> ActionResult:
    """Invoke ``owt`` as a subprocess and surface the result."""
    cmd = ["owt", *args]
    try:
        proc = await asyncio.create_subprocess_exec(  # noqa: S603
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return ActionResult(ok=False, message=f"timed out: {' '.join(cmd[:3])}")
    except OSError as err:
        return ActionResult(ok=False, message=f"failed to launch {cmd[0]}: {err}")

    if proc.returncode == 0:
        last_line = (stdout.decode(errors="replace").strip().splitlines() or [""])[-1]
        return ActionResult(ok=True, message=last_line or "ok")
    err_line = (stderr.decode(errors="replace").strip().splitlines() or [""])[-1]
    return ActionResult(ok=False, message=err_line or "non-zero exit", detail=stderr.decode(errors="replace"))


async def action_ship(row: ControlPlaneRow, runtime: ControlPlaneRuntime) -> ActionResult:
    worktree = row.meta.get("worktree") or row.name
    return await _run_owt(["ship", worktree, "--yes"], runtime.repo_root)


async def action_merge(row: ControlPlaneRow, runtime: ControlPlaneRuntime) -> ActionResult:
    worktree = row.meta.get("worktree") or row.name
    return await _run_owt(["merge", worktree], runtime.repo_root)


async def action_attach(row: ControlPlaneRow, runtime: ControlPlaneRuntime) -> ActionResult:
    worktree = row.meta.get("worktree") or row.name
    if runtime.backend_attach is not None:
        return await runtime.backend_attach(worktree)
    return await _run_owt(["attach", worktree], runtime.repo_root)


async def action_review(row: ControlPlaneRow, runtime: ControlPlaneRuntime) -> ActionResult:
    """REVIEW expands the row into a critic verdict summary.

    The view layer renders ``detail``; the dispatcher only fetches it.
    """
    worktree = row.meta.get("worktree") or row.name
    if runtime.critic_lookup is not None:
        verdict = runtime.critic_lookup(worktree)
        if verdict is not None:
            return ActionResult(ok=True, message=verdict.summary, detail=_render_verdict(verdict))
    return ActionResult(ok=True, message=f"No critic verdict cached for {worktree}.")


async def action_fix(row: ControlPlaneRow, runtime: ControlPlaneRuntime) -> ActionResult:
    """Open the conflicted files (or the worktree path) in ``$EDITOR``."""
    if not runtime.editor:
        return ActionResult(ok=False, message="$EDITOR is not set")
    worktree = row.meta.get("worktree") or row.name
    files = row.meta.get("files", "")
    target = files if files else worktree
    editor_argv = shlex.split(runtime.editor) + shlex.split(target)
    try:
        subprocess.Popen(  # noqa: S603 — argv list, no shell
            editor_argv,
            cwd=runtime.repo_root,
            start_new_session=True,
        )
    except OSError as err:
        return ActionResult(ok=False, message=f"failed to launch editor: {err}")
    return ActionResult(ok=True, message=f"opened {target} in editor", handoff=True)


async def action_dismiss(row: ControlPlaneRow, runtime: ControlPlaneRuntime) -> ActionResult:
    """No-op acknowledgement — the view removes the row from its list."""
    return ActionResult(ok=True, message=f"dismissed {row.name}")


_DEFAULT_TABLE: dict[tuple[SectionKind, RowAction], ActionCallable] = {
    (SectionKind.NEEDS_YOU, RowAction.FIX): action_fix,
    (SectionKind.NEEDS_YOU, RowAction.REVIEW): action_review,
    (SectionKind.NEEDS_YOU, RowAction.ATTACH): action_attach,
    (SectionKind.READY_TO_SHIP, RowAction.SHIP): action_ship,
    (SectionKind.READY_TO_SHIP, RowAction.MERGE): action_merge,
    (SectionKind.READY_TO_SHIP, RowAction.REVIEW): action_review,
    (SectionKind.READY_TO_SHIP, RowAction.ATTACH): action_attach,
    (SectionKind.IN_FLIGHT, RowAction.ATTACH): action_attach,
    (SectionKind.IN_FLIGHT, RowAction.REVIEW): action_review,
    (SectionKind.BACKGROUND, RowAction.DISMISS): action_dismiss,
}


class ControlPlaneActions:
    """The dispatcher.

    Resolves ``dispatch(row, key)`` to a coroutine. Keys are single
    characters (the ``RowAction`` enum value), but you can also pass a
    ``RowAction`` directly.
    """

    def __init__(
        self,
        runtime: ControlPlaneRuntime,
        *,
        table: dict[tuple[SectionKind, RowAction], ActionCallable] | None = None,
    ) -> None:
        self._runtime = runtime
        self._table = dict(_DEFAULT_TABLE)
        if table:
            self._table.update(table)

    def resolve(self, row: ControlPlaneRow, key: str | RowAction) -> ActionCallable | None:
        """Return the callable that handles ``(row, key)``, or None."""
        action = _coerce_action(key)
        if action is None or action not in row.actions:
            return None
        return self._table.get((row.section, action))

    async def dispatch(self, row: ControlPlaneRow, key: str | RowAction) -> ActionResult:
        """Resolve and invoke. Returns a friendly error result if unhandled."""
        handler = self.resolve(row, key)
        if handler is None:
            return ActionResult(
                ok=False,
                message=f"No action '{key}' for {row.section.value} row",
            )
        try:
            return await handler(row, self._runtime)
        except Exception as err:  # noqa: BLE001
            logger.exception("Action %s failed for %s", key, row.id)
            return ActionResult(ok=False, message=f"action error: {err}")

    @property
    def runtime(self) -> ControlPlaneRuntime:
        return self._runtime


def _coerce_action(key: str | RowAction) -> RowAction | None:
    if isinstance(key, RowAction):
        return key
    try:
        return RowAction(key)
    except ValueError:
        return None


def _render_verdict(verdict: CriticVerdict) -> str:
    """Render a critic verdict as a multi-line review panel."""
    lines = [verdict.summary, ""]
    for finding in verdict.findings:
        lines.append(f"  [{finding.severity.value}] {finding.category}: {finding.message}")
        if finding.details:
            for detail_line in finding.details.splitlines():
                lines.append(f"      {detail_line}")
    return "\n".join(lines)

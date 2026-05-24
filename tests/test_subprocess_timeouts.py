"""Subprocess timeout coverage tests.

Two guarantees this module enforces:

1. **Regression guard** — every ``subprocess.run`` / ``subprocess.check_call`` /
   ``subprocess.check_output`` site under ``src/open_orchestrator/`` must pass
   an explicit ``timeout=`` argument. A hung child can never silently block the
   switchboard, dream daemon, or orchestrator again.
2. **Behavior check** — the timeout constants in :mod:`core._subprocess` are at
   the right order of magnitude per operation class (tmux/git/gh/ai_cli), and a
   simulated ``TimeoutExpired`` raised from the tmux probe surfaces cleanly
   without crashing the caller.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from open_orchestrator.core._subprocess import (
    AI_CLI_TIMEOUT,
    FAST_PROBE_TIMEOUT,
    GH_TIMEOUT,
    GIT_TIMEOUT,
    TMUX_TIMEOUT,
    class_timeout,
    run_with_class_timeout,
)

SRC_ROOT = Path(__file__).parent.parent / "src" / "open_orchestrator"

_SUBPROCESS_CALL_RE = re.compile(r"subprocess\.(run|check_call|check_output)\(")


def _iter_subprocess_calls(src: str) -> list[tuple[int, str]]:
    """Walk a Python source string and yield (line_no, call_text) for every
    ``subprocess.run`` / ``check_call`` / ``check_output`` invocation."""
    calls: list[tuple[int, str]] = []
    i = 0
    while True:
        match = _SUBPROCESS_CALL_RE.search(src, i)
        if not match:
            break
        start = match.start()
        # Walk forward matching parens to find the call's end
        depth = 0
        end = match.end()
        for j in range(end - 1, len(src)):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        calls.append((src[:start].count("\n") + 1, src[start:end]))
        i = end
    return calls


def test_every_subprocess_call_has_explicit_timeout() -> None:
    """Regression guard: no ``subprocess.run`` in src/ may omit ``timeout=``.

    The contract is *explicit* timeout — even ``timeout=None`` is acceptable
    (it documents a deliberate "no timeout" decision for interactive shells
    like ``$EDITOR`` or ``tmux attach-session``). What we forbid is silent
    omission.
    """
    offenders: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        src = py.read_text()
        for line_no, call in _iter_subprocess_calls(src):
            if "timeout=" not in call:
                offenders.append(f"{py.relative_to(SRC_ROOT.parent.parent)}:{line_no}")

    assert not offenders, "These subprocess sites lack an explicit timeout= argument:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize(
    "name,value,lower,upper",
    [
        ("TMUX_TIMEOUT", TMUX_TIMEOUT, 1, 10),  # local IPC, sub-second usual
        ("GIT_TIMEOUT", GIT_TIMEOUT, 10, 60),  # checkout/fetch can be slow
        ("GH_TIMEOUT", GH_TIMEOUT, 30, 180),  # network-bound
        ("AI_CLI_TIMEOUT", AI_CLI_TIMEOUT, 60, 1800),  # long planning runs OK
        ("FAST_PROBE_TIMEOUT", FAST_PROBE_TIMEOUT, 1, 10),
    ],
)
def test_timeout_constants_in_sane_band(name: str, value: int, lower: int, upper: int) -> None:
    """Each timeout class should sit in its operation's expected band."""
    assert lower <= value <= upper, f"{name}={value} outside expected band [{lower}, {upper}]"


def test_tmux_probe_handles_timeout_expired_cleanly() -> None:
    """A TimeoutExpired from the tmux activity probe must not crash callers.

    ``TmuxManager.detect_session_activity`` is the hottest tmux subprocess
    path — it runs every refresh from the switchboard. Verifying it
    swallows ``TimeoutExpired`` and returns ``None`` protects the UI loop
    from a hung tmux server.
    """
    from open_orchestrator.core.tmux_manager import TmuxManager

    mgr = TmuxManager()
    with (
        patch.object(mgr, "session_exists", return_value=True),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=TMUX_TIMEOUT),
        ),
    ):
        result = mgr.detect_session_activity("some-session")

    assert result is None


def test_ai_cli_planner_timeout_raises_friendly_runtime_error() -> None:
    """``batch.plan_tasks`` wraps its AI invocation with TimeoutExpired so a
    hung planner becomes a single user-facing RuntimeError, not a stack
    trace. This ties the AI CLI class to the timeout contract."""
    from open_orchestrator.core import batch

    expired = subprocess.TimeoutExpired(cmd=["claude", "--print"], timeout=300)
    with patch("subprocess.run", side_effect=expired):
        with pytest.raises(RuntimeError, match=r"(?i)tim(ed )?out|timeout"):
            batch.plan_tasks(goal="x", ai_tool="claude", repo_path=str(SRC_ROOT))


@pytest.mark.parametrize(
    "op_class,expected",
    [
        ("tmux", TMUX_TIMEOUT),
        ("git", GIT_TIMEOUT),
        ("gh", GH_TIMEOUT),
        ("ai_cli", AI_CLI_TIMEOUT),
        ("fast", FAST_PROBE_TIMEOUT),
    ],
)
def test_class_timeout_routes_to_constant(op_class: str, expected: int) -> None:
    """``class_timeout`` is the single source of truth for which constant a
    given operation class uses. A bug here would propagate to every caller."""
    assert class_timeout(op_class) == expected  # type: ignore[arg-type]


def test_class_timeout_unknown_class_raises() -> None:
    """Unknown classes must fail loudly (no permissive fallback)."""
    with pytest.raises(KeyError):
        class_timeout("smtp")  # type: ignore[arg-type]


@pytest.mark.parametrize("op_class", ["tmux", "git", "gh", "ai_cli"])
def test_run_with_class_timeout_propagates_timeout_expired(op_class: str) -> None:
    """One dedicated TimeoutExpired exercise per operation class.

    The sprint contract requires at least one TimeoutExpired test per
    (git, gh, tmux, ai_cli) class. ``run_with_class_timeout`` is the
    canonical chokepoint, so verifying it surfaces ``TimeoutExpired``
    cleanly for every class covers the contract uniformly — including
    the gh class, which has no in-tree call site today but is reserved
    for forthcoming PR/issue automation.
    """
    expected = class_timeout(op_class)  # type: ignore[arg-type]
    expired = subprocess.TimeoutExpired(cmd=[op_class, "--help"], timeout=expected)

    with patch("subprocess.run", side_effect=expired):
        with pytest.raises(subprocess.TimeoutExpired) as info:
            run_with_class_timeout([op_class, "--help"], op_class)  # type: ignore[arg-type]

    assert info.value.timeout == expected


def test_run_with_class_timeout_invokes_on_timeout_callback() -> None:
    """The ``on_timeout`` hook lets a caller flip a worktree's status to
    STALLED (or emit a metric) without re-implementing the try/except in
    every subprocess site."""
    fired: list[str] = []

    def _cb() -> None:
        fired.append("called")

    expired = subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=GIT_TIMEOUT)
    with patch("subprocess.run", side_effect=expired):
        with pytest.raises(subprocess.TimeoutExpired):
            run_with_class_timeout(["git", "fetch"], "git", on_timeout=_cb)

    assert fired == ["called"], "on_timeout callback must fire exactly once on TimeoutExpired"


def test_run_with_class_timeout_callback_failure_does_not_mask_timeout() -> None:
    """If the callback itself raises, the original TimeoutExpired must still
    propagate — masking it would lose the diagnostic the caller needs."""

    def _bad_cb() -> None:
        raise RuntimeError("tracker dead")

    expired = subprocess.TimeoutExpired(cmd=["gh", "pr", "list"], timeout=GH_TIMEOUT)
    with patch("subprocess.run", side_effect=expired):
        with pytest.raises(subprocess.TimeoutExpired):
            run_with_class_timeout(["gh", "pr", "list"], "gh", on_timeout=_bad_cb)


def test_sync_git_timeout_marks_worktree_stalled(tmp_path: Path) -> None:
    """End-to-end: SyncService.sync_worktree catches a git TimeoutExpired,
    marks the worktree STALLED via the injected StatusTracker, and returns
    a friendly ERROR result.

    This is the git-class TimeoutExpired smoke against a real boundary
    (sync.py is the only sync-time git caller in core/). The unit-class
    sweep above proves *all four* timeout classes route correctly; this
    test proves the routing is also wired into a live caller.
    """
    from open_orchestrator.core.status import StatusConfig, StatusTracker
    from open_orchestrator.core.sync import SyncConfig, SyncService, SyncStatus
    from open_orchestrator.models.status import AIActivityStatus

    # Create a fake worktree directory so the path-exists guard passes.
    worktree_dir = tmp_path / "feat-x"
    worktree_dir.mkdir()

    tracker = StatusTracker(config=StatusConfig(storage_path=tmp_path / "status.db"))
    tracker.initialize_status(
        worktree_name=worktree_dir.name,
        worktree_path=str(worktree_dir),
        branch="feat/x",
        tmux_session=None,
    )

    service = SyncService(config=SyncConfig(), status_tracker=tracker)

    # Force the upstream-detection path to succeed so we hit the
    # fetch_upstream → TimeoutExpired branch (not the no-upstream guard).
    expired = subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=30)

    with (
        patch.object(service, "_get_current_branch", return_value="feat/x"),
        patch.object(service, "_get_upstream_branch", return_value="origin/feat/x"),
        patch.object(service, "_has_uncommitted_changes", return_value=False),
        patch.object(service, "_fetch_upstream", side_effect=expired),
    ):
        result = service.sync_worktree(str(worktree_dir))

    assert result.status == SyncStatus.ERROR
    assert "timed out" in result.message.lower()
    assert "stalled" in result.message.lower()

    after = tracker.get_status(worktree_dir.name)
    assert after is not None
    assert after.activity_status == AIActivityStatus.STALLED
    assert after.notes is not None and "timed out" in after.notes.lower()


def test_sync_git_timeout_without_tracker_still_returns_error(tmp_path: Path) -> None:
    """The tracker is optional — sync_worktree must still degrade gracefully
    on git timeout when nothing is observing the worktree."""
    from open_orchestrator.core.sync import SyncConfig, SyncService, SyncStatus

    worktree_dir = tmp_path / "feat-y"
    worktree_dir.mkdir()

    service = SyncService(config=SyncConfig())  # no status_tracker

    expired = subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=30)

    with (
        patch.object(service, "_get_current_branch", return_value="feat/y"),
        patch.object(service, "_get_upstream_branch", return_value="origin/feat/y"),
        patch.object(service, "_has_uncommitted_changes", return_value=False),
        patch.object(service, "_fetch_upstream", side_effect=expired),
    ):
        result = service.sync_worktree(str(worktree_dir))

    assert result.status == SyncStatus.ERROR
    assert "timed out" in result.message.lower()

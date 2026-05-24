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

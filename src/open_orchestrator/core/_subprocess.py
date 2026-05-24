"""Shared subprocess timeout constants.

Every ``subprocess.run`` / ``subprocess.Popen`` invocation in ``core/`` and
``commands/`` should pass one of these constants as the ``timeout=`` argument
so a hung child can never block the switchboard, orchestrator, or daemon
indefinitely. The classes are calibrated to the slowest reasonable success
case for each tool — long enough that a healthy call never trips, short
enough that a stalled call surfaces within the user's attention span.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any, Literal

logger = logging.getLogger(__name__)

# tmux commands — pure local IPC; should respond in milliseconds. The 5 s
# floor leaves headroom for first-time socket creation on a cold tmux.
TMUX_TIMEOUT = 5

# git plumbing/porcelain — most calls are sub-second, but large worktree
# checkouts and "git fetch" against a remote can push 10–20 s.
GIT_TIMEOUT = 30

# gh CLI — network-bound; pull/issue listing on a busy repo can be slow.
GH_TIMEOUT = 60

# AI CLI invocations (Claude, droid, pi, opencode) — these can legitimately
# run for minutes during long planning/generation phases. The 600 s ceiling
# is the longest we'll let any single tool turn block without operator
# intervention; the orchestrator marks the worktree STALLED when it trips.
AI_CLI_TIMEOUT = 600

# Generic filesystem helpers (``which``, ``brew --prefix``, etc.) — should
# always be near-instant; a multi-second hang means a broken shim.
FAST_PROBE_TIMEOUT = 5

# Interactive editor handoff (``$EDITOR plan.toml``) — no upper bound the
# tool can sensibly enforce; the editor owns the user's session.
EDITOR_TIMEOUT: float | None = None

OperationClass = Literal["tmux", "git", "gh", "ai_cli", "fast"]

_CLASS_TIMEOUTS: dict[str, int] = {
    "tmux": TMUX_TIMEOUT,
    "git": GIT_TIMEOUT,
    "gh": GH_TIMEOUT,
    "ai_cli": AI_CLI_TIMEOUT,
    "fast": FAST_PROBE_TIMEOUT,
}


def class_timeout(op_class: OperationClass) -> int:
    """Return the timeout (seconds) calibrated for ``op_class``.

    Raises ``KeyError`` for unknown classes so a typo at the call site
    fails loudly instead of falling back to a too-permissive default.
    """
    return _CLASS_TIMEOUTS[op_class]


def run_with_class_timeout(
    args: list[str],
    op_class: OperationClass,
    *,
    on_timeout: Any = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Invoke ``subprocess.run`` with the timeout matched to ``op_class``.

    On ``subprocess.TimeoutExpired``:
      - logs at WARNING with the class + command head,
      - invokes the optional ``on_timeout`` callback (e.g. a
        ``StatusTracker.mark_stalled`` partial) for caller-side side effects,
      - re-raises so the caller can choose to surface a friendly error.

    The single chokepoint matters because each new gh / tmux / AI-CLI site
    in the codebase otherwise reinvents the same try/except pattern with
    drift between them. Routing through this helper guarantees consistent
    log lines, a single test surface for *all four* timeout classes, and
    one place to add metrics later.
    """
    timeout = class_timeout(op_class)
    try:
        return subprocess.run(args, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        head = args[0] if args else "<empty>"
        logger.warning("subprocess.timeout class=%s cmd=%s timeout=%ds", op_class, head, timeout)
        if callable(on_timeout):
            try:
                on_timeout()
            except Exception:  # noqa: BLE001 — callback failures must not mask the timeout
                logger.exception("on_timeout callback failed for class=%s cmd=%s", op_class, head)
        raise

"""Shared subprocess timeout constants.

Every ``subprocess.run`` / ``subprocess.Popen`` invocation in ``core/`` and
``commands/`` should pass one of these constants as the ``timeout=`` argument
so a hung child can never block the switchboard, orchestrator, or daemon
indefinitely. The classes are calibrated to the slowest reasonable success
case for each tool — long enough that a healthy call never trips, short
enough that a stalled call surfaces within the user's attention span.
"""

from __future__ import annotations

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

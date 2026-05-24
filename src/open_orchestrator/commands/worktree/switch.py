"""``owt switch`` — jump to a worktree's backend session."""

from __future__ import annotations

import click

from open_orchestrator.commands import worktree as _pkg
from open_orchestrator.commands._shared import resolve_session_target


@click.command("switch")
@click.argument("identifier")
def switch_worktree(identifier: str) -> None:
    """Jump to a worktree's session via its backend (tmux or herdr).

    Works for both worktree-mode and branch-mode sessions. Backend is
    resolved from the status DB row written at create-time so no flag is
    needed here — herdr-created sessions hand off to herdr, tmux-created
    sessions hand off to tmux.
    """
    from open_orchestrator.core.backend_factory import select_backend, select_backend_for_session

    wt_manager = _pkg.get_worktree_manager()
    tracker = _pkg.get_status_tracker(wt_manager.git_root)
    resolved = resolve_session_target(identifier, wt_manager, tracker)

    session = tracker.get_backend_session(resolved.name)
    if session is None:
        # Legacy row or no row: fall back to tmux + session_for lookup.
        backend = select_backend(_pkg.load_config_safe().backend, override="tmux")
        session = backend.session_for(resolved.name)
        if session is None:
            raise click.ClickException(f"No session found for '{resolved.name}'. Run 'owt new' to create one.")
    else:
        backend = select_backend_for_session(session)
        if not backend.is_alive(session):
            raise click.ClickException(f"No {session.kind.value} session for '{resolved.name}'. Run 'owt new' to create one.")
    backend.attach(session)

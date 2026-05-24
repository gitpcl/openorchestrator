"""``owt attach`` — hand off to a session via its recorded backend."""

from __future__ import annotations

import click

from open_orchestrator.commands import worktree as _pkg
from open_orchestrator.commands._shared import resolve_session_target


@click.command("attach")
@click.argument("identifier")
@click.option("--herdr", "force_herdr", is_flag=True, help="Force herdr backend.")
@click.option("--tmux", "force_tmux", is_flag=True, help="Force tmux backend.")
def attach_worktree(identifier: str, force_herdr: bool, force_tmux: bool) -> None:
    """Hand off to a worktree's session via the active backend.

    By default reads the backend kind recorded at create-time so
    herdr-created sessions hand off to herdr and tmux-created sessions
    hand off to tmux.

    Pass ``--herdr`` / ``--tmux`` to force a specific backend. When the
    forced backend differs from the recorded backend, owt re-resolves
    the session via ``backend.session_for(name)`` instead of coercing
    the recorded id (Sprint 026 P4 — the id formats are different, so
    coercing would silently misroute the attach).
    """
    from open_orchestrator.config import load_config
    from open_orchestrator.core.backend_factory import (
        BackendUnavailableError,
        select_backend,
        select_backend_for_session,
    )

    if force_herdr and force_tmux:
        raise click.ClickException("--herdr and --tmux are mutually exclusive.")
    override = "herdr" if force_herdr else "tmux" if force_tmux else None

    wt_manager = _pkg.get_worktree_manager()
    tracker = _pkg.get_status_tracker(wt_manager.git_root)
    resolved = resolve_session_target(identifier, wt_manager, tracker)

    from open_orchestrator.models.backend import BackendSession

    recorded_session = tracker.get_backend_session(resolved.name)
    session: BackendSession | None

    # No override: prefer the recorded session via its native backend.
    if override is None:
        if recorded_session is not None:
            backend = select_backend_for_session(recorded_session)
            session = recorded_session
        else:
            try:
                backend = select_backend(load_config().backend, override="tmux")
            except BackendUnavailableError as err:
                raise click.ClickException(str(err)) from err
            session = backend.session_for(resolved.name)
            if session is None:
                raise click.ClickException(f"No session for '{resolved.name}'. Run 'owt new' to create one.")
        backend.attach(session)
        return

    # Forced override: re-resolve via the forced backend rather than
    # coercing the recorded session (recorded ids are backend-specific).
    try:
        backend = select_backend(load_config().backend, override=override)
    except BackendUnavailableError as err:
        raise click.ClickException(str(err)) from err

    if recorded_session is not None and recorded_session.kind.value != override:
        # Re-resolve under the forced backend; do not pass the recorded id.
        session = backend.session_for(resolved.name)
        if session is None:
            raise click.ClickException(
                f"No {override} session for '{resolved.name}'. Recorded as {recorded_session.kind.value}. "
                f"Drop --{override} to use the recorded backend."
            )
    else:
        session = recorded_session or backend.session_for(resolved.name)
        if session is None:
            raise click.ClickException(f"No {backend.kind.value} session for '{resolved.name}'. Run 'owt new' to create one.")
    backend.attach(session)

"""Agent interaction commands: send, wait, hook, note."""

from __future__ import annotations

import json
import logging
import time

import click

from open_orchestrator.commands._shared import console, get_status_tracker, get_worktree_manager
from open_orchestrator.core.worktree import WorktreeNotFoundError
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

logger = logging.getLogger(__name__)


@click.command("send")
@click.argument("identifier", required=False)
@click.argument("message", nargs=-1, required=True)
@click.option("--pane", "pane_index", type=int, default=0, help="Target pane index.")
@click.option("--all", "send_all", is_flag=True, help="Send to ALL worktrees.")
@click.option("--working", "send_working", is_flag=True, help="Send only to WORKING worktrees.")
@click.option("--swarm", "swarm_id", default=None, help="Broadcast to all workers in a swarm.")
def send_to_worktree(
    identifier: str | None,
    message: tuple[str, ...],
    pane_index: int,
    send_all: bool,
    send_working: bool,
    swarm_id: str | None,
) -> None:
    """Send a command/message to a worktree's AI agent.

    Examples:
        owt send auth-jwt "Fix the failing tests"
        owt send --all "Run tests"
        owt send --working "Wrap up and commit"
        owt send --swarm swarm-abc12345 "status check"
    """
    from open_orchestrator.core.tmux_manager import TmuxError, TmuxManager

    msg = " ".join(message)
    tmux = TmuxManager()
    tracker = get_status_tracker()

    if swarm_id:
        from open_orchestrator.commands.swarm_cmd import get_manager
        from open_orchestrator.core.swarm import SwarmError

        try:
            targets = get_manager().broadcast(swarm_id, msg)
        except SwarmError as exc:
            raise click.ClickException(str(exc)) from exc
        console.print(f"[green]Broadcast to {len(targets)} swarm worker(s):[/green] {msg[:80]}")
        return

    if send_all or send_working:
        # Broadcast mode
        statuses = tracker.get_all_statuses()
        if send_working:
            statuses = [s for s in statuses if s.activity_status == AIActivityStatus.WORKING]

        sent = 0
        for s in statuses:
            if s.tmux_session and tmux.session_exists(s.tmux_session):
                try:
                    tmux.send_keys_to_pane(s.tmux_session, msg, pane_index=pane_index)
                    tracker.record_command(s.worktree_name, msg)
                    sent += 1
                except TmuxError:
                    console.print(f"[yellow]Failed to send to {s.worktree_name}[/yellow]")
        console.print(f"[green]Broadcast to {sent} worktree(s):[/green] {msg[:80]}")
        return

    if not identifier:
        raise click.ClickException("Specify a worktree name, or use --all / --working / --swarm")

    wt_manager = get_worktree_manager()
    try:
        worktree = wt_manager.get(identifier)
    except WorktreeNotFoundError as e:
        raise click.ClickException(str(e)) from e

    session_name = tmux.generate_session_name(worktree.name)

    if not tmux.session_exists(session_name):
        raise click.ClickException(f"No tmux session for '{worktree.name}'.")

    try:
        tmux.send_keys_to_pane(session_name, msg, pane_index=pane_index)
        console.print(f"[green]Sent to {worktree.name}:[/green] {msg[:80]}")
    except TmuxError as e:
        raise click.ClickException(str(e)) from e

    try:
        tracker.record_command(worktree.name, msg)
    except Exception:
        logger.debug("Failed to record command for %s", worktree.name, exc_info=True)


@click.command("wait")
@click.argument("worktree_name")
@click.option("--timeout", type=int, default=600, help="Max wait time in seconds (default 600).")
@click.option("--poll", type=int, default=10, help="Poll interval in seconds.")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
def wait_for_worktree(worktree_name: str, timeout: int, poll: int, json_output: bool) -> None:
    """Wait for a worktree's agent to finish (reach WAITING/COMPLETED).

    Polls status until the agent is done or timeout is reached.
    Useful for CI/CD pipelines and scripted workflows.

    Examples:
        owt wait auth-jwt
        owt wait my-feature --timeout 1200
        owt wait my-feature --json
    """
    tracker = get_status_tracker()
    elapsed = 0
    status: WorktreeAIStatus | None = None
    terminal_states = {AIActivityStatus.WAITING, AIActivityStatus.COMPLETED, AIActivityStatus.ERROR}

    while elapsed < timeout:
        tracker.reload()
        status = tracker.get_status(worktree_name)
        if not status:
            raise click.ClickException(f"No status found for '{worktree_name}'")

        if status.activity_status in terminal_states:
            if json_output:
                console.print(
                    json.dumps(
                        {
                            "worktree": worktree_name,
                            "status": status.activity_status.value,
                            "elapsed": elapsed,
                            "task": status.current_task,
                        },
                        indent=2,
                    )
                )
            else:
                console.print(f"[green]{worktree_name}:[/green] {status.activity_status.value} ({elapsed}s)")
            if status.activity_status == AIActivityStatus.ERROR:
                raise SystemExit(1)
            return

        if not json_output:
            console.print(
                f"[dim]{worktree_name}: {status.activity_status.value} ({elapsed}s / {timeout}s)[/dim]",
                end="\r",
            )

        time.sleep(poll)
        elapsed += poll

    last_status = status.activity_status.value if status else "unknown"
    raise click.ClickException(f"Timeout after {timeout}s \u2014 {worktree_name} still {last_status}")


@click.command("note")
@click.argument("message", nargs=-1, required=True)
@click.option("--clear", is_flag=True, help="Clear all shared notes.")
def shared_note(message: tuple[str, ...], clear: bool) -> None:
    """Share context across all active agent sessions.

    Notes are injected into each worktree's CLAUDE.md so agents
    stay aware of cross-cutting changes.

    Examples:
        owt note "The users table now has a verified_at column"
        owt note "API endpoint changed from /api/v1 to /api/v2"
        owt note --clear
    """
    from open_orchestrator.core.environment import inject_shared_notes

    tracker = get_status_tracker()
    tracker.reload()

    if clear:
        tracker.clear_shared_notes()
        console.print("[green]Shared notes cleared.[/green]")
        return

    note_text = " ".join(message)
    tracker.add_shared_note(note_text)

    # Inject into all active worktrees' CLAUDE.md
    notes = tracker.get_shared_notes()
    injected = 0
    for s in tracker.get_all_statuses():
        try:
            inject_shared_notes(s.worktree_path, notes)
            injected += 1
        except Exception:
            logger.debug("Failed to inject notes into %s", s.worktree_name, exc_info=True)

    console.print(f"[green]Note shared with {injected} worktree(s):[/green] {note_text[:80]}")


@click.command("hook", hidden=True)
@click.option("--event", required=True, type=click.Choice(["working", "waiting", "blocked"]))
@click.option("--worktree", required=True)
def hook_event(event: str, worktree: str) -> None:
    """Handle AI tool hook events (internal use by installed hooks).

    This command is called by hooks installed in .claude/settings.local.json
    or .factory/settings.json. It updates the worktree's status in the
    shared status store so the switchboard reflects real-time state.
    """
    from datetime import datetime

    status_map = {
        "working": AIActivityStatus.WORKING,
        "waiting": AIActivityStatus.WAITING,
        "blocked": AIActivityStatus.BLOCKED,
    }

    try:
        tracker = get_status_tracker()
        wt_status = tracker.get_status(worktree)
        if wt_status:
            wt_status.activity_status = status_map[event]
            wt_status.updated_at = datetime.now()
            tracker.set_status(wt_status)
    except Exception:
        logger.debug("Hook event handler failed for %s", worktree, exc_info=True)


def register(main: click.Group) -> None:
    """Register agent commands on the main CLI group."""
    main.add_command(send_to_worktree)
    main.add_command(wait_for_worktree)
    main.add_command(shared_note)
    main.add_command(hook_event)

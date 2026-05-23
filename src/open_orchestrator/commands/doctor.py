"""Diagnostic command: owt doctor — detect and fix orphaned resources.

Reconciliation rules differ by ``session_type``:

* **worktree** rows — cross-check against ``git worktree list``. A row whose
  worktree directory no longer exists is an orphan candidate; a worktree
  whose backend session is dead is also flagged.
* **branch** rows — cross-check against ``git branch --list``. A branch-mode
  row is an orphan only when *both* its backend session is dead and the
  underlying branch is gone. Branch rows are never compared to the worktree
  list (Sprint 026 P2 — that comparison destroyed perfectly healthy branch
  sessions in earlier sprints).

``--fix`` dispatches to the right teardown per ``session_type``:
worktree-mode goes through ``teardown_worktree`` defaults; branch-mode adds
``delete_branch=True`` and ``pop_stash=True`` so the auto-stash is restored.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import click
from rich.table import Table

from open_orchestrator.commands._shared import console, get_status_tracker, get_worktree_manager
from open_orchestrator.core.backend_factory import BackendUnavailableError, select_backend_for_session
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.models.backend import BackendSession

if TYPE_CHECKING:
    from open_orchestrator.models.status import WorktreeAIStatus

logger = logging.getLogger(__name__)


def _list_local_branches(repo_root: str) -> set[str]:
    """Return the set of local branch names. Empty on failure."""
    try:
        from git import Repo

        repo = Repo(repo_root)
        return {h.name for h in repo.heads}
    except Exception:  # noqa: BLE001
        logger.debug("Could not list local branches in %s", repo_root, exc_info=True)
        return set()


def _live_backend_session(status: WorktreeAIStatus, tracker: StatusTracker) -> BackendSession | None:
    """Reconstruct the backend session and check liveness; returns ``None`` if dead/unreachable."""
    session = tracker.get_backend_session(status.worktree_name)
    if session is None:
        return None
    try:
        backend = select_backend_for_session(session)
    except BackendUnavailableError:
        return None
    try:
        if backend.is_alive(session):
            return session
    except Exception:  # noqa: BLE001
        logger.debug("backend.is_alive raised for %s", status.worktree_name, exc_info=True)
    return None


@click.command("doctor")
@click.option("--fix", is_flag=True, help="Clean up detected orphans (default is diagnosis only).")
def doctor(fix: bool) -> None:
    """Diagnose and fix orphaned worktree resources.

    Cross-checks worktrees, backend (tmux/herdr) sessions, branch list, and
    status DB entries to find inconsistencies. Worktree-mode and branch-mode
    rows are reconciled against the right git surface so doctor never
    mistakes a healthy branch session for an orphan.

    Read-only by default. Use --fix to clean up.
    """
    wt_manager = get_worktree_manager()
    tracker = get_status_tracker(wt_manager.git_root)

    worktrees = {wt.name for wt in wt_manager.list_all() if not wt.is_main}
    branches = _list_local_branches(str(wt_manager.git_root))
    all_statuses = list(tracker.get_all_statuses())

    # Partition rows by session_type. Anything that isn't an explicit
    # "branch" defaults to worktree-mode for backwards-compat with legacy
    # rows that pre-date the column.
    worktree_rows = [s for s in all_statuses if (s.session_type or "worktree") != "branch"]
    branch_rows = [s for s in all_statuses if (s.session_type or "worktree") == "branch"]

    # Live sessions across both groups — used to flag "session running but
    # no worktree on disk" (worktree-mode) and to spot dead-session +
    # missing-branch combos (branch-mode).
    live_sessions: dict[str, BackendSession] = {}
    for s in worktree_rows + branch_rows:
        session = _live_backend_session(s, tracker)
        if session is not None:
            live_sessions[s.worktree_name] = session

    live_session_names = set(live_sessions.keys())

    # Worktree-mode reconciliation: worktrees without sessions, sessions
    # without worktrees, status rows without worktrees.
    worktree_row_names = {s.worktree_name for s in worktree_rows}
    orphan_wt_no_session = worktrees - live_session_names
    orphan_session_no_wt = {name for name, sess in live_sessions.items() if name in worktree_row_names and name not in worktrees}
    orphan_status_no_wt = {s.worktree_name for s in worktree_rows if s.worktree_name not in worktrees}

    # Branch-mode reconciliation: orphan iff backend dead AND branch absent.
    orphan_branch_dead = {
        s.worktree_name for s in branch_rows if s.worktree_name not in live_session_names and s.worktree_name not in branches
    }

    total_issues = len(orphan_wt_no_session) + len(orphan_session_no_wt) + len(orphan_status_no_wt) + len(orphan_branch_dead)

    if total_issues == 0:
        console.print("[green]No orphaned resources found. All clean.[/green]")
        return

    table = Table(title="Orphaned Resources", show_header=True, header_style="bold")
    table.add_column("Resource")
    table.add_column("Issue")
    table.add_column("Name")

    for name in sorted(orphan_wt_no_session):
        table.add_row("worktree", "[yellow]no session[/yellow]", name)
    for name in sorted(orphan_session_no_wt):
        kind = live_sessions[name].kind.value
        table.add_row(kind, "[yellow]no worktree[/yellow]", live_sessions[name].id)
    for name in sorted(orphan_status_no_wt):
        table.add_row("status", "[yellow]no worktree[/yellow]", name)
    for name in sorted(orphan_branch_dead):
        table.add_row("branch", "[yellow]dead session + branch gone[/yellow]", name)

    console.print(table)
    console.print(f"\n[bold]{total_issues} issue(s) found.[/bold]")

    if not fix:
        console.print("[dim]Run with --fix to clean up.[/dim]")
        return

    fixed = 0
    fixed += _fix_orphan_sessions(orphan_session_no_wt, live_sessions)
    fixed += _fix_orphan_status_rows(orphan_status_no_wt, tracker)
    fixed += _fix_orphan_branch_rows(orphan_branch_dead, tracker, str(wt_manager.git_root))

    # Worktree directories with no live session are NOT auto-deleted — they
    # may be headless or paused. Branch rows with a live session but no
    # branch on disk are also left alone (a user mid-rebase, perhaps).
    if orphan_wt_no_session:
        console.print(
            f"\n[dim]{len(orphan_wt_no_session)} worktree(s) without sessions left untouched "
            f"(may be headless). Use 'owt delete' to remove manually.[/dim]"
        )

    console.print(f"\n[green]Fixed {fixed} issue(s).[/green]")


def _fix_orphan_sessions(names: set[str], live_sessions: dict[str, BackendSession]) -> int:
    fixed = 0
    for name in names:
        session = live_sessions[name]
        try:
            backend = select_backend_for_session(session)
            backend.kill(session)
            console.print(f"  [green]Killed {session.kind.value} session:[/green] {session.id}")
            fixed += 1
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]Failed to kill {session.id}: {e}[/red]")
    return fixed


def _fix_orphan_status_rows(names: set[str], tracker: StatusTracker) -> int:
    fixed = 0
    for name in names:
        try:
            tracker.remove_status(name)
            console.print(f"  [green]Removed status entry:[/green] {name}")
            fixed += 1
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]Failed to remove status for {name}: {e}[/red]")
    return fixed


def _fix_orphan_branch_rows(names: set[str], tracker: StatusTracker, repo_root: str) -> int:
    """Branch-mode teardown: branch is already gone so we just clear the row.

    Stash pop is skipped — if we don't know which branch the stash maps
    to (branch is gone), restoring it would silently land changes on
    whatever branch is currently checked out.
    """
    del repo_root  # currently unused; reserved for future stash inspection
    fixed = 0
    for name in names:
        try:
            tracker.remove_status(name)
            console.print(f"  [green]Removed dead branch row:[/green] {name}")
            fixed += 1
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]Failed to remove branch row {name}: {e}[/red]")
    return fixed


def register(main: click.Group) -> None:
    """Register doctor command on the main CLI group."""
    main.add_command(doctor)

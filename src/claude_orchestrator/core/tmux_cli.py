"""
tmux CLI subcommands for Claude Orchestrator.

This module provides the CLI interface for tmux session management.
These commands can be integrated into the main CLI as a command group.
"""

import click
from rich.console import Console
from rich.table import Table

from .tmux_manager import (
    TmuxError,
    TmuxLayout,
    TmuxManager,
    TmuxSessionConfig,
    TmuxSessionExistsError,
    TmuxSessionNotFoundError,
)

console = Console()


def get_layout_from_string(layout_str: str) -> TmuxLayout:
    """Convert layout string to TmuxLayout enum."""
    layout_map = {
        "main-vertical": TmuxLayout.MAIN_VERTICAL,
        "three-pane": TmuxLayout.THREE_PANE,
        "quad": TmuxLayout.QUAD,
        "even-horizontal": TmuxLayout.EVEN_HORIZONTAL,
        "even-vertical": TmuxLayout.EVEN_VERTICAL,
    }

    if layout_str not in layout_map:
        valid_layouts = ", ".join(layout_map.keys())
        raise click.BadParameter(
            f"Invalid layout '{layout_str}'. Valid options: {valid_layouts}"
        )

    return layout_map[layout_str]


@click.group(name="tmux")
def tmux_group():
    """Manage tmux sessions for worktrees."""
    pass


@tmux_group.command(name="create")
@click.argument("session_name")
@click.option(
    "-d", "--directory",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    default=".",
    help="Working directory for the session"
)
@click.option(
    "-l", "--layout",
    type=click.Choice(["main-vertical", "three-pane", "quad", "even-horizontal", "even-vertical"]),
    default="main-vertical",
    help="Pane layout for the session"
)
@click.option(
    "-p", "--panes",
    type=int,
    default=2,
    help="Number of panes (for layouts that support variable counts)"
)
@click.option(
    "--claude/--no-claude",
    default=True,
    help="Auto-start Claude Code in the main pane"
)
@click.option(
    "-a", "--attach",
    is_flag=True,
    help="Attach to session after creation"
)
def create_session(session_name: str, directory: str, layout: str, panes: int, claude: bool, attach: bool):
    """Create a new tmux session.

    SESSION_NAME is the name for the new tmux session.
    """
    manager = TmuxManager()

    try:
        layout_enum = get_layout_from_string(layout)

        config = TmuxSessionConfig(
            session_name=session_name,
            working_directory=directory,
            layout=layout_enum,
            pane_count=panes,
            auto_start_claude=claude
        )

        session_info = manager.create_session(config)

        console.print(f"[green]Created session:[/green] {session_info.session_name}")
        console.print(f"  Directory: {session_info.working_directory}")
        console.print(f"  Layout: {layout}")
        console.print(f"  Panes: {session_info.pane_count}")

        if claude:
            console.print("  [cyan]Claude Code started in main pane[/cyan]")

        if attach:
            console.print("\n[dim]Attaching to session...[/dim]")
            manager.attach(session_name)
        else:
            console.print(f"\n[dim]Use 'cwt tmux attach {session_name}' to attach[/dim]")

    except TmuxSessionExistsError as e:
        console.print(f"[yellow]Warning:[/yellow] {e}")
        raise SystemExit(1)
    except TmuxError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)


@tmux_group.command(name="attach")
@click.argument("session_name")
def attach_session(session_name: str):
    """Attach to an existing tmux session.

    SESSION_NAME is the name of the session to attach to.
    """
    manager = TmuxManager()

    try:
        if manager.is_inside_tmux():
            console.print("[dim]Already inside tmux, switching client...[/dim]")
            manager.switch_client(session_name)
        else:
            manager.attach(session_name)

    except TmuxSessionNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")

        sessions = manager.list_sessions()
        if sessions:
            console.print("\n[dim]Available sessions:[/dim]")
            for s in sessions:
                console.print(f"  - {s.session_name}")

        raise SystemExit(1)
    except TmuxError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)


@tmux_group.command(name="list")
@click.option(
    "-a", "--all",
    is_flag=True,
    help="Show all tmux sessions, not just worktree sessions"
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON"
)
def list_sessions(all: bool, output_json: bool):
    """List tmux sessions."""
    manager = TmuxManager()

    sessions = manager.list_sessions(filter_prefix=not all)

    if not sessions:
        if all:
            console.print("[dim]No tmux sessions found[/dim]")
        else:
            console.print("[dim]No worktree sessions found[/dim]")
            console.print("[dim]Use --all to see all tmux sessions[/dim]")
        return

    if output_json:
        import json
        data = [
            {
                "name": s.session_name,
                "id": s.session_id,
                "windows": s.window_count,
                "panes": s.pane_count,
                "attached": s.attached,
                "directory": s.working_directory
            }
            for s in sessions
        ]
        console.print(json.dumps(data, indent=2))
        return

    table = Table(title="tmux Sessions")
    table.add_column("Session", style="cyan")
    table.add_column("Windows", justify="right")
    table.add_column("Panes", justify="right")
    table.add_column("Status", style="green")
    table.add_column("Directory", style="dim")

    for session in sessions:
        status = "[green]attached[/green]" if session.attached else "[dim]detached[/dim]"
        directory = session.working_directory or "unknown"

        if len(directory) > 40:
            directory = "..." + directory[-37:]

        table.add_row(
            session.session_name,
            str(session.window_count),
            str(session.pane_count),
            status,
            directory
        )

    console.print(table)


@tmux_group.command(name="kill")
@click.argument("session_name")
@click.option(
    "-f", "--force",
    is_flag=True,
    help="Kill without confirmation"
)
def kill_session(session_name: str, force: bool):
    """Kill a tmux session.

    SESSION_NAME is the name of the session to kill.
    """
    manager = TmuxManager()

    try:
        session_info = None
        sessions = manager.list_sessions(filter_prefix=False)
        for s in sessions:
            if s.session_name == session_name:
                session_info = s
                break

        if not session_info:
            console.print(f"[red]Error:[/red] Session '{session_name}' not found")
            raise SystemExit(1)

        if session_info.attached and not force:
            console.print(f"[yellow]Warning:[/yellow] Session '{session_name}' is currently attached")
            if not click.confirm("Do you want to kill it anyway?"):
                console.print("[dim]Cancelled[/dim]")
                return

        manager.kill_session(session_name)
        console.print(f"[green]Killed session:[/green] {session_name}")

    except TmuxSessionNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)
    except TmuxError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)


@tmux_group.command(name="send")
@click.argument("session_name")
@click.argument("keys")
@click.option(
    "-p", "--pane",
    type=int,
    default=0,
    help="Target pane index"
)
@click.option(
    "-w", "--window",
    type=int,
    default=0,
    help="Target window index"
)
def send_keys(session_name: str, keys: str, pane: int, window: int):
    """Send keys to a pane in a session.

    SESSION_NAME is the target session.
    KEYS is the text/command to send.
    """
    manager = TmuxManager()

    try:
        manager.send_keys_to_pane(session_name, keys, pane_index=pane, window_index=window)
        console.print(f"[green]Sent keys to {session_name}:{window}.{pane}[/green]")

    except TmuxSessionNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)
    except TmuxError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)


def register_tmux_commands(cli: click.Group) -> None:
    """
    Register tmux commands with the main CLI.

    This function should be called from the main CLI module to add
    tmux commands as a subgroup.

    Args:
        cli: The main click Group to add commands to
    """
    cli.add_command(tmux_group)

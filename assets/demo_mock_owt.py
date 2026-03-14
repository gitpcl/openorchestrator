"""Mock owt CLI for VHS demo recording. Monochromatic Rich output."""

import sys
import time

from rich.console import Console
from rich.table import Table

console = Console()

WORKTREES = [
    {
        "name": "add-user-authentication-jwt",
        "branch": "feat/add-user-authentication-jwt",
        "status": "WORKING",
        "tool": "claude",
        "task": "Implementing JWT auth flow",
    },
    {
        "name": "write-api-documentation",
        "branch": "feat/write-api-documentation",
        "status": "WORKING",
        "tool": "claude",
        "task": "Writing endpoint docs",
    },
    {
        "name": "add-integration-tests-payments",
        "branch": "feat/add-integration-tests-payments",
        "status": "WAITING",
        "tool": "claude",
        "task": "Waiting for input",
    },
]


def mock_new(description: str, wt: dict) -> None:
    time.sleep(0.3)
    console.print(f"\u2713 Branch: [bold]{wt['branch']}[/bold]")
    time.sleep(0.2)
    console.print("\u2713 Worktree created")
    time.sleep(0.2)
    console.print("\u2713 Dependencies installed [dim](python: uv)[/dim]")
    time.sleep(0.1)
    console.print("\u2713 Environment copied [dim](.env)[/dim]")
    time.sleep(0.1)
    console.print("\u2713 CLAUDE.md linked")
    time.sleep(0.1)
    console.print(f"\u2713 AI tool: [bold]claude[/bold] [dim](tmux: owt-{wt['name']})[/dim]")
    console.print()


def mock_list() -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Branch")
    table.add_column("Status")
    table.add_column("Task")
    table.add_column("tmux")

    status_map = {
        "WORKING": "\u25cf working",
        "WAITING": "\u26a0 waiting",
        "COMPLETED": "\u2713 done",
    }

    for wt in WORKTREES:
        table.add_row(
            wt["name"],
            wt["branch"],
            status_map.get(wt["status"], wt["status"]),
            wt["task"],
            f"owt-{wt['name']}",
        )

    console.print(table)
    console.print()


def mock_help() -> None:
    console.print("[bold]Usage:[/bold] owt \\[OPTIONS] COMMAND \\[ARGS]...")
    console.print()
    console.print("  Open Orchestrator \u2014 multi-agent worktree orchestration.")
    console.print()
    console.print("  Run 'owt' with no arguments to launch the Switchboard.")
    console.print()
    console.print("[bold]Commands:[/bold]")
    cmds = [
        ("batch", "Run a batch of tasks from a TOML file."),
        ("cleanup", "Remove stale worktrees (dry-run by default)."),
        ("delete", "Delete a worktree + tmux session + status."),
        ("list", "List all worktrees with status."),
        ("merge", "Merge a worktree branch into its base and clean up."),
        ("new", "Create a worktree + tmux session + deps + AI agent."),
        ("note", "Share context across all active agent sessions."),
        ("queue", "Show optimal merge order for completed worktrees."),
        ("send", "Send a command/message to a worktree's AI agent."),
        ("ship", "Commit, merge, and clean up a worktree in one shot."),
        ("switch", "Jump to a worktree's tmux session."),
        ("sync", "Sync worktree(s) with upstream."),
        ("version", "Show version."),
        ("wait", "Wait for a worktree's agent to finish."),
    ]
    for name, desc in cmds:
        console.print(f"  [bold]{name:<10}[/bold] {desc}")
    console.print()


def mock_send(target: str, msg: str) -> None:
    time.sleep(0.2)
    console.print(f"\u2713 Sent to [bold]{target}[/bold]: {msg}")
    console.print()


def mock_send_all(msg: str) -> None:
    time.sleep(0.2)
    console.print(f"Broadcast to 3 worktree(s): {msg}")
    console.print()


def mock_switchboard() -> None:
    """Launch the mock switchboard TUI."""
    import importlib.util
    import os

    spec = importlib.util.spec_from_file_location(
        "demo_switchboard",
        os.path.join(os.path.dirname(__file__), "demo_switchboard.py"),
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.MockSwitchboard().run()


def main() -> None:
    args = sys.argv[1:]
    if not args:
        mock_switchboard()
        return
    if args[0] == "--help":
        mock_help()
    elif args[0] == "new":
        desc = " ".join(a for a in args[1:] if not a.startswith("-"))
        for wt in WORKTREES:
            if any(word.lower() in wt["name"] for word in desc.split()[:2]):
                mock_new(desc, wt)
                return
        mock_new(desc, WORKTREES[0])
    elif args[0] == "list":
        mock_list()
    elif args[0] == "send":
        if "--all" in args:
            msg = " ".join(a for a in args[1:] if a != "--all")
            mock_send_all(msg)
        else:
            target = args[1] if len(args) > 1 else ""
            msg = " ".join(args[2:])
            mock_send(target, msg)


if __name__ == "__main__":
    main()

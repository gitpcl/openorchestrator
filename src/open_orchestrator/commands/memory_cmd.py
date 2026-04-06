"""Memory commands: add, search, consolidate."""

from __future__ import annotations

import click

from open_orchestrator.commands._shared import console
from open_orchestrator.core.memory import MemoryManager
from open_orchestrator.models.memory import MemoryType, TopicFile


@click.group("memory")
def memory_group() -> None:
    """Persistent cross-worktree memory system."""


@memory_group.command("add")
@click.argument("fact")
@click.option("--name", "-n", help="Short title (auto-generated from fact if omitted).")
@click.option(
    "--type",
    "memory_type",
    type=click.Choice([t.value for t in MemoryType], case_sensitive=False),
    help="Memory type (auto-classified if omitted).",
)
def add_memory(fact: str, name: str | None, memory_type: str | None) -> None:
    """Store a fact in the memory system.

    Auto-classifies the fact type (decision, architecture, convention, reference)
    unless --type is provided.

    Examples:

        owt memory add "We use Pydantic v2 for all data models"

        owt memory add --name "API versioning" --type decision "Chose URL-based versioning over header-based"
    """
    mgr = MemoryManager()
    mgr.ensure_dirs()

    # Auto-classify if not provided
    if memory_type:
        mtype = MemoryType(memory_type)
    else:
        mtype = mgr.classify_fact(fact)

    # Auto-generate name if not provided
    if not name:
        # Use first 50 chars of fact as name
        name = fact[:50].rstrip()
        if len(fact) > 50:
            name = name.rsplit(" ", 1)[0] if " " in name else name

    filename = mgr.slugify(name)

    topic = TopicFile(
        name=name,
        description=fact[:100],
        memory_type=mtype,
        body=fact,
        filename=filename,
    )

    path = mgr.write_topic(topic)
    console.print(f"[green]Stored[/green] \\[{mtype.value}] {name}")
    console.print(f"[dim]  → {path}[/dim]")


@memory_group.command("search")
@click.argument("query")
@click.option("--no-transcripts", is_flag=True, help="Skip transcript search.")
def search_memory(query: str, no_transcripts: bool) -> None:
    """Search across memory index, topics, and transcripts.

    Examples:

        owt memory search "pydantic"

        owt memory search "auth" --no-transcripts
    """
    mgr = MemoryManager()
    results = mgr.search(query, include_transcripts=not no_transcripts)

    if not results:
        console.print(f"[dim]No results for '{query}'[/dim]")
        return

    console.print(f"[bold]{len(results)} result(s) for '{query}':[/bold]\n")

    for r in results:
        source_color = {"index": "cyan", "topic": "green", "transcript": "yellow"}.get(r.source, "white")
        console.print(f"  [{source_color}]{r.source}[/{source_color}] {r.filename}:{r.line_number}")
        console.print(f"    {r.line[:120]}")
        console.print()


@memory_group.command("consolidate")
def consolidate_memory() -> None:
    """Deduplicate, prune orphans, and index untracked topic files.

    - Removes index entries whose topic file was deleted
    - Adds topic files that aren't in the index
    - Removes duplicate index entries
    """
    mgr = MemoryManager()
    mgr.ensure_dirs()

    stats = mgr.consolidate()

    total = sum(stats.values())
    if total == 0:
        console.print("[green]Memory is clean — nothing to consolidate.[/green]")
        return

    console.print("[bold]Consolidation complete:[/bold]")
    if stats["orphaned_removed"]:
        console.print(f"  [red]Removed {stats['orphaned_removed']} orphaned index entries[/red]")
    if stats["unindexed_added"]:
        console.print(f"  [green]Added {stats['unindexed_added']} unindexed topic files[/green]")
    if stats["duplicates_removed"]:
        console.print(f"  [yellow]Removed {stats['duplicates_removed']} duplicate entries[/yellow]")


@memory_group.command("list")
def list_memory() -> None:
    """List all memory entries."""
    mgr = MemoryManager()
    entries = mgr.list_entries()

    if not entries:
        console.print("[dim]No memory entries found.[/dim]")
        return

    console.print(f"[bold]{len(entries)} memory entries:[/bold]\n")
    for entry in entries:
        type_color = {
            MemoryType.DECISION: "magenta",
            MemoryType.ARCHITECTURE: "cyan",
            MemoryType.CONVENTION: "green",
            MemoryType.REFERENCE: "yellow",
        }.get(entry.memory_type, "white")
        console.print(f"  [{type_color}]{entry.memory_type.value:13s}[/{type_color}] {entry.name}")
        console.print(f"  [dim]{' ' * 13} {entry.description}[/dim]")


def register(main: click.Group) -> None:
    """Register memory commands on the main CLI group."""
    main.add_command(memory_group)

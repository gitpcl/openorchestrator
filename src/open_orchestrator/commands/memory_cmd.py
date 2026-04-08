"""Memory commands: add, search, consolidate, mine."""

from __future__ import annotations

from pathlib import Path

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


@memory_group.command("mine")
@click.option("--worktree", "-w", default="global", help="Worktree scope label for mined facts.")
@click.option("--since", default=None, help="Only mine commits since this date (e.g. 2026-01-01).")
@click.option("--limit", default=100, show_default=True, help="Max commits to scan.")
@click.option("--no-comments", is_flag=True, help="Skip code-comment scanning (TODO/NOTE/etc).")
@click.option("--store", is_flag=True, help="Persist mined facts into the recall store.")
@click.option(
    "--path",
    "root_path",
    default=None,
    type=click.Path(exists=True, file_okay=False),
    help="Repository root to mine (defaults to cwd).",
)
def mine_memory(
    worktree: str,
    since: str | None,
    limit: int,
    no_comments: bool,
    store: bool,
    root_path: str | None,
) -> None:
    """Mine decisions from git history, progress files, and code comments.

    Reads-only by default. Pass --store to persist mined facts into the
    recall memory store.

    Examples:

        owt memory mine

        owt memory mine --worktree feature/auth --since 2026-01-01

        owt memory mine --store --no-comments
    """
    from open_orchestrator.core.memory_miner import FactMiner
    from open_orchestrator.core.memory_store import MemoryStore

    miner = FactMiner(Path(root_path) if root_path else None)
    facts = miner.mine_all(
        worktree=worktree,
        since=since,
        limit=limit,
        include_comments=not no_comments,
    )

    if not facts:
        console.print("[dim]No facts mined.[/dim]")
        return

    by_source: dict[str, int] = {}
    for fact in facts:
        prefix = fact.source.split(":", 1)[0]
        by_source[prefix] = by_source.get(prefix, 0) + 1

    console.print(f"[bold]Mined {len(facts)} fact(s):[/bold]")
    for prefix, count in sorted(by_source.items()):
        console.print(f"  [cyan]{prefix}[/cyan]: {count}")

    if store:
        memstore = MemoryStore()
        try:
            persisted = 0
            for fact in facts:
                memstore.add_fact(
                    content=fact.content,
                    kind=fact.kind,
                    category=fact.category,
                    worktree=fact.worktree,
                    source=fact.source,
                )
                persisted += 1
        finally:
            memstore.close()
        console.print(f"[green]Persisted {persisted} fact(s) into recall store.[/green]")
    else:
        console.print("[dim]Run with --store to persist facts.[/dim]")


def register(main: click.Group) -> None:
    """Register memory commands on the main CLI group."""
    main.add_command(memory_group)

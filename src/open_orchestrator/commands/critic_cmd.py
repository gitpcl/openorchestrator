"""Critic command: pre-action safety review."""

from __future__ import annotations

import click

from open_orchestrator.commands._shared import console


@click.command("critic")
@click.argument("action", type=click.Choice(["ship", "merge", "delete"], case_sensitive=False))
@click.argument("worktree_name")
def critic_command(action: str, worktree_name: str) -> None:
    """Run safety review before a destructive action.

    Checks for file overlaps, uncommitted changes, empty branches,
    and unmerged commits. Exit code 0 = safe, 1 = blocking issues.

    Examples:

        owt critic ship my-feature

        owt critic merge auth-branch

        owt critic delete old-branch
    """
    from open_orchestrator.core.critic import CriticAgent, Severity

    critic = CriticAgent()
    verdict = critic.review_action(action, worktree_name)

    # Display findings
    if not verdict.findings:
        console.print(f"[green]Safe to {action} '{worktree_name}' — no issues found.[/green]")
        return

    console.print(f"[bold]Critic review for {action} '{worktree_name}':[/bold]\n")

    for finding in verdict.findings:
        color = {
            Severity.BLOCKING: "red",
            Severity.WARNING: "yellow",
            Severity.INFO: "cyan",
        }.get(finding.severity, "white")
        icon = {
            Severity.BLOCKING: "X",
            Severity.WARNING: "!",
            Severity.INFO: "i",
        }.get(finding.severity, "?")
        console.print(f"  [{color}]{icon}[/{color}] [{color}]{finding.severity.value.upper()}[/{color}] {finding.message}")
        if finding.details:
            for line in finding.details.splitlines()[:5]:
                console.print(f"    [dim]{line}[/dim]")
        console.print()

    # Summary
    console.print(verdict.summary)

    if not verdict.is_safe:
        raise SystemExit(1)


def register(main: click.Group) -> None:
    """Register critic command on the main CLI group."""
    main.add_command(critic_command)

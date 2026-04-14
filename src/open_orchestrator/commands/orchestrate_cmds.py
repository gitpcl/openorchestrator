"""Orchestration commands: plan, batch, orchestrate."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.table import Table

from open_orchestrator.commands._shared import (
    console,
    get_worktree_manager,
    print_batch_results,
    print_batch_status,
)


def _print_orchestrator_status(state: object) -> None:
    """Print compact orchestrator task status counts.

    Accepts OrchestratorState but typed as object to avoid import at module level.
    """
    counts: dict[str, int] = {}
    for t in state.tasks:  # type: ignore[attr-defined]
        counts[t.status] = counts.get(t.status, 0) + 1
    parts = [f"{v} {k}" for k, v in sorted(counts.items())]
    console.print(f"  [dim]{' | '.join(parts)}[/dim]")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.command("plan")
@click.argument("goal", nargs=-1, required=True)
@click.option("-o", "--output", "output_path", help="Output path for plan TOML (default: plan.toml).")
@click.option("--execute", is_flag=True, help="Execute the plan immediately after generation (batch mode).")
@click.option("--start", is_flag=True, help="Start orchestrator after plan generation (feature branch mode).")
@click.option("--branch", "orch_branch", help="Feature branch name for orchestrator (used with --start).")
@click.option("--edit", is_flag=True, help="Open plan in $EDITOR before executing.")
@click.option("--auto-ship", is_flag=True, help="Auto-ship completed tasks during execution.")
@click.option("--max-concurrent", type=int, default=3, help="Max parallel tasks.")
@click.option(
    "--ai-tool",
    default=None,
    help="AI tool by registered name (auto-detected if not specified).",
)
def plan_goal(
    goal: tuple[str, ...],
    output_path: str | None,
    execute: bool,
    start: bool,
    orch_branch: str | None,
    edit: bool,
    auto_ship: bool,
    max_concurrent: int,
    ai_tool: str | None,
) -> None:
    """AI-powered task decomposition into a dependency-aware DAG.

    Decomposes a feature goal into parallel tasks with dependency ordering,
    generates a TOML batch file, and optionally executes it.

    Examples:
        owt plan Build JWT auth with refresh tokens
        owt plan "Add rate limiting" --execute
        owt plan "Add auth" --start --branch feat/auth-v2
        owt plan "Fix auth bugs" --execute --auto-ship
    """
    from open_orchestrator.core.batch import (
        load_batch_config,
        plan_tasks,
    )

    goal_text = " ".join(goal)
    wt_manager = get_worktree_manager()

    # Auto-detect AI tool if not specified
    if ai_tool is None:
        from open_orchestrator.core.agent_detector import detect_installed_agents

        installed = detect_installed_agents()
        if not installed:
            raise click.ClickException("No AI coding tools found. Install claude, opencode, or droid.")
        ai_tool = installed[0]

    # 1. Generate plan
    console.print(f"[bold blue]Planning tasks with {ai_tool}...[/bold blue]")
    try:
        plan_path = plan_tasks(
            goal=goal_text,
            repo_path=str(wt_manager.git_root),
            ai_tool=ai_tool,
            output_path=output_path,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Planning cancelled.[/yellow]")
        return
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e

    # 2. Show task summary
    config = load_batch_config(str(plan_path))
    console.print(f"\n[bold]Plan:[/bold] {goal_text}")
    console.print(f"[bold]Tasks:[/bold] {len(config.tasks)}  [bold]File:[/bold] {plan_path}\n")

    for t in config.tasks:
        deps = f" [dim]\u2190 {', '.join(t.depends_on)}[/dim]" if t.depends_on else ""
        console.print(f"  [cyan]{t.id}[/cyan]: {t.description[:70]}{deps}")

    # 3. Optionally open in editor
    if edit:
        import os
        import subprocess

        editor = os.environ.get("EDITOR", "vim")
        subprocess.run([editor, str(plan_path)], check=False)
        config = load_batch_config(str(plan_path))
        console.print(f"\n[green]Reloaded {len(config.tasks)} task(s) after edit[/green]")

    # 4. Optionally start orchestrator (--start)
    if start:
        _start_orchestrator(goal_text, plan_path, orch_branch, max_concurrent, wt_manager)
        return

    # 4b. Optionally execute (batch mode)
    if execute:
        _execute_batch(plan_path, auto_ship, max_concurrent)
    else:
        console.print(
            f"\n[dim]Plan saved to {plan_path}. Use --start to orchestrate, --execute for batch, or: owt batch {plan_path}[/dim]"
        )


def _start_orchestrator(
    goal_text: str,
    plan_path: str | Path,
    orch_branch: str | None,
    max_concurrent: int,
    wt_manager: object,
) -> None:
    """Start orchestrator from a generated plan."""
    from open_orchestrator.core.branch_namer import generate_branch_name
    from open_orchestrator.core.orchestrator import Orchestrator, TaskPhase

    feature_branch = orch_branch
    if not feature_branch:
        try:
            feature_branch = f"orchestrator/{generate_branch_name(goal_text, prefix='').lstrip('/')}"
        except ValueError:
            feature_branch = "orchestrator/plan"

    console.print("\n[bold]Starting orchestrator[/bold]")
    console.print(f"  Feature branch: {feature_branch}")
    console.print(f"  Max concurrent: {max_concurrent}")

    orch = Orchestrator.from_plan(
        plan_path=plan_path,
        goal=goal_text,
        feature_branch=feature_branch,
        repo_path=str(wt_manager.git_root),  # type: ignore[attr-defined]
        max_concurrent=max_concurrent,
    )

    try:
        final = orch.run(on_status=_print_orchestrator_status)
        shipped = sum(1 for t in final.tasks if t.status == TaskPhase.SHIPPED)
        failed = sum(1 for t in final.tasks if t.status == TaskPhase.FAILED)
        console.print(
            f"\n[bold green]Orchestration complete![/bold green] {shipped} shipped, {failed} failed \u2192 {feature_branch}"
        )
        if shipped > 0:
            console.print(f"[dim]Ready for review. Open PR: {feature_branch} \u2192 main[/dim]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Orchestrator paused. Resume with: owt orchestrate --resume[/yellow]")


def _execute_batch(plan_path: object, auto_ship: bool, max_concurrent: int) -> None:
    """Execute plan in batch mode via background tmux session."""
    import subprocess

    batch_cmd = ["owt", "batch", str(plan_path)]
    if auto_ship:
        batch_cmd.append("--auto-ship")
    batch_cmd.extend(["--max-concurrent", str(max_concurrent)])

    batch_session = "owt-batch"
    subprocess.run(["tmux", "kill-session", "-t", batch_session], capture_output=True, check=False)
    subprocess.run(["tmux", "new-session", "-d", "-s", batch_session, *batch_cmd], check=False)
    console.print(f"\n[green]Batch launched in tmux session '{batch_session}'[/green]")
    console.print("[dim]Use 'owt' for switchboard, or: tmux attach -t owt-batch[/dim]")


@click.command("batch")
@click.argument("tasks_file", type=click.Path(exists=True), required=False)
@click.option("--auto-ship", is_flag=True, help="Auto-ship completed tasks.")
@click.option("--max-concurrent", type=int, default=3, help="Max parallel tasks.")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
@click.option("--resume", is_flag=True, help="Resume from saved state.")
def batch_run(tasks_file: str | None, auto_ship: bool, max_concurrent: int, json_output: bool, resume: bool) -> None:
    """Run a batch of tasks from a TOML file.

    Karpathy-style autopilot: creates worktrees, starts agents,
    monitors progress, and optionally auto-ships completed work.

    Examples:
        owt batch tasks.toml
        owt batch tasks.toml --auto-ship
        owt batch --resume
    """
    from open_orchestrator.core.batch import BatchRunner, load_batch_config

    wt_manager = get_worktree_manager()
    repo_path = str(wt_manager.git_root)

    if resume:
        try:
            runner = BatchRunner.resume(repo_path)
        except FileNotFoundError:
            raise click.ClickException("No batch state found. Start with: owt batch <tasks.toml>")  # noqa: B904
        pending = sum(1 for r in runner.results if r.status.value == "pending")
        running = sum(1 for r in runner.results if r.status.value == "running")
        console.print(f"[bold]Resuming batch:[/bold] {pending} pending, {running} running")
    else:
        if not tasks_file:
            raise click.ClickException("Provide a tasks file, or use --resume")
        config = load_batch_config(tasks_file)
        if auto_ship:
            config.auto_ship = True
        if max_concurrent:
            config.max_concurrent = max_concurrent
        console.print(f"[bold]Batch: {len(config.tasks)} task(s), max {config.max_concurrent} concurrent[/bold]")
        runner = BatchRunner(config, repo_path)

    status_cb = None if json_output else print_batch_status

    try:
        results = runner.run(on_status=status_cb)
    except KeyboardInterrupt:
        console.print("\n[yellow]Batch interrupted. State saved. Resume with: owt batch --resume[/yellow]")
        return

    if json_output:
        output = [
            {"task": r.task.description, "worktree": r.worktree_name, "status": r.status.value, "error": r.error} for r in results
        ]
        console.print(json.dumps(output, indent=2))
    else:
        print_batch_results(results)


@click.command("orchestrate")
@click.argument("plan_file", type=click.Path(exists=True), required=False)
@click.option("--branch", "feature_branch", help="Feature branch name (required for new orchestration).")
@click.option("--resume", is_flag=True, help="Resume from saved state.")
@click.option("--stop", "stop_orch", is_flag=True, help="Graceful stop (worktrees kept).")
@click.option("--status", "show_status", is_flag=True, help="Show orchestrator progress.")
@click.option("--max-concurrent", type=int, default=3, help="Max parallel tasks.")
def orchestrate_cmd(
    plan_file: str | None,
    feature_branch: str | None,
    resume: bool,
    stop_orch: bool,
    show_status: bool,
    max_concurrent: int,
) -> None:
    """Drive a plan end-to-end with the orchestrator agent.

    Creates worktrees, coordinates agents, merges completed tasks into
    a feature branch for review, and persists state for stop/resume.

    Examples:
        owt orchestrate plan.toml --branch feat/auth-v2
        owt orchestrate --resume
        owt orchestrate --stop
        owt orchestrate --status
    """
    from open_orchestrator.core.orchestrator import Orchestrator, TaskPhase

    wt_manager = get_worktree_manager()
    repo_path = str(wt_manager.git_root)

    if show_status:
        _show_orchestrator_status(repo_path)
        return

    if stop_orch:
        try:
            orch = Orchestrator.resume(repo_path)
            orch.stop()
            console.print("[green]Orchestrator stopped.[/green] Worktrees kept. Resume with: owt orchestrate --resume")
        except FileNotFoundError:
            console.print("[yellow]No running orchestrator found.[/yellow]")
        return

    if resume:
        try:
            orch = Orchestrator.resume(repo_path)
        except FileNotFoundError:
            raise click.ClickException(  # noqa: B904
                "No orchestrator state found. Start with: owt orchestrate <plan.toml> --branch <name>"
            )
        console.print(f"[bold]Resuming orchestrator[/bold]: {orch.state.goal}")
        console.print(f"  Feature branch: {orch.state.feature_branch}")
    elif plan_file:
        if not feature_branch:
            from open_orchestrator.core.batch import load_batch_config
            from open_orchestrator.core.branch_namer import generate_branch_name

            config = load_batch_config(plan_file)
            goal = config.tasks[0].description if config.tasks else "plan"
            try:
                feature_branch = f"orchestrator/{generate_branch_name(goal, prefix='').lstrip('/')}"
            except ValueError:
                feature_branch = "orchestrator/plan"

        goal_text = feature_branch.split("/")[-1].replace("-", " ")
        orch = Orchestrator.from_plan(
            plan_path=plan_file,
            goal=goal_text,
            feature_branch=feature_branch,
            repo_path=repo_path,
            max_concurrent=max_concurrent,
        )
        console.print("[bold]Starting orchestrator[/bold]")
        console.print(f"  Plan: {plan_file}")
        console.print(f"  Feature branch: {feature_branch}")
    else:
        raise click.ClickException("Provide a plan file, or use --resume / --status / --stop")

    try:
        final = orch.run(on_status=_print_orchestrator_status)
        shipped = sum(1 for t in final.tasks if t.status == TaskPhase.SHIPPED)
        failed = sum(1 for t in final.tasks if t.status == TaskPhase.FAILED)
        console.print(
            f"\n[bold green]Orchestration complete![/bold green] {shipped} shipped, {failed} failed \u2192 {final.feature_branch}"
        )
        if shipped > 0:
            console.print(f"[dim]Ready for review. Open PR: {final.feature_branch} \u2192 main[/dim]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Orchestrator paused. Resume with: owt orchestrate --resume[/yellow]")


def _show_orchestrator_status(repo_path: str) -> None:
    """Display orchestrator task status table."""
    from open_orchestrator.core.orchestrator import Orchestrator, OrchestratorState

    state_path = Orchestrator._state_path(repo_path)
    if not state_path.exists():
        console.print("[dim]No orchestrator state found.[/dim]")
        return
    state = OrchestratorState.model_validate_json(state_path.read_text())
    table = Table(title=f"Orchestrator: {state.goal}", show_header=True, header_style="bold")
    table.add_column("Task")
    table.add_column("Status")
    table.add_column("Worktree")
    table.add_column("Branch")
    for t in state.tasks:
        icon = {
            "pending": "[dim]\u25cb[/dim]",
            "running": "[green]\u25cf[/green]",
            "completed": "[cyan]\u2713[/cyan]",
            "shipped": "[bold green]\u2713[/bold green]",
            "failed": "[red]\u2717[/red]",
        }.get(t.status, "?")
        table.add_row(t.id, f"{icon} {t.status}", t.worktree_name or "", t.branch or "")
    console.print(table)
    console.print(f"\n[dim]Feature branch: {state.feature_branch} | Updated: {state.updated_at}[/dim]")


def register(main: click.Group) -> None:
    """Register orchestration commands on the main CLI group."""
    main.add_command(plan_goal)
    main.add_command(batch_run)
    main.add_command(orchestrate_cmd)

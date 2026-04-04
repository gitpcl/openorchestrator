"""Config diagnostic commands: validate, show."""

from __future__ import annotations

import click
import toml

from open_orchestrator.commands._shared import console
from open_orchestrator.config import ConfigError, load_config


@click.group("config")
def config_group() -> None:
    """Configuration diagnostics."""


@config_group.command("validate")
@click.option("--config", "config_path", help="Path to config file to validate.")
def validate_config(config_path: str | None) -> None:
    """Validate the configuration file.

    Loads the effective config and checks for unknown keys,
    invalid values, and TOML syntax errors.

    Exit code 0 on valid config, 1 on invalid.
    """
    try:
        load_config(config_path)
        console.print("[green]Config is valid.[/green]")

        # Show which file was loaded
        from pathlib import Path

        search_paths = [
            Path(config_path) if config_path else None,
            Path.cwd() / ".worktreerc",
            Path.cwd() / ".worktreerc.toml",
            Path.home() / ".config" / "open-orchestrator" / "config.toml",
            Path.home() / ".worktreerc",
        ]
        for path in search_paths:
            if path and path.exists():
                console.print(f"[dim]Loaded from: {path}[/dim]")
                break
        else:
            console.print("[dim]Using defaults (no config file found).[/dim]")

    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)


@config_group.command("show")
@click.option("--config", "config_path", help="Path to config file.")
def show_config(config_path: str | None) -> None:
    """Show the effective configuration as TOML.

    Displays the fully resolved config, including defaults
    for any unset values.
    """
    try:
        config = load_config(config_path)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    data = config.model_dump(mode="json")
    # Render each top-level section with proper [section] headers
    for section, values in data.items():
        if isinstance(values, dict):
            if not values:
                continue
            console.print(f"[bold]\\[{section}][/bold]")
            console.print(toml.dumps(values).rstrip())
            console.print()
        else:
            console.print(f"{section} = {toml.dumps({'_': values}).split('= ', 1)[1].strip()}")


def register(main: click.Group) -> None:
    """Register config commands on the main CLI group."""
    main.add_command(config_group)

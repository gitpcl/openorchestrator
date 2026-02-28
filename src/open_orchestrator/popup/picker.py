"""Popup picker for on-demand worktree pane creation.

This script is invoked by tmux display-popup (prefix+n) inside a workspace
session. It presents a Rich-based interactive picker for selecting an AI tool,
entering a branch name, and optionally choosing a template. The result is
written as JSON to the file path passed as sys.argv[1], which the keybinding
chain then feeds to `owt pane add --from-popup`.

Designed for fast startup — no click dependency, minimal imports.
"""

import json
import sys

from rich.console import Console
from rich.prompt import Prompt

from open_orchestrator.config import AITool, get_builtin_templates

console = Console()


def main() -> None:
    """Run the popup picker and write result JSON to the output file."""
    if len(sys.argv) < 2:
        console.print("[red]Usage: owt-popup <output-json-path>[/red]")
        sys.exit(1)

    output_path = sys.argv[1]

    console.print()
    console.print("[bold cyan]  New Worktree Pane[/bold cyan]")
    console.print("[dim]─────────────────────────[/dim]")
    console.print()

    # AI tool selection
    tools = [t.value for t in AITool]
    tool_display = "  ".join(f"[bold]{i+1}[/bold]) {t}" for i, t in enumerate(tools))
    console.print(f"  AI Tool: {tool_display}")
    choice = Prompt.ask("  Select", choices=[str(i + 1) for i in range(len(tools))] + tools, default="1")

    # Accept both number and name
    if choice.isdigit():
        ai_tool = tools[int(choice) - 1]
    else:
        ai_tool = choice

    console.print()

    # Branch name
    branch = Prompt.ask("  Branch name").strip()
    if not branch:
        console.print("[red]Branch name is required.[/red]")
        sys.exit(1)

    console.print()

    # Optional template
    templates = get_builtin_templates()
    template_names = list(templates.keys())
    if template_names:
        template_display = ", ".join(template_names)
        console.print(f"  Templates: {template_display}")
        template = Prompt.ask("  Template (optional)", default="").strip()
        if template and template not in template_names:
            console.print(f"[yellow]Unknown template '{template}', skipping.[/yellow]")
            template = ""
    else:
        template = ""

    # Write result
    result = {
        "ai_tool": ai_tool,
        "branch": branch,
    }
    if template:
        result["template"] = template

    with open(output_path, "w") as f:
        json.dump(result, f)

    console.print()
    console.print(f"[green]Creating pane for [bold]{branch}[/bold] with {ai_tool}...[/green]")


if __name__ == "__main__":
    main()

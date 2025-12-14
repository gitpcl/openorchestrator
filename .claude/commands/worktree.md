---
allowed-tools: Bash
description: Manage git worktrees for parallel development with Claude Code
---

# Worktree Management

Manage git worktrees for parallel development workflows.

## Usage

- `/worktree create <branch>` - Create new worktree with tmux session
- `/worktree list` - List all worktrees
- `/worktree switch <name>` - Switch to worktree
- `/worktree delete <name>` - Delete worktree
- `/worktree cleanup` - Clean stale worktrees
- `/worktree sync` - Sync worktrees with upstream

## Current Context

Current git status:
!`git status --short 2>/dev/null || echo "Not a git repository"`

Current worktrees:
!`git worktree list 2>/dev/null || echo "No worktrees found"`

Active tmux sessions:
!`tmux list-sessions 2>/dev/null | grep owt || echo "No owt sessions"`

## Instructions

Parse the user's subcommand from $ARGUMENTS and execute the appropriate `owt` command:

1. **create**: Run `owt create <branch>` - Creates worktree with tmux session and Claude Code
2. **list**: Run `owt list --all` - Shows all worktrees with status
3. **switch**: Run `owt switch <name> --tmux` - Switches to worktree's tmux session
4. **delete**: Run `owt delete <name>` - Deletes worktree and its tmux session
5. **cleanup**: Run `owt cleanup` - Shows stale worktrees (dry-run by default)
6. **sync**: Run `owt sync --all` - Syncs all worktrees with upstream

After executing, provide a summary of the result and suggest next steps.

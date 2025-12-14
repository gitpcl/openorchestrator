---
allowed-tools: Bash
description: Create a new git worktree with full environment setup and tmux session
---

# Create Worktree

Create a new git worktree with automatic tmux session and Claude Code startup.

## Arguments

$ARGUMENTS - Branch name for the worktree (e.g., feature/add-login, bugfix/JIRA-123)

## Current Context

Current branch:
!`git branch --show-current 2>/dev/null || echo "Not on a branch"`

Recent remote branches:
!`git branch -r --list 2>/dev/null | head -10 || echo "No remote branches"`

Existing worktrees:
!`git worktree list 2>/dev/null || echo "No worktrees"`

## Instructions

1. Run: `cwt create $ARGUMENTS`
2. Report the created worktree details:
   - Path to the new worktree
   - Branch name
   - tmux session name
3. Provide instructions for:
   - Attaching to the tmux session: `cwt tmux attach cwt-<name>`
   - Switching to the worktree: `cd $(cwt switch <name>)`

## Options

You can suggest additional flags based on user needs:
- `--base main` - Create from a specific base branch
- `--no-tmux` - Skip tmux session creation
- `--no-claude` - Don't auto-start Claude Code
- `--layout quad` - Use a different tmux layout
- `--attach` - Automatically attach to the tmux session

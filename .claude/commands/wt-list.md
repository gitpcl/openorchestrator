---
allowed-tools: Bash
description: List all git worktrees and their status
---

# List Worktrees

Display all active worktrees with their branch, commit, and status information.

## Current Context

Git worktrees:
!`git worktree list 2>/dev/null || echo "No worktrees found"`

tmux sessions (owt-*):
!`tmux list-sessions 2>/dev/null | grep owt || echo "No owt sessions active"`

## Instructions

1. Run: `owt list --all`
2. Present the worktree table showing:
   - Name
   - Branch
   - Commit hash
   - Path
   - Status (main/active/detached)
3. Indicate which worktrees have active tmux sessions
4. Suggest actions like:
   - Switch to a worktree: `owt switch <name> --tmux`
   - Delete unused: `owt delete <name>`
   - Cleanup stale: `owt cleanup`

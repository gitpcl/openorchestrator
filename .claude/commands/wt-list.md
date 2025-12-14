---
allowed-tools: Bash
description: List all git worktrees and their status
---

# List Worktrees

Display all active worktrees with their branch, commit, and status information.

## Current Context

Git worktrees:
!`git worktree list 2>/dev/null || echo "No worktrees found"`

tmux sessions (cwt-*):
!`tmux list-sessions 2>/dev/null | grep cwt || echo "No cwt sessions active"`

## Instructions

1. Run: `cwt list --all`
2. Present the worktree table showing:
   - Name
   - Branch
   - Commit hash
   - Path
   - Status (main/active/detached)
3. Indicate which worktrees have active tmux sessions
4. Suggest actions like:
   - Switch to a worktree: `cwt switch <name> --tmux`
   - Delete unused: `cwt delete <name>`
   - Cleanup stale: `cwt cleanup`

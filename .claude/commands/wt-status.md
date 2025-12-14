---
allowed-tools: Bash
description: Show what each worktree's Claude is working on
---

# Worktree Claude Status

View Claude activity across all worktrees - see what each Claude session is working on, recent commands sent, and overall progress.

## Current Context

Git worktrees:
!`git worktree list 2>/dev/null || echo "No worktrees found"`

tmux sessions (owt-*):
!`tmux list-sessions 2>/dev/null | grep owt || echo "No owt sessions active"`

## Instructions

1. Run: `owt status`
2. Present the status table showing:
   - Worktree name
   - Branch
   - Activity status (working/idle/blocked/waiting/completed)
   - Current task description
   - Number of commands sent to this worktree
   - Last update time
3. Highlight any worktrees that are blocked or have errors
4. Show summary stats (how many active, idle, blocked)
5. Suggest actions like:
   - View detailed status: `owt status <worktree-name>`
   - Set task: `owt status <worktree-name> --set-task "description"`
   - Send command: `owt send <worktree-name> "your instruction"`
   - Mark completed: `owt status <worktree-name> --set-status completed`

## Usage Examples

```bash
# View all worktree statuses
owt status

# View specific worktree in detail
owt status feature/auth

# Update what a worktree is working on
owt status feature/auth --set-task "Implementing OAuth flow"

# Mark a worktree as blocked
owt status feature/api --set-status blocked --notes "Waiting for API spec"

# Output as JSON for programmatic use
owt status --json
```

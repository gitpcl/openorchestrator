---
allowed-tools: Bash
description: Clean up stale git worktrees that haven't been used recently
---

# Cleanup Stale Worktrees

Identify and remove worktrees that haven't been accessed recently.

## Current Context

All worktrees:
!`git worktree list 2>/dev/null || echo "No worktrees"`

## Instructions

1. First, run a dry-run to show what would be cleaned:
   ```
   cwt cleanup --days 14
   ```

2. Show the user the list of stale worktrees found, including:
   - Path
   - Branch name
   - Last accessed date
   - Status (uncommitted changes, unpushed commits)

3. If stale worktrees are found and user confirms cleanup:
   ```
   cwt cleanup --no-dry-run -y
   ```

4. Report cleanup results:
   - Number cleaned
   - Number skipped (protected due to uncommitted changes)
   - Any errors

## Options

- `--days 7` - Change the staleness threshold
- `--force` - Include worktrees with uncommitted changes
- `--no-dry-run` - Actually delete (default is dry-run)

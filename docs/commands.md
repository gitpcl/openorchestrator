# Commands

For an overview and quickstart, see the [README](../README.md).

## Daily use — the control plane

You don't need this page for everyday work. Run `owt` and drive the [control plane](#the-control-plane) from the keyboard:

| Key | What it does |
|-----|--------------|
| `n` | **Start work** — type a task, pick how to run it (one worktree, or a native plan-first workflow), confirm |
| `↑ ↓` / `j k` | Move between rows; the footer shows the focused row's actions |
| `a` | Attach to the focused worktree's agent session |
| `s` | Ship the focused worktree (commit + merge + delete) |
| `f` / `m` | Fix conflicts · merge |
| `q` | Quit |

The full verb list below is the same set of actions exposed for **scripting and CI** — reach for it when automating pipelines, not for day-to-day driving.

## Full command reference (scripting / CI)

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Control Plane** — prioritized lanes with verb-per-row actions |
| `owt new "task"` | `owt n` | Create worktree + session + deps + AI agent |
| `owt new "task" --workflow` | | Launch a native plan-first Claude Code workflow in the worktree |
| `owt new "task" --ai-tool <name>` | | Pick the provider (claude/pi/droid/opencode/custom) |
| `owt new "task" --headless` | | Create worktree without tmux (CI/script use) |
| `owt new "task" --herdr` | | Use the herdr multiplexer backend instead of tmux |
| `owt new "task" --tmux` | | Force tmux backend (override `[backend]` config) |
| `owt branch "task"` | | Create branch in current checkout instead of worktree |
| `owt list` | `owt ls` | List worktrees with status |
| `owt switch <name>` | `owt s` | Jump to a worktree's session |
| `owt attach <name>` | | Hand off to the worktree's session via the active backend (`--herdr` / `--tmux` to override) |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt send --all "msg"` | | Broadcast to ALL worktrees |
| `owt send --working "msg"` | | Broadcast to WORKING worktrees only |
| `owt merge <name>` | `owt m` | Two-phase merge + Conflict Guard + cleanup (`--rebase`, `--strategy`, `--leave-conflicts`) |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + session + status |
| `owt queue` | | Show optimal merge order (with overlap counts) for completed worktrees |
| `owt queue --ship` | | Ship all completed worktrees in optimal order |
| `owt wait <name>` | | Poll until agent finishes (for CI/scripts) |
| `owt note "msg"` | | Share context across all agent sessions |
| `owt sync [--all]` | | Sync with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees |
| `owt config validate` | | Validate configuration file |
| `owt config show` | | Display effective config as TOML |
| `owt db purge [--days N]` | | Delete messages older than N days (default 30) |
| `owt db vacuum` | | Optimize and compact the database |
| `owt db health [--check]` | | Database health diagnostics with CI thresholds |
| `owt doctor [--fix]` | | Diagnose and fix orphaned resources |
| `owt usage [--days N]` | | Local usage counts (cockpit launches, worktrees started) |
| `owt --theme <name>` | | Override UI theme (auto, dark, light, dark-ansi, light-ansi) |
| `owt --json <cmd>` | | Machine-readable JSON output for `list`, `queue`, `doctor`, `db health` |
| `owt version` | | Show version |

## The Control Plane

Running `owt` with no arguments launches the **control plane** — a prioritized decision surface. Three lanes render top-to-bottom in priority order; empty lanes are hidden so you always see the most important thing first.

```
  open-orchestrator · 4 rows · 14:32:08
  ▸ NEEDS YOU      (1)
  ▶ auth-jwt        merge conflict — needs manual resolution   [f] [a]
  ▸ READY TO SHIP  (2)
    fix-login       +3 commits · queued #1/2                   [s] [a]
    docs-update     +1 commits · queued #2/2 · 1 overlap       [s] [a]
  ▸ IN FLIGHT      (1)
    api-refactor    45m · opencode · Refactoring REST routes   [a]

  ↑↓ nav | n new | s ship | a attach | q quit
```

**Starting work (`n`):** press `n` from anywhere to begin a task without leaving the UI:

1. Type the task in plain English.
2. Pick how to run it — **One worktree + agent** (`owt new`) or **Native Claude workflow (plan-first)** (`owt new --workflow`).
3. Confirm the resolved `owt …` command, which then runs in the background.

This is the only thing you need to start work; the `owt new` verbs exist for scripts.

**Lanes (priority order):**
- **NEEDS YOU** — merge conflicts and BLOCKED/ERROR status
- **READY TO SHIP** — completed worktrees in optimal merge order with the `[s]hip` action and per-worktree overlap counts
- **IN FLIGHT** — WORKING agents with elapsed time + provider + last task message

**Row verbs:**

| Key | Action | Where it applies |
|-----|--------|------------------|
| `n` | new — start work (task → mode pick → confirm) | always available |
| `s` | ship (commit + merge + delete via confirm) | READY TO SHIP |
| `a` | attach (hand off via active backend) | every lane |
| `f` | fix (open conflicted files in `$EDITOR`) | NEEDS YOU |
| `m` | merge (without ship's cleanup) | READY TO SHIP |

**Context-sensitive footer:** the hotkey strip always shows `↑↓ nav`, `n new`, and `q quit`; between them it lists only the verbs that apply to the currently-focused row, so the UI teaches itself as you navigate.

**Navigation:** `↑/↓` or `j/k` for previous/next row across lanes; `q` to quit.

**Header bar:** project name, row count, and a clock.

**Architecture:** section builders are pure functions in `core/control_plane_sections.py` (fully testable without a Textual Pilot); the action dispatcher in `core/control_plane_actions.py` is a `(SectionKind, RowAction) → coroutine` table; the view in `core/control_plane_view.py` is dumb — it only knows about rows and key presses.

## Conflict Guard

`owt` watches the files each worktree has modified and warns when two worktrees touch the same files. The overlap count appears per worktree in the READY TO SHIP lane and in `owt queue` output. The detection is `MergeManager.check_file_overlaps()` — pure, AI-free, reading from the status DB. Active merge conflicts (MERGE_HEAD / rebase-in-progress) surface in NEEDS YOU.

## Slash Commands (Claude Code)

Use these slash commands inside Claude Code sessions:

- `/wt-create` — Quick worktree creation
- `/wt-list` — List all worktrees
- `/wt-status` — Check AI activity
- `/wt-cleanup` — Clean stale worktrees

## Workflow Templates

Three built-in templates for common workflows:

```bash
owt new "Add payments" --template feature   # Plan mode, TDD workflow
owt new "Fix crash" --template bugfix       # Root cause focus, minimal changes
owt new "Patch CVE" --template hotfix       # Emergency, production stability
```

## Common Patterns

### Multi-provider parallel development
```bash
owt new "Build Stripe integration"                  # default provider
owt new "Write payment tests" --ai-tool pi          # a different engine in its own worktree
owt new "Port parser to Rust" --ai-tool droid
# -> Three agents, three providers, working in parallel, visible in IN FLIGHT
# -> Conflict Guard warns if agents touch the same files; conflicts surface in NEEDS YOU
```

### Native plan-first workflow
```bash
owt new "Refactor the billing module" --workflow
# -> Launches native Claude Code in plan mode with a plan-then-execute protocol
# -> Tracked on the board as a workflow session alongside everything else
```

### Broadcasting instructions
```bash
owt send --all "Run tests and fix any failures"
owt send --working "Wrap up and commit your changes"
```

### Sharing context across agents
```bash
owt note "The users table now has a verified_at column"
owt note "API endpoints moved from /api/v1 to /api/v2"
# -> Injected into each worktree's CLAUDE.md
```

### Smart merge order
```bash
owt queue              # Show optimal merge order (with overlap counts)
owt queue --ship       # Ship all completed worktrees, smallest first
owt queue --ship --yes # No confirmation
```

### Merge strategies
```bash
owt merge auth-jwt                     # Standard merge + auto-cleanup
owt merge auth-jwt --rebase            # Rebase for linear history
owt merge auth-jwt --strategy theirs   # Bias conflict resolution (ours|theirs)
owt merge auth-jwt --leave-conflicts   # Keep merge in-progress for manual resolution
owt merge auth-jwt --keep              # Keep worktree after merging
```

### CI/CD headless mode
```bash
owt new "Run security audit" --headless
owt wait security-audit --timeout 1200
# -> Polls until agent finishes, exits 0 on success
```
Headless mode requires Claude (Droid and OpenCode lack non-interactive mode and hook integration).

### Delegating tasks
```bash
owt send auth-jwt "Now add refresh token support"
owt send api-refactor "Focus on the /users endpoint first"
```

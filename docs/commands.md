# Commands

For an overview and quickstart, see the [README](../README.md).

## Daily use — the control plane

You don't need this page for everyday work. Run `owt` and drive the [control plane](#the-control-plane) from the keyboard:

| Key | What it does |
|-----|--------------|
| `n` | **Start work** — type a task, pick how to run it (one worktree, or a multi-step plan), confirm |
| `↑ ↓` / `j k` | Move between rows; the footer shows the focused row's actions |
| `a` | Attach to the focused worktree's agent session |
| `s` | Ship the focused worktree (commit + merge + delete) |
| `r` / `f` / `m` / `x` | Review · fix conflicts · merge · dismiss |
| `q` | Quit |

The full verb list below is the same set of actions exposed for **scripting and CI** — reach for it when automating pipelines, not for day-to-day driving.

## Full command reference (scripting / CI)

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Control Plane** — prioritized sections with verb-per-row actions |
| `owt --legacy-cards` | | Launch the deprecated card-grid switchboard (one-release migration) |
| `owt new "task"` | `owt n` | Create worktree + tmux + deps + AI agent |
| `owt new "task" --headless` | | Create worktree without tmux (CI/script use) |
| `owt new "task" --herdr` | | Use the herdr multiplexer backend instead of tmux |
| `owt new "task" --tmux` | | Force tmux backend (override `[backend]` config) |
| `owt list` | `owt ls` | List worktrees with status |
| `owt switch <name>` | `owt s` | Jump to a worktree's tmux session |
| `owt attach <name>` | | Hand off to the worktree's session via the active backend (`--herdr` / `--tmux` to override) |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt send --all "msg"` | | Broadcast to ALL worktrees |
| `owt send --working "msg"` | | Broadcast to WORKING worktrees only |
| `owt merge <name>` | `owt m` | Two-phase merge + conflict guard + cleanup (`--rebase`, `--strategy`, `--leave-conflicts`) |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + tmux + status |
| `owt queue` | | Show optimal merge order for completed worktrees |
| `owt queue --ship` | | Ship all completed worktrees in optimal order |
| `owt plan "goal"` | | AI-powered task decomposition into dependency DAG |
| `owt plan "goal" --start` | | Plan + start orchestrator in one shot |
| `owt batch tasks.toml` | | Autopilot: run batch tasks from TOML (DAG-aware) |
| `owt orchestrate plan.toml` | | Orchestrate plan into feature branch with coordination |
| `owt orchestrate --resume` | | Resume orchestrator from saved state |
| `owt orchestrate --stop` | | Graceful stop (worktrees kept) |
| `owt orchestrate --status` | | Show orchestrator progress |
| `owt wait <name>` | | Poll until agent finishes (for CI/scripts) |
| `owt note "msg"` | | Share context across all agent sessions |
| `owt sync [--all]` | | Sync with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees |
| `owt config validate` | | Validate configuration file |
| `owt config show` | | Display effective config as TOML |
| `owt db purge [--days N]` | | Delete messages older than N days (default 30) |
| `owt db vacuum` | | Optimize and compact the database |
| `owt db health [--check]` | | Database health diagnostics with CI thresholds |
| `owt memory add "fact"` | | Store a fact with auto-classification |
| `owt memory search "q"` | | Search index, topics, and transcripts |
| `owt memory consolidate` | | Dedup, prune, and index untracked topics |
| `owt memory list` | | List all memory entries |
| `owt memory mine` | | Mine facts from git history, progress files, and code comments |
| `owt swarm start "goal" -w wt` | | Launch coordinator + specialist workers in a worktree |
| `owt swarm list` | | List all active swarms |
| `owt swarm send <id> "msg"` | | Broadcast a message to all workers in a swarm |
| `owt swarm stop <id>` | | Stop a swarm and kill its worker panes |
| `owt critic ship\|merge\|delete <name>` | | Pre-action safety review (overlaps, uncommitted, empty branch) |
| `owt dream enable` | | Start the background dream daemon |
| `owt dream disable` | | Stop the dream daemon |
| `owt dream status` | | Show daemon state and last heartbeat |
| `owt dream consolidate` | | Run consolidation immediately |
| `owt dream reports` | | List recent dream reports |
| `owt doctor [--fix]` | | Diagnose and fix orphaned resources |
| `owt --theme <name>` | | Override UI theme (auto, dark, light, dark-ansi, light-ansi) |
| `owt --json <cmd>` | | Machine-readable JSON output for `list`, `queue`, `doctor`, `db health` |
| `owt version` | | Show version |

## The Control Plane

Running `owt` with no arguments launches the **control plane** — a prioritized decision surface. Four sections render top-to-bottom in priority order; empty sections are hidden so you always see the most important thing first.

```
  open-orchestrator · 5 rows · 14:32:08
  ▸ NEEDS YOU      (1)
  ▶ auth-jwt        merge conflict — needs manual resolution   [f] [a]
  ▸ READY TO SHIP  (2)
    fix-login       +3 commits · queued #1/2                   [s] [r] [a]
    docs-update     +1 commits · queued #2/2                   [s] [r] [a]
  ▸ IN FLIGHT      (1)
    api-refactor    45m · opencode · Refactoring REST routes   [a] [r]
  ▸ BACKGROUND     (1)
    14:20 dream     consolidated · memory=3 stale=0            [x]

  ↑↓ nav | n new | s ship | r review | a attach | q quit
```

**Starting work (`n`):** press `n` from anywhere to begin a task without leaving the UI:

1. Type the task in plain English.
2. Pick how to run it — **One worktree + agent** (`owt new`) or **Multi-step plan** that decomposes into a DAG and runs it (`owt plan --start`).
3. Confirm the resolved `owt …` command, which then runs in the background.

This is the only thing you need to start work; the `owt new` / `owt plan` verbs exist for scripts.

**Sections (priority order):**
- **NEEDS YOU** — merge conflicts, critic-blocking verdicts, BLOCKED/ERROR status
- **READY TO SHIP** — completed worktrees in optimal merge order with the `[s]hip` action
- **IN FLIGHT** — WORKING agents with elapsed time + last task message
- **BACKGROUND** — recent dream / memory / critic events (≤10, newest first); `[x]` to dismiss

**Row verbs:**

| Key | Action | Where it applies |
|-----|--------|------------------|
| `n` | new — start work (task → mode pick → confirm) | always available |
| `s` | ship (commit + merge + delete via confirm) | READY TO SHIP |
| `r` | review (inline critic verdict panel) | NEEDS YOU, READY TO SHIP, IN FLIGHT |
| `a` | attach (hand off via active backend) | every section except BACKGROUND |
| `f` | fix (open conflicted files in `$EDITOR`) | NEEDS YOU |
| `m` | merge (without ship's cleanup) | READY TO SHIP |
| `x` | dismiss | BACKGROUND |

**Context-sensitive footer:** the hotkey strip always shows `↑↓ nav`, `n new`, and `q quit`; between them it lists only the verbs that apply to the currently-focused row, so the UI teaches itself as you navigate.

**Navigation:** `↑/↓` or `j/k` for previous/next row across sections; `q` to quit; `Esc` closes the inline review panel.

**Header bar:** when `owt orchestrate` is active, the header shows DAG progress (`X/Y done · Z running`). Otherwise it shows the project name, row count, and a clock.

**Architecture:** section builders are pure functions in `core/control_plane_sections.py` (fully testable without a Textual Pilot); the action dispatcher in `core/control_plane_actions.py` is a `(SectionKind, RowAction) → coroutine` table; the view in `core/control_plane_view.py` is dumb — it only knows about rows and key presses.

## Legacy Switchboard (`--legacy-cards`)

`owt --legacy-cards` launches the original card-grid switchboard. It will be removed in the next minor release; a deprecation banner is printed on every legacy invocation.

```
  SWITCHBOARD (legacy) · 4  ●3  ○1

  ┌─ auth-jwt ──────────────┐   ┌─ fix-login ──────────────┐
  │ ● WORKING        12m    │   │ ○ IDLE              3h    │
  │ feat/auth-jwt           │   │ fix/login-redirect        │
  │ claude        +142 -37  │   │ claude                    │
  │ Implementing JWT auth   │   │ —                         │
  └─────────────────────────┘   └───────────────────────────┘

  legacy · ↑↓←→ nav · Enter patch · n new · S ship · q quit
```

Keys: arrows nav, `Enter` patch into tmux, `s` send, `a` broadcast, `n` new, `S` ship, `m` merge, `d` delete, `q` quit. Detail/info modals were removed in Sprint 024 — `f` (overlap) and `i` (info) now surface as toasts.

**Global tmux keybindings (work from any agent session):**
- **Alt+s** — switch back to the switchboard
- **Alt+m** — merge current worktree
- **Alt+d** — delete current worktree
- **Alt+c** — create a new worktree (opens popup)

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

### Parallel Feature Development
```bash
owt new "Build Stripe integration"
owt new "Write payment tests"
owt new "Add payment docs"
# -> Three agents working in parallel, visible in the control plane's IN FLIGHT section
# -> Conflict Guard warns if agents touch the same files; conflicts surface in NEEDS YOU
```

### AI-Powered Planning (DAG Execution)
```bash
owt plan "Build JWT auth with refresh tokens and admin dashboard"
# -> AI decomposes into dependency-aware tasks, saves plan.toml

owt plan "Add auth" --start --branch feat/auth-v2
# -> Generate plan + orchestrate into feature branch

owt plan "Add rate limiting" --execute
# -> Generate plan + run in batch mode (ships to main)

owt plan "Fix auth bugs" --execute --auto-ship
# -> Generate plan + run + auto-merge completed tasks
```

Tasks with dependencies run in topological order. Independent tasks run in parallel. Parent task context (git log summaries) is auto-injected into child worktrees' CLAUDE.md.

### Orchestrator (Feature Branch Mode)
```bash
# Plan + start orchestration in one shot
owt plan "Add JWT auth" --start --branch feat/auth-v2

# Or plan first, orchestrate later
owt plan "Add JWT auth"                              # generates plan.toml
owt orchestrate plan.toml --branch feat/auth-v2      # starts orchestration

# Control the orchestrator
owt orchestrate --resume                              # resume from saved state
owt orchestrate --stop                                # graceful stop
owt orchestrate --status                              # show progress

# User jumps in to help (orchestrator pauses that worktree)
owt switch auth-models
# -> orchestrator detects user, skips auto-actions on auth-models
# -> user leaves → orchestrator resumes coordination

# When all tasks complete:
# "All 5 tasks merged into feat/auth-v2. Ready for review."
# User opens PR: feat/auth-v2 → main
```

The orchestrator merges completed tasks into a **feature branch** (not main), persists state for stop/resume, detects user presence to pause auto-actions, and coordinates agents when file overlaps are detected (Agno or template fallback). Orchestrated and batch agents start as live provider sessions, receive the structured session-init prompt through the active multiplexer backend, and remain patchable via `owt attach` (which routes through tmux or herdr). The shared runtime evaluator watches both hook updates and pane state to detect waiting, blocked, exited, and silent-failure cases. Safety nets: auto-commits uncommitted work, optional quality gate, empty-branch guard, retry with failure context, and per-task timeouts (30 min default).

### Overnight Autopilot (Batch Mode)
```toml
# tasks.toml — now supports dependency DAGs
[batch]
max_concurrent = 3
auto_ship = true
min_agent_runtime = 60

[[tasks]]
id = "models"
description = "Create User and Token models"
depends_on = []

[[tasks]]
id = "auth-api"
description = "Build auth endpoints"
depends_on = ["models"]

[[tasks]]
id = "auth-tests"
description = "Write auth integration tests"
depends_on = ["auth-api"]
```
```bash
owt batch tasks.toml --auto-ship
# -> Respects dependency order, injects parent context
# -> Auto-ships completed work, starts next task
```

### Broadcasting Instructions
```bash
owt send --all "Run tests and fix any failures"
owt send --working "Wrap up and commit your changes"
# In the legacy switchboard (--legacy-cards), press 'a' to broadcast
```

### Sharing Context Across Agents
```bash
owt note "The users table now has a verified_at column"
owt note "API endpoints moved from /api/v1 to /api/v2"
# -> Injected into each worktree's CLAUDE.md
```

### Smart Merge Order
```bash
owt queue              # Show optimal merge order
owt queue --ship       # Ship all completed worktrees, smallest first
owt queue --ship --yes # No confirmation
```

### Merge Strategies
```bash
owt merge auth-jwt                     # Standard merge + auto-cleanup
owt merge auth-jwt --rebase            # Rebase for linear history
owt merge auth-jwt --strategy theirs   # Auto-resolve conflicts (ours|theirs)
owt merge auth-jwt --leave-conflicts   # Keep merge in-progress for manual resolution
owt merge auth-jwt --keep              # Keep worktree after merging
```

### CI/CD Headless Mode
```bash
owt new "Run security audit" --headless
owt wait security-audit --timeout 1200
# -> Polls until agent finishes, exits 0 on success
```
Headless mode requires Claude (Droid and OpenCode lack non-interactive mode and hook integration).

### Bug Investigation + Fix
```bash
owt new "Profile memory usage in user service" --plan-mode
# -> Agent investigates in plan mode (read-only)
# Later: owt merge memory-profile
```

### Delegating Tasks
```bash
owt send auth-jwt "Now add refresh token support"
owt send api-refactor "Focus on the /users endpoint first"
```

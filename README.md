# Open Orchestrator

[![CI](https://github.com/gitpcl/openorchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/gitpcl/openorchestrator/actions/workflows/ci.yml) [![License](https://img.shields.io/github/license/gitpcl/openorchestrator)](LICENSE)

A lean Git Worktree + AI agent orchestration tool for parallel development workflows. Coordinate multiple AI coding sessions across isolated branches with a Textual-based **switchboard UI**. Supports Claude Code, OpenCode, and Droid.

## Overview

Open Orchestrator enables developers to work on multiple tasks simultaneously by creating isolated worktrees, each with its own AI coding session and tmux session. Start with `owt new "task description"` — it auto-generates a branch name, creates the worktree, installs dependencies, copies `.env`, and starts the AI tool. Run `owt` to launch the **switchboard** — a card grid showing all active agents at a glance.

![Open Orchestrator demo](assets/demo.gif)

> **Agent Teams vs Open Orchestrator:** [Claude Code's Agent Teams](https://code.claude.com/docs/en/agent-teams) coordinate multiple AI agents within the *same codebase*. Open Orchestrator manages multiple *isolated worktrees* (different branches, different directories, independent environments). They're complementary — use Agent Teams for intra-branch collaboration, Open Orchestrator for cross-branch orchestration.

## Features

- **16 commands** — focused CLI surface, no bloat
- **Switchboard UI** — Textual-based card grid with status lights, diff stats, file overlap warnings, and detail panels
- **Conflict Guard** — real-time file overlap detection between parallel agents; warns before merge when two branches touch the same files
- **AI-Powered Planning** — `owt plan "Build auth system"` decomposes a goal into a dependency-aware DAG, spawns agents in parallel, auto-injects parent context into child tasks
- **Autopilot Loops** — `owt batch tasks.toml` runs Karpathy-style autonomous loops with DAG-aware scheduling
- **Agent Broadcast** — `owt send --all "Run tests"` fans out instructions to all active agents
- **Merge Queue** — `owt queue` shows optimal merge order; `owt queue --ship` ships all completed work intelligently
- **Context Bridge** — `owt note "msg"` shares context across all agent sessions via CLAUDE.md injection
- **Headless Mode** — `owt new "task" --headless` for CI/CD; `owt wait` polls until agent finishes
- **One-command setup** — `owt new "task"` does everything: branch → worktree → deps → .env → tmux → AI tool
- **Ship in one shot** — `owt ship` auto-commits, merges to main, and tears down worktree + session
- **Two-phase merge** — `owt merge` catches conflicts early with file overlap warnings, then auto-cleans
- **Full teardown** — `owt delete` kills tmux session + removes worktree + cleans status
- **Live status detection** — switchboard detects when agents are waiting for input or blocked
- **AI tool auto-detection** — detects Claude, OpenCode, Droid with picker when multiple found
- **Project detection** — auto-detects Python, Node.js, Rust, Go, PHP and installs deps
- **7 dependencies** — click, pydantic, rich, textual, toml, gitpython, libtmux

## Installation

### Requirements

- Python 3.10+
- Git
- tmux
- An AI coding tool (Claude Code, OpenCode, or Droid)

### Install from PyPI

```bash
pip install open-orchestrator
```

### Install from source

```bash
git clone https://github.com/gitpcl/openorchestrator.git
cd openorchestrator
uv pip install -e .
```

## Quick Start

```bash
# Launch the switchboard (persistent tmux session)
owt

# Create a worktree with AI agent (one command does everything)
owt new "Add user authentication with JWT"

# From inside an agent session, press Alt+s to return to the switchboard
# Or use CLI to interact:
owt send auth-jwt "Fix the failing tests"
owt switch auth-jwt    # Jump to that session

# Ship when done (commit + merge + delete in one shot)
owt ship auth-jwt
# Or press S from the switchboard
```

## Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Switchboard** — card grid with status lights |
| `owt new "task"` | `owt n` | Create worktree + tmux + deps + AI agent |
| `owt new "task" --headless` | | Create worktree without tmux (CI/script use) |
| `owt list` | `owt ls` | List worktrees with status |
| `owt switch <name>` | `owt s` | Jump to a worktree's tmux session |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt send --all "msg"` | | Broadcast to ALL worktrees |
| `owt send --working "msg"` | | Broadcast to WORKING worktrees only |
| `owt merge <name>` | `owt m` | Two-phase merge + conflict guard + cleanup |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + tmux + status |
| `owt queue` | | Show optimal merge order for completed worktrees |
| `owt queue --ship` | | Ship all completed worktrees in optimal order |
| `owt plan "goal"` | | AI-powered task decomposition into dependency DAG |
| `owt batch tasks.toml` | | Autopilot: run batch tasks from TOML (DAG-aware) |
| `owt wait <name>` | | Poll until agent finishes (for CI/scripts) |
| `owt note "msg"` | | Share context across all agent sessions |
| `owt sync [--all]` | | Sync with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees |
| `owt version` | | Show version |

## The Switchboard

Run `owt` with no arguments to launch the switchboard — your command center. It runs in a persistent tmux session (`owt-switchboard`), so it stays alive when you patch into an agent session.

```
  SWITCHBOARD                          4 lines  ●3 active  ⚠1 waiting  !1 overlap

  ┌─ auth-jwt ──────────────┐   ┌─ fix-login ──────────────┐
  │ ● WORKING        12m    │   │ ○ IDLE              3h    │
  │ feat/auth-jwt           │   │ fix/login-redirect        │
  │ claude        +142 -37  │   │ claude                    │
  │ Implementing JWT auth   │   │ —                         │
  └─────────────────────────┘   └───────────────────────────┘

  ┌─ api-refactor ──────────┐   ┌─ db-migration ────────────┐
  │ ● WORKING        45m    │   │ ⚠ BLOCKED           5m    │
  │ refactor/api-v2         │   │ feat/db-migration         │
  │ opencode       +89 -12  │   │ claude          +23 -5    │
  │ [! 2 overlap]           │   │ Waiting for input         │
  └─────────────────────────┘   └───────────────────────────┘

  [arrows] nav [Enter] patch [s] send [a] all [n] new [S] ship [f] files [i] info [q] quit
```

**Status lights:** ● working, ○ idle, ⚠ blocked, ✓ done

**Switchboard keys:**
- **Arrow keys** — navigate between cards
- **Enter** — patch into that agent's tmux session (switchboard stays alive)
- **s** — send a message to the selected agent
- **a** — broadcast a message to ALL agents
- **n** — create a new worktree + agent
- **S** — ship the selected worktree (commit + merge + delete)
- **d** — delete the selected worktree (with confirmation)
- **m** — merge the selected worktree
- **f** — show file overlap detail for the selected card
- **i** — show detail panel (commits, diff stats, overlaps)
- **q** — quit back to terminal

**Global tmux keybindings (work from any agent session):**
- **Alt+s** — switch back to the switchboard
- **Alt+m** — merge current worktree
- **Alt+d** — delete current worktree
- **Alt+c** — create a new worktree (opens popup)

**Navigation flow:**

```
owt → switchboard → Enter → agent session → Alt+s → switchboard → q → terminal
```

## Workflow Templates

Three built-in templates for common workflows:

```bash
owt new "Add payments" --template feature   # Plan mode, TDD workflow
owt new "Fix crash" --template bugfix       # Root cause focus, minimal changes
owt new "Patch CVE" --template hotfix       # Emergency, production stability
```

## Configuration

Config files are loaded in priority order:
1. `.worktreerc` in current directory
2. `.worktreerc.toml`
3. `~/.config/open-orchestrator/config.toml`
4. `~/.worktreerc`

```toml
[worktree]
base_directory = "../"
auto_cleanup_days = 14

[tmux]
auto_start_ai = true
ai_tool = "claude"        # claude, opencode, droid
mouse_mode = true

[environment]
auto_install_deps = true
copy_env_file = true
```

## AI Tool Support

Open Orchestrator auto-detects installed AI tools and offers a picker when multiple are found:

| Tool | Binary | Notes |
|------|--------|-------|
| Claude Code | `claude` | Default, `--dangerously-skip-permissions` by default |
| OpenCode | `opencode` | Go-based |
| Droid | `droid` | `--skip-permissions-unsafe` by default |

```bash
owt new "task" --ai-tool claude --plan-mode
owt new "task" --ai-tool opencode
owt new "task" --ai-tool droid
```

## Project Detection

Automatically detects project type and installs dependencies:

| Type | Detection | Package Manager |
|------|-----------|----------------|
| Python | `pyproject.toml`, `uv.lock`, `requirements.txt` | uv > poetry > pipenv > pip |
| Node.js | `package.json`, `bun.lockb`, `pnpm-lock.yaml` | bun > pnpm > yarn > npm |
| Rust | `Cargo.toml` | cargo |
| Go | `go.mod` | go |
| PHP | `composer.json` | composer |

## Common Patterns

### Parallel Feature Development
```bash
owt new "Build Stripe integration"
owt new "Write payment tests"
owt new "Add payment docs"
# -> Three agents working in parallel, visible in switchboard
# -> Conflict Guard warns if agents touch the same files
```

### AI-Powered Planning (DAG Execution)
```bash
owt plan "Build JWT auth with refresh tokens and admin dashboard"
# -> AI decomposes into dependency-aware tasks, saves plan.toml

owt plan "Add rate limiting" --execute
# -> Generate plan + run immediately in background

owt plan "Refactor DB layer" --edit --execute
# -> Generate plan + edit in $EDITOR + run

owt plan "Fix auth bugs" --execute --auto-ship
# -> Generate plan + run + auto-merge completed tasks
```

Tasks with dependencies run in topological order. Independent tasks run in parallel. Parent task context (git log summaries) is auto-injected into child worktrees' CLAUDE.md.

### Overnight Autopilot (Batch Mode)
```toml
# tasks.toml — now supports dependency DAGs
[batch]
max_concurrent = 3
auto_ship = true

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
# Or press 'a' in the switchboard to broadcast
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

### CI/CD Headless Mode
```bash
owt new "Run security audit" --headless
owt wait security-audit --timeout 1200
# -> Polls until agent finishes, exits 0 on success
```

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

## Development

```bash
uv pip install -e .
uv run pytest
uv run ruff check src/
uv run mypy src/
```

## Claude Code Integration

Use these slash commands in Claude Code sessions:

- `/wt-create` — Quick worktree creation
- `/wt-list` — List all worktrees
- `/wt-status` — Check AI activity
- `/wt-cleanup` — Clean stale worktrees

## Architecture

```
src/open_orchestrator/         (~7,100 LOC)
├── cli.py                     # 16 CLI commands (click)
├── config.py                  # Hierarchical config (TOML)
├── core/
│   ├── switchboard.py         # Textual card grid UI (async polling, modal screens, broadcast)
│   ├── worktree.py            # Git worktree CRUD
│   ├── tmux_manager.py        # tmux session management
│   ├── merge.py               # Two-phase merge + merge queue + conflict guard
│   ├── batch.py               # Autopilot loop + DAG scheduler + AI planner
│   ├── environment.py         # Deps, .env, CLAUDE.md, shared notes injection
│   ├── status.py              # AI activity tracking (SQLite + WAL)
│   ├── hooks.py               # AI tool hook installer (status push)
│   ├── cleanup.py             # Stale worktree removal
│   ├── sync.py                # Upstream sync
│   ├── branch_namer.py        # Task → branch name
│   ├── project_detector.py    # Auto-detect project type
│   ├── pane_actions.py        # Create/remove orchestration
│   └── agent_detector.py      # Detect installed AI tools
├── models/                    # Pydantic data models
├── popup/                     # tmux popup picker
├── skills/                    # Claude Code skill definition
└── utils/                     # Safe file I/O
```

## License

MIT

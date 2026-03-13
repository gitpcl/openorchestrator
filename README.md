# Open Orchestrator

A lean Git Worktree + AI agent orchestration tool for parallel development workflows. Coordinate multiple AI coding sessions across isolated branches with a curses-based **switchboard UI**. Supports Claude Code, OpenCode, and Droid.

## Overview

Open Orchestrator enables developers to work on multiple tasks simultaneously by creating isolated worktrees, each with its own AI coding session and tmux session. Start with `owt new "task description"` — it auto-generates a branch name, creates the worktree, installs dependencies, copies `.env`, and starts the AI tool. Run `owt` to launch the **switchboard** — a card grid showing all active agents at a glance.

> **Agent Teams vs Open Orchestrator:** [Claude Code's Agent Teams](https://code.claude.com/docs/en/agent-teams) coordinate multiple AI agents within the *same codebase*. Open Orchestrator manages multiple *isolated worktrees* (different branches, different directories, independent environments). They're complementary — use Agent Teams for intra-branch collaboration, Open Orchestrator for cross-branch orchestration.

## Features

- **10 commands** — focused CLI surface, no bloat
- **Switchboard UI** — curses-based card grid with status lights, instant navigation
- **One-command setup** — `owt new "task"` does everything: branch → worktree → deps → .env → tmux → AI tool
- **Two-phase merge** — `owt merge` catches conflicts early, then auto-cleans worktree + session
- **Full teardown** — `owt delete` kills tmux session + removes worktree + cleans status
- **AI tool auto-detection** — detects Claude, OpenCode, Droid with picker when multiple found
- **Project detection** — auto-detects Python, Node.js, Rust, Go, PHP and installs deps
- **Environment setup** — copies `.env` files and `CLAUDE.md` with path adjustments
- **6 dependencies** — click, pydantic, rich, toml, gitpython, libtmux

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
# Create a worktree with AI agent (one command does everything)
owt new "Add user authentication with JWT"

# Launch the switchboard to see all active agents
owt

# Jump to an agent's session
owt switch auth-jwt

# Send a message to an agent
owt send auth-jwt "Fix the failing tests"

# Merge and clean up when done
owt merge auth-jwt
```

## Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Switchboard** — card grid with status lights |
| `owt new "task"` | `owt n` | Create worktree + tmux + deps + AI agent |
| `owt list` | `owt ls` | List worktrees with status |
| `owt switch <name>` | `owt s` | Jump to a worktree's tmux session |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt merge <name>` | `owt m` | Two-phase merge + cleanup |
| `owt delete <name>` | `owt rm` | Delete worktree + tmux + status |
| `owt sync [--all]` | | Sync with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees |
| `owt version` | | Show version |

## The Switchboard

Run `owt` with no arguments to launch the switchboard — your command center:

```
  SWITCHBOARD                                    4 lines  ●3 active ○1

  ┌─ auth-jwt ──────────────┐   ┌─ fix-login ──────────────┐
  │ ● WORKING        12m    │   │ ○ IDLE              3h    │
  │ feat/auth-jwt           │   │ fix/login-redirect        │
  │ claude                  │   │ claude                    │
  │ Implementing JWT auth   │   │ —                         │
  └─────────────────────────┘   └───────────────────────────┘

  ┌─ api-refactor ──────────┐   ┌─ db-migration ────────────┐
  │ ● WORKING        45m    │   │ ⚠ BLOCKED           5m    │
  │ refactor/api-v2         │   │ feat/db-migration         │
  │ opencode                │   │ claude                    │
  │ Refactoring endpoints   │   │ Waiting for input         │
  └─────────────────────────┘   └───────────────────────────┘

  [↑↓←→] navigate  [Enter] patch in  [s] send  [n] new  [d] drop  [m] merge  [q] quit
```

**Status lights:** ● working, ○ idle, ⚠ blocked, ✓ done

**Keys:**
- **Arrow keys** — navigate between cards
- **Enter** — patch into that agent's tmux session
- **s** — send a message to the selected agent
- **n** — create a new worktree + agent
- **d** — delete the selected worktree (with confirmation)
- **m** — merge the selected worktree
- **q** — quit

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
| Claude Code | `claude` | Default, supports plan mode |
| OpenCode | `opencode` | Go-based |
| Droid | `droid` | Supports autonomy levels |

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
src/open_orchestrator/         (~5,600 LOC)
├── cli.py                     # 10 CLI commands (click)
├── config.py                  # Hierarchical config (TOML)
├── core/
│   ├── switchboard.py         # Curses-based card grid UI
│   ├── worktree.py            # Git worktree CRUD
│   ├── tmux_manager.py        # tmux session management
│   ├── merge.py               # Two-phase merge logic
│   ├── environment.py         # Deps, .env, CLAUDE.md setup
│   ├── status.py              # AI activity tracking
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

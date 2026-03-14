---
name: open-orchestrator
description: "Git worktree + AI agent orchestration with Textual switchboard UI. Use when: (1) Creating isolated dev environments from task descriptions (owt new), (2) Viewing all agent worktrees in a switchboard card grid (owt), (3) Jumping between agent sessions (owt switch), (4) Sending messages to agents (owt send), (5) Broadcasting to all agents (owt send --all), (6) Merging worktree branches with conflict guard (owt merge), (7) Shipping worktrees in one shot (owt ship), (8) Running batch autopilot tasks (owt batch), (9) Viewing optimal merge order (owt queue), (10) Sharing context across agents (owt note), (11) Headless CI/CD mode (owt new --headless, owt wait), (12) Orchestrating AI tools across branches (auto-detects claude, opencode, droid). Triggers: worktree, parallel development, multi-branch, AI orchestration, switchboard, owt commands, owt new, owt merge, owt ship, owt delete, owt switch, owt send, owt batch, owt queue, owt note, owt wait, auto-detect agents, conflict guard, autopilot."
---

# Open Orchestrator - Git Worktree + AI Orchestration

Open Orchestrator (`owt`) enables developers to manage parallel development workflows with isolated git worktrees and a **Textual-based switchboard UI**. The simplest way to start: `owt new "add user authentication"` — it auto-generates a branch name, creates the worktree, installs deps, and starts the AI tool in a tmux session. Run `owt` with no arguments to launch the switchboard — a card grid showing all active agents with status lights, diff stats, and file overlap warnings.

## Commands (15 total)

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Switchboard** — card grid with status lights, navigate + act |
| `owt new "task"` | `owt n` | Create worktree + tmux session + deps + AI agent. One command. |
| `owt new "task" --headless` | | Create worktree without tmux (CI/script use) |
| `owt list` | `owt ls` | Quick text list of worktrees (non-interactive, for scripts/pipes) |
| `owt switch <name>` | `owt s` | Jump to a worktree's tmux session |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt send --all "msg"` | | Broadcast to ALL worktrees |
| `owt send --working "msg"` | | Broadcast to WORKING worktrees only |
| `owt merge <name>` | `owt m` | Two-phase merge + conflict guard + auto-cleanup |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + tmux session + status |
| `owt queue` | | Show optimal merge order for completed worktrees |
| `owt queue --ship` | | Ship all completed worktrees in optimal order |
| `owt batch tasks.toml` | | Autopilot: run batch tasks from TOML file |
| `owt wait <name>` | | Poll until agent finishes (for CI/scripts) |
| `owt note "msg"` | | Share context across all agent sessions |
| `owt sync [--all]` | | Sync worktree(s) with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees (dry-run by default) |
| `owt version` | | Show version |

## The Switchboard

Run `owt` to launch the switchboard — your command center for multi-agent orchestration:

```
  SWITCHBOARD                          4 lines  *3 active  !1 overlap

  +- auth-jwt --------------+   +- fix-login --------------+
  | * WORKING        12m    |   | o IDLE              3h    |
  | feat/auth-jwt           |   | fix/login-redirect        |
  | claude        +142 -37  |   | claude                    |
  | Implementing JWT auth   |   | -                         |
  +-------------------------+   +---------------------------+

  +- api-refactor ----------+   +- db-migration ------------+
  | * WORKING        45m    |   | ! BLOCKED           5m    |
  | refactor/api-v2         |   | feat/db-migration         |
  | opencode       +89 -12  |   | claude          +23 -5    |
  | [! 2 overlap]           |   | Waiting for input         |
  +-------------------------+   +---------------------------+

  [arrows] nav [Enter] patch [s] send [a] all [n] new [S] ship [f] files [i] info [q] quit
```

**Switchboard keys:**
- Arrow keys: navigate between cards
- `Enter`: patch into that agent's tmux session (switchboard stays alive)
- `s`: send a message to the selected agent
- `a`: broadcast a message to ALL agents
- `n`: create a new worktree + agent
- `S`: ship the selected worktree (commit + merge + delete)
- `d`: delete the selected worktree
- `m`: merge the selected worktree
- `f`: show file overlap detail for the selected card
- `i`: show detail panel (commits, diff stats, overlaps)
- `q`: quit back to terminal

**Global tmux keybindings (work from any agent session):**
- `Alt+s`: switch back to the switchboard
- `Alt+m`: merge current worktree
- `Alt+d`: delete current worktree
- `Alt+c`: create a new worktree (opens popup)

**Navigation flow:** `owt` -> switchboard -> `Enter` -> agent -> `Alt+s` -> switchboard -> `q` -> terminal

## Core Workflow

### 1. Create a Worktree
```bash
owt new "Add user authentication with JWT"
# -> Generates branch: feat/add-user-auth-jwt
# -> Creates worktree, installs deps, copies .env
# -> Creates tmux session with Claude running
# -> Sends task description as initial prompt

owt new --branch feat/my-branch    # Explicit branch name
owt new "Fix login" --plan-mode    # Start Claude in plan mode
owt new "Quick fix" --template bugfix  # Use workflow template
owt new "Audit" --headless         # No tmux (CI/scripts)
```

### 2. Monitor via Switchboard
```bash
owt           # Launch switchboard in persistent tmux session
owt list      # Quick text table for scripts/pipes
owt wait auth-jwt --timeout 600    # Poll until done (CI/scripts)
```

### 3. Interact with Agents
```bash
owt send auth-jwt "Fix the failing tests"
owt send --all "Run tests"          # Broadcast to all
owt send --working "Wrap up"        # Broadcast to working only
owt note "Users table has verified_at column"  # Share context
owt switch auth-jwt                 # Jump to that tmux session
```

### 4. Complete Work
```bash
owt queue              # Show optimal merge order
owt queue --ship       # Ship all completed, smallest first
owt ship auth-jwt      # Commit + merge + delete in one shot
owt merge auth-jwt     # Two-phase merge + conflict guard + cleanup
owt delete fix-login   # Delete worktree + session + status
owt cleanup --force    # Delete stale worktrees
```

### 5. Batch Autopilot
```bash
owt batch tasks.toml               # Run batch from TOML
owt batch tasks.toml --auto-ship   # Auto-ship completed work
```

## Templates

Three built-in templates: `feature`, `bugfix`, `hotfix`.

```bash
owt new "Add payments" --template feature   # Plan mode, TDD workflow
owt new "Fix crash" --template bugfix       # Root cause focus
owt new "Patch CVE" --template hotfix       # Minimal changes, production focus
```

## Configuration

Config files (priority order): `.worktreerc`, `.worktreerc.toml`, `~/.config/open-orchestrator/config.toml`, `~/.worktreerc`

```toml
[worktree]
base_directory = "../"
auto_cleanup_days = 14

[tmux]
auto_start_ai = true
ai_tool = "claude"    # claude, opencode, droid

[environment]
auto_install_deps = true
copy_env_file = true
```

## AI Tool Support

Auto-detects installed tools: Claude Code, OpenCode, Droid. Offers picker when multiple found.

```bash
owt new "task" --ai-tool claude
owt new "task" --ai-tool opencode
owt new "task" --ai-tool droid
```

## Project Detection

Auto-detects: Python (uv/poetry/pip), Node.js (bun/pnpm/yarn/npm), Rust (cargo), Go, PHP (composer).

## Dependencies

7 production deps: click, pydantic, rich, textual, toml, gitpython, libtmux.

---
name: open-orchestrator
description: "Git worktree + AI agent orchestration with curses switchboard UI. Use when: (1) Creating isolated dev environments from task descriptions (owt new), (2) Viewing all agent worktrees in a switchboard card grid (owt), (3) Jumping between agent sessions (owt switch), (4) Sending messages to agents (owt send), (5) Merging worktree branches with two-phase merge (owt merge), (6) Deleting worktrees atomically (owt delete), (7) Orchestrating AI tools across branches (auto-detects claude, opencode, droid), (8) Cleaning up stale worktrees, (9) Syncing worktrees with upstream. Triggers: worktree, parallel development, multi-branch, AI orchestration, switchboard, owt commands, owt new, owt merge, owt delete, owt switch, owt send, auto-detect agents."
---

# Open Orchestrator - Git Worktree + AI Orchestration

Open Orchestrator (`owt`) enables developers to manage parallel development workflows with isolated git worktrees and a **curses-based switchboard UI**. The simplest way to start: `owt new "add user authentication"` — it auto-generates a branch name, creates the worktree, installs deps, and starts the AI tool in a tmux session. Run `owt` with no arguments to launch the switchboard — a card grid showing all active agents with status lights.

## Commands (10 total)

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Switchboard** — card grid with status lights, navigate + act |
| `owt new "task"` | `owt n` | Create worktree + tmux session + deps + AI agent. One command. |
| `owt list` | `owt ls` | Quick text list of worktrees (non-interactive, for scripts/pipes) |
| `owt switch <name>` | `owt s` | Jump to a worktree's tmux session |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt merge <name>` | `owt m` | Two-phase merge + auto-cleanup worktree + tmux session |
| `owt delete <name>` | `owt rm` | Delete worktree + tmux session + status |
| `owt sync [--all]` | | Sync worktree(s) with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees (dry-run by default) |
| `owt version` | | Show version |

## The Switchboard

Run `owt` to launch the switchboard — your command center for multi-agent orchestration:

```
  SWITCHBOARD                                    4 lines  *3 active o1

  +- auth-jwt --------------+   +- fix-login --------------+
  | * WORKING        12m    |   | o IDLE              3h    |
  | feat/auth-jwt           |   | fix/login-redirect        |
  | claude                  |   | claude                    |
  | Implementing JWT auth   |   | -                         |
  +-------------------------+   +---------------------------+

  [arrows] navigate  [Enter] patch in  [s] send  [n] new  [d] drop  [q] quit
```

**Switchboard keys:**
- Arrow keys: navigate between cards
- `Enter`: patch into that agent's tmux session (switchboard stays alive)
- `s`: send a message to the selected agent
- `n`: create a new worktree + agent
- `d`: delete the selected worktree
- `m`: merge the selected worktree
- `q`: quit back to terminal

**Global tmux keybindings (work from any agent session):**
- `Alt+s`: switch back to the switchboard
- `Alt+c`: create a new worktree (opens popup)

**Navigation flow:** `owt` → switchboard → `Enter` → agent → `Alt+s` → switchboard → `q` → terminal

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
```

### 2. Monitor via Switchboard
```bash
owt           # Launch switchboard in persistent tmux session
owt list      # Quick text table for scripts/pipes
```

### 3. Interact with Agents
```bash
# From the switchboard: press Enter to patch into an agent session
# From any agent session: press Alt+s to return to the switchboard
# Or use CLI:
owt send auth-jwt "Fix the failing tests"
owt switch auth-jwt    # Jump to that tmux session
```

### 4. Complete Work
```bash
owt merge auth-jwt     # Two-phase merge + auto-cleanup
owt delete fix-login   # Delete worktree + session + status
owt cleanup            # Remove stale worktrees (dry-run)
owt cleanup --force    # Actually delete stale worktrees
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

6 production deps: click, pydantic, rich, toml, gitpython, libtmux.

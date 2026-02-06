# Open Orchestrator

A Git Worktree + AI coding tool orchestration system for managing parallel development workflows with AI agent swarms. Coordinate multiple Claude Code sessions, Agent Teams, or AI agents across isolated branches with single-terminal control. Supports Claude Code, OpenCode, and Droid.

## Overview

Open Orchestrator enables developers to work on multiple tasks simultaneously by creating isolated worktrees, each with its own Claude Code session and tmux pane. Perfect for parallel development workflows where you need to context-switch between features without losing your place.

> **Agent Teams vs Open Orchestrator:** While [Claude Code's Agent Teams](https://code.claude.com/docs/en/agent-teams) coordinate multiple AI agents within the *same codebase*, Open Orchestrator manages multiple *isolated worktrees* (different branches, different directories, independent environments). They're complementary tools that can work together - use Agent Teams for intra-branch collaboration, Open Orchestrator for cross-branch orchestration. [Learn more](#open-orchestrator-vs-agent-teams)

## Features

- **Git Worktree Management**: Create, list, switch, and delete worktrees with automatic branch management
- **tmux Integration**: Auto-create tmux sessions with customizable layouts for each worktree
- **Multi-AI Tool Support**: Auto-launch Claude Code, OpenCode, or Droid in new sessions
- **Project Detection**: Automatically detect project type (Python, Node.js, Rust, Go, PHP) and package manager
- **Dependency Installation**: Auto-install dependencies when creating new worktrees
- **Environment Setup**: Copy `.env` files and `CLAUDE.md` with path adjustments
- **Cleanup Service**: Detect and clean up stale worktrees with safety checks
- **Live Dashboard**: Real-time TUI monitoring of all worktrees and AI sessions
- **Token Tracking**: Monitor AI token usage and estimated costs per worktree
- **GitHub PR Integration**: Link worktrees to PRs, track PR status, clean up merged PRs
- **Status Change Hooks**: Trigger notifications, webhooks, or scripts on status changes
- **Session Management**: Copy Claude sessions between worktrees, resume previous sessions
- **Shell Completion**: Auto-completion for bash, zsh, and fish shells
- **No-tmux Mode**: Manage AI processes without tmux for simpler setups

## Installation

### Requirements

- Python 3.10 or higher
- Git
- tmux
- Claude Code CLI

### Install with uv (recommended)

```bash
# Clone the repository
git clone https://github.com/gitpcl/openorchestrator.git
cd open-orchestrator

# Install with uv
uv pip install -e .

# Or install with dev dependencies
uv pip install -e ".[dev]"
```

### Install with pip

```bash
pip install -e .
```

## Quick Start

### Create a new worktree

```bash
# Create a worktree for a new feature branch
owt create feature/add-login

# Create a worktree from an existing branch
owt create feature/existing-branch --no-create-branch

# Create a worktree with Claude in plan mode (safe exploration)
owt create feature/research --plan-mode

# Create a worktree with OpenCode instead of Claude
owt create feature/new-api --ai-tool opencode

# Create a worktree with Droid in high autonomy mode
owt create feature/refactor --ai-tool droid --droid-auto high

# Create a worktree with OpenCode and custom config
owt create feature/test --ai-tool opencode --opencode-config ~/.config/opencode.json
```

### List worktrees

```bash
owt list
```

### Switch to a worktree

```bash
owt switch feature/add-login
```

### Delete a worktree

```bash
owt delete feature/add-login
```

### Clean up stale worktrees

```bash
# Dry run (show what would be cleaned)
owt cleanup --dry-run

# Actually clean up
owt cleanup
```

### Monitor with live dashboard

```bash
# Launch live dashboard
owt dashboard

# Check token usage
owt tokens show
```

## CLI Command Reference

### Worktree Commands

| Command | Description |
|---------|-------------|
| `owt create <branch>` | Create a new worktree with tmux session and Claude Code |
| `owt list` | List all worktrees with status |
| `owt switch <name>` | Switch to worktree (prints path or attaches tmux) |
| `owt delete <name>` | Delete worktree and its tmux session |
| `owt cleanup` | Remove stale worktrees (dry-run by default) |
| `owt sync <name>` | Sync a worktree with its upstream branch |
| `owt send <name> "cmd"` | Send a command to another worktree's Claude session |
| `owt status [name]` | Show Claude activity status across all worktrees |
| `owt dashboard` | Launch live TUI dashboard to monitor all worktrees |

### Session & Token Commands

| Command | Description |
|---------|-------------|
| `owt copy-session <src> <dest>` | Copy Claude session data between worktrees |
| `owt resume <name>` | Get resume command for a worktree's Claude session |
| `owt session [name]` | Show Claude session information for worktrees |
| `owt tokens show [name]` | Show token usage for worktree(s) |
| `owt tokens update <name>` | Manually update token usage |
| `owt tokens reset <name>` | Reset token usage to zero |

### GitHub PR Commands

| Command | Description |
|---------|-------------|
| `owt pr link <name> --pr <num>` | Link a worktree to a GitHub PR |
| `owt pr unlink <name>` | Remove PR association from worktree |
| `owt pr status [name]` | Show PR status for worktree(s) |
| `owt pr list` | List all worktrees with linked PRs |
| `owt pr refresh <name>` | Refresh PR info from GitHub |
| `owt pr cleanup` | Delete worktrees with merged PRs |

### Hook Commands

| Command | Description |
|---------|-------------|
| `owt hooks list` | List configured status change hooks |
| `owt hooks add` | Add a new hook (interactive) |
| `owt hooks remove <id>` | Remove a hook by ID |
| `owt hooks enable <id>` | Enable a disabled hook |
| `owt hooks disable <id>` | Disable a hook |
| `owt hooks test <id>` | Test a hook execution |
| `owt hooks history` | View hook execution history |
| `owt hooks clear-history` | Clear hook execution history |

### Process Commands (No-tmux Mode)

| Command | Description |
|---------|-------------|
| `owt process start <name>` | Start AI tool as background process |
| `owt process stop <name>` | Stop AI tool process |
| `owt process list` | List running AI tool processes |
| `owt process logs <name>` | View logs for an AI tool process |

### Shell Completion

| Command | Description |
|---------|-------------|
| `owt completion bash` | Generate bash completion script |
| `owt completion zsh` | Generate zsh completion script |
| `owt completion fish` | Generate fish completion script |
| `owt completion install` | Show installation instructions |

### Claude Code Skill

| Command | Description |
|---------|-------------|
| `owt skill install` | Install skill to ~/.claude/skills/ (symlink) |
| `owt skill install --copy` | Install skill as copy (not symlink) |
| `owt skill status` | Check skill installation status |
| `owt skill uninstall` | Remove skill from ~/.claude/skills/ |

### tmux Commands

| Command | Description |
|---------|-------------|
| `owt tmux create <name>` | Create a tmux session |
| `owt tmux attach <name>` | Attach to an existing session |
| `owt tmux list` | List worktree tmux sessions |
| `owt tmux kill <name>` | Kill a tmux session |
| `owt tmux send <name> "keys"` | Send keys to a session pane |

### Command Options

#### `owt create`

| Option | Description |
|--------|-------------|
| `-b, --base <branch>` | Base branch for creating new branches |
| `-p, --path <path>` | Custom path for the worktree |
| `-f, --force` | Force creation even if branch exists elsewhere |
| `--tmux / --no-tmux` | Create tmux session (default: enabled) |
| `--claude / --no-claude` | Auto-start AI tool (default: enabled) |
| `--ai-tool <tool>` | AI tool to start: `claude`, `opencode`, `droid` (default: claude) |
| `--droid-auto <level>` | Droid autonomy level: `low`, `medium`, `high` |
| `--droid-skip-permissions` | Skip Droid permissions check (use with caution) |
| `--opencode-config <path>` | Path to OpenCode configuration file |
| `-l, --layout <layout>` | tmux layout: `main-vertical`, `three-pane`, `quad`, `even-horizontal`, `even-vertical` |
| `--panes <n>` | Number of panes (default: 2) |
| `-a, --attach` | Attach to tmux session after creation |
| `--deps / --no-deps` | Install dependencies (default: enabled) |
| `--env / --no-env` | Copy .env file (default: enabled) |
| `--plan-mode` | Start Claude in plan mode (safe exploration) |
| `--sync-claude-md / --no-sync-claude-md` | Copy CLAUDE.md files (default: enabled) |

#### `owt list`

| Option | Description |
|--------|-------------|
| `-a, --all` | Show all worktrees including main |

#### `owt switch`

| Option | Description |
|--------|-------------|
| `-t, --tmux` | Attach to worktree's tmux session |

#### `owt delete`

| Option | Description |
|--------|-------------|
| `-f, --force` | Force deletion with uncommitted changes |
| `-y, --yes` | Skip confirmation prompt |
| `--keep-tmux` | Keep the associated tmux session |

#### `owt cleanup`

| Option | Description |
|--------|-------------|
| `-d, --days <n>` | Days threshold for stale detection (default: 14) |
| `--dry-run / --no-dry-run` | Preview mode (default: dry-run) |
| `-f, --force` | Include worktrees with uncommitted changes |
| `-y, --yes` | Skip confirmation prompt |
| `--json` | Output results in JSON format |

#### `owt sync`

| Option | Description |
|--------|-------------|
| `-a, --all` | Sync all worktrees |
| `--strategy <merge\|rebase>` | Pull strategy (default: merge) |
| `--no-stash` | Don't auto-stash uncommitted changes |
| `--json` | Output results in JSON format |

#### `owt dashboard`

| Option | Description |
|--------|-------------|
| `-r, --refresh <secs>` | Refresh rate in seconds (default: 2.0) |
| `--no-tokens` | Hide token usage columns |
| `--no-commands` | Hide command count column |
| `-c, --compact` | Compact mode (no summary panel) |

#### `owt send`

| Option | Description |
|--------|-------------|
| `-p, --pane <n>` | Target pane index (default: 0) |
| `-w, --window <n>` | Target window index (default: 0) |
| `--no-enter` | Don't press Enter after sending |
| `--no-log` | Don't persist command in status history |

#### `owt status`

| Option | Description |
|--------|-------------|
| `-a, --all` | Show status for all worktrees |
| `--set-task <text>` | Set the current task for this worktree |
| `--set-status <status>` | Set activity status: idle, working, blocked, waiting, completed, error |
| `--notes <text>` | Set notes for this worktree |
| `--json` | Output as JSON |

#### `owt tmux create`

| Option | Description |
|--------|-------------|
| `-d, --directory <path>` | Working directory (default: current) |
| `-l, --layout <layout>` | Pane layout |
| `-p, --panes <n>` | Number of panes |
| `--claude / --no-claude` | Auto-start AI tool (default: enabled) |
| `--ai-tool <tool>` | AI tool to start: `claude`, `opencode`, `droid` (default: claude) |
| `--droid-auto <level>` | Droid autonomy level: `low`, `medium`, `high` |
| `--droid-skip-permissions` | Skip Droid permissions check (use with caution) |
| `--opencode-config <path>` | Path to OpenCode configuration file |
| `-a, --attach` | Attach after creation |

#### `owt tmux list`

| Option | Description |
|--------|-------------|
| `-a, --all` | Show all tmux sessions |
| `--json` | Output as JSON |

#### `owt tmux kill`

| Option | Description |
|--------|-------------|
| `-f, --force` | Kill without confirmation |

#### `owt tmux send`

| Option | Description |
|--------|-------------|
| `-p, --pane <n>` | Target pane index |
| `-w, --window <n>` | Target window index |

## Configuration

Create a `.worktreerc` file in your project root to customize behavior:

```toml
[worktree]
base_directory = "../"
naming_pattern = "{project}-{branch}"
auto_cleanup_days = 14

[tmux]
default_layout = "main-vertical"
auto_start_ai = true
ai_tool = "claude"  # Options: claude, opencode, droid
pane_count = 2

[environment]
auto_install_deps = true
copy_env_file = true

# Droid-specific configuration
[droid]
default_auto_level = "medium"  # low, medium, high
skip_permissions_unsafe = false

# OpenCode-specific configuration
[opencode]
config_path = "~/.config/opencode/opencode.json"
```

### Configuration Options

#### `[worktree]` Section

| Option | Default | Description |
|--------|---------|-------------|
| `base_directory` | `"../"` | Where to create worktrees relative to main repo |
| `naming_pattern` | `"{project}-{branch}"` | Pattern for worktree directory names |
| `auto_cleanup_days` | `14` | Days before a worktree is considered stale |

#### `[tmux]` Section

| Option | Default | Description |
|--------|---------|-------------|
| `default_layout` | `"main-vertical"` | Default tmux pane layout |
| `auto_start_ai` | `true` | Auto-start AI tool in first pane |
| `ai_tool` | `"claude"` | AI tool to start: `claude`, `opencode`, `droid` |
| `pane_count` | `2` | Number of panes to create |

Available layouts:
- `main-vertical`: Large left pane, smaller right panes
- `three-pane`: Main top pane, two bottom panes
- `quad`: Four equal panes
- `even-horizontal`: Equal horizontal split
- `even-vertical`: Equal vertical split

#### `[environment]` Section

| Option | Default | Description |
|--------|---------|-------------|
| `auto_install_deps` | `true` | Auto-install dependencies on worktree creation |
| `copy_env_file` | `true` | Copy `.env` file to new worktrees |

#### `[droid]` Section

| Option | Default | Description |
|--------|---------|-------------|
| `default_auto_level` | `null` | Default autonomy level: `low`, `medium`, `high` |
| `skip_permissions_unsafe` | `false` | Skip permissions check (use with caution) |

#### `[opencode]` Section

| Option | Default | Description |
|--------|---------|-------------|
| `config_path` | `null` | Path to OpenCode configuration file |

## Project Detection

Open Orchestrator automatically detects your project type and package manager:

| Project Type | Detected By | Package Manager Priority |
|-------------|-------------|-------------------------|
| Python | `pyproject.toml`, `requirements.txt` | uv > poetry > pipenv > pip |
| Node.js | `package.json` | bun > pnpm > yarn > npm |
| Rust | `Cargo.toml` | cargo |
| Go | `go.mod` | go |
| PHP | `composer.json` | composer |

## Usage Modes

Open Orchestrator can be used in two ways:

### 1. Standalone CLI Tool

Use `owt` directly from the terminal to manage worktrees and tmux sessions:

```bash
# Create a worktree with auto-setup
owt create feature/my-feature

# List all worktrees
owt list

# Attach to a worktree's tmux session
owt switch feature/my-feature --tmux

# Clean up stale worktrees
owt cleanup --dry-run
```

This mode is ideal for developers who want worktree management without Claude Code integration.

### 2. Claude Code Plugin Integration

For developers using Claude Code, the tool provides slash commands and context hooks that allow Claude to manage worktrees on your behalf.

#### Setup

Copy the `.claude/` directory to your project (or symlink it):

```bash
# From your project root
cp -r /path/to/open-orchestrator/.claude .
```

Or add the permissions to your existing `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(owt:*)",
      "Bash(git worktree:*)",
      "Bash(tmux:*)"
    ]
  }
}
```

#### Slash Commands

Once configured, Claude Code can use these slash commands:

| Command | Description |
|---------|-------------|
| `/worktree` | Main worktree management (create, list, switch, delete) |
| `/wt-create` | Quick worktree creation shortcut |
| `/wt-list` | List all worktrees with status |
| `/wt-cleanup` | Clean up stale worktrees |
| `/wt-status` | Show Claude activity status across worktrees |

Example usage in Claude Code:
```
/worktree create feature/add-authentication
```

#### Context Hook (Optional)

To automatically inject worktree context into Claude Code prompts, add the hook to your `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "type": "command",
        "command": "python3 scripts/context-injector.py"
      }
    ]
  }
}
```

This will show `[Worktree: name | Branch: branch]` in your prompts when working in a worktree.

## Orchestration Workflow

Open Orchestrator's key value proposition is **single-terminal orchestration** - control multiple AI coding sessions from one terminal window without constantly switching contexts.

### The Problem

Traditional parallel development requires:
- Multiple terminal windows/tabs open
- Manual context switching between sessions
- Lost focus from constant window management
- No visibility into what AI agents are working on
- Difficulty coordinating work across branches

### The Solution: Single-Terminal Control

Open Orchestrator lets you:
1. **Create isolated worktrees** with dedicated AI sessions
2. **Send commands** to any worktree from your main terminal
3. **Track AI activity** across all worktrees in real-time
4. **Orchestrate work** without leaving your current context

### Open Orchestrator vs Agent Teams

**Not familiar with [Claude Code's Agent Teams](https://code.claude.com/docs/en/agent-teams)?** They're an experimental feature that lets multiple AI agents coordinate within the same codebase using shared task lists and inter-agent messaging.

**Key Difference:**
- **Agent Teams**: Multiple AI agents collaborating in the **same worktree** (same branch, same directory)
- **Open Orchestrator**: Multiple **isolated worktrees** (different branches, different directories, different environments)

| Feature | Agent Teams | Open Orchestrator |
|---------|-------------|-------------------|
| **Scope** | Same codebase, multiple agents | Multiple branches, multiple codebases |
| **Coordination** | Agents message each other | Single-terminal command delegation |
| **Isolation** | Shared git worktree | Separate worktrees with independent environments |
| **Dependencies** | Same node_modules/venv | Each worktree has its own dependencies |
| **Best For** | Code review, competing hypotheses, research | Parallel feature development, branch management |
| **Infrastructure** | Built into Claude Code | CLI + git worktrees + tmux |

### Using Both Together

Open Orchestrator can **enhance your Agent Team workflows** by providing infrastructure for agent swarms across branches:

**Pattern 1: Agent Teams per Feature Branch**
```bash
# Create isolated worktree for feature A
owt create feature/auth --plan-mode

# Inside that worktree, spawn Agent Team
# Have multiple agents collaborate on auth implementation

# Meanwhile, create another worktree for feature B
owt create feature/payments
# Spawn different Agent Team here

# Monitor both from main terminal
owt status --all
```

**Pattern 2: Parallel Agent Team Research**
```bash
# Create worktrees for different experiments
owt create experiment/approach-a
owt create experiment/approach-b
owt create experiment/approach-c

# In each worktree, spawn Agent Team to explore different approaches
# Each team works in isolation with their own dependencies
# Compare results across worktrees without conflicts
```

**Pattern 3: Agent Team + Infrastructure Orchestration**
```bash
# Use Open Orchestrator for infrastructure
owt create feature/refactor

# Use Agent Teams inside for collaboration
# One agent on security review
# One agent on performance
# One agent on test coverage

# Use Open Orchestrator to track progress
owt status feature/refactor

# Use Open Orchestrator to link to GitHub PR
owt pr link feature/refactor --pr 123
```

**Why This Combination Works:**
- ✅ **Agent Teams** handle *intra-branch* coordination (multiple agents, one codebase)
- ✅ **Open Orchestrator** handles *cross-branch* orchestration (multiple worktrees, isolated environments)
- ✅ Use Agent Teams when you need agents to debate, review, or collaborate on the same code
- ✅ Use Open Orchestrator when you need complete isolation between different features/experiments
- ✅ Combine both for maximum parallelism: agent swarms working across multiple isolated branches

### Example Workflow

**Scenario:** You're working on a frontend feature but need to quickly test API changes in parallel.

```bash
# In your main terminal (working on frontend)
$ cd my-project

# Create a worktree for API work (auto-creates tmux + AI session)
$ owt create feature/api-refactor
✅ Created worktree: feature/api-refactor
✅ tmux session: owt-api-refactor
✅ Claude Code started in pane 0

# Send a command to the API worktree's AI session
$ owt send api-refactor "Review the authentication endpoints and suggest improvements"
📤 Sent to api-refactor (pane 0)

# Check what all AI sessions are doing
$ owt status
┌─────────────────┬──────────┬───────────────────────────┬─────────────┐
│ Worktree        │ Status   │ Current Task              │ Last Active │
├─────────────────┼──────────┼───────────────────────────┼─────────────┤
│ main            │ working  │ Frontend auth UI          │ 2m ago      │
│ api-refactor    │ working  │ Reviewing auth endpoints  │ just now    │
│ feature/cleanup │ idle     │ -                         │ 3h ago      │
└─────────────────┴──────────┴───────────────────────────┴─────────────┘

# Continue working in your main terminal
# AI in api-refactor is working independently
# You'll see status updates when you check again

# Later: Get results from the API worktree
$ owt switch api-refactor --tmux
# [Now attached to api-refactor session, see Claude's analysis]
```

### Send/Receive Notification Pattern

The `owt send` command creates a **fire-and-forget notification system** between worktrees:

#### Sending Commands

```bash
# Basic send
$ owt send <worktree-name> "command or instruction"

# Send to specific tmux pane
$ owt send api-refactor "run tests" --pane 1

# Send without auto-entering (for multi-line prep)
$ owt send frontend "implement login form" --no-enter

# Send without logging to status history
$ owt send cleanup "check for stale code" --no-log
```

#### How It Works

1. **Command Sent:** Your command is transmitted to the target worktree's tmux session
2. **AI Receives:** If AI tool is active in that pane, it receives the instruction
3. **Status Logged:** Command is logged in `~/.open-orchestrator/ai_status.json` (unless `--no-log`)
4. **You Continue:** Return immediately to your current work
5. **Check Later:** Use `owt status` to see progress

#### Real-World Use Cases

**Use Case 1: Parallel Code Review**
```bash
# You're fixing bugs, but want AI to review another branch
$ owt create feature/review-auth
$ owt send review-auth "Review the authentication code for security issues"
# Continue fixing bugs while AI reviews independently
```

**Use Case 2: Background Testing**
```bash
# Start long-running tests in another worktree
$ owt send test-branch "Run full integration test suite"
$ owt status test-branch  # Check later if tests passed
```

**Use Case 3: Research Tasks**
```bash
# Ask AI to research while you implement
$ owt send research "Find best practices for rate limiting in Express.js"
# AI researches in background, you check results when ready
```

**Use Case 4: Multi-Branch Coordination**
```bash
# Coordinate work across multiple features
$ owt send frontend "Implement login UI using design system"
$ owt send backend "Add OAuth endpoints with JWT tokens"
$ owt send docs "Document the new authentication flow"
$ owt status --all  # See all AI agents working
```

### Status Tracking

Track AI activity across all worktrees from a single terminal:

```bash
# View all worktree status
$ owt status

# View specific worktree status
$ owt status api-refactor

# Set custom status for a worktree
$ owt status --set-task "Implementing auth flow" --set-status working

# Add notes to a worktree
$ owt status --notes "Waiting for API design approval"

# Export status as JSON (for scripts/dashboards)
$ owt status --json
```

**Status Fields:**
- **Status:** `idle`, `working`, `blocked`, `waiting`, `completed`, `error`
- **Current Task:** What the AI is working on
- **Command History:** Recent commands sent to this worktree
- **Token Usage:** Input/output tokens and estimated cost
- **Last Active:** Timestamp of last activity

### Live Dashboard

Monitor all worktrees in real-time with a live terminal UI:

```bash
# Launch the dashboard (updates every 2 seconds)
$ owt dashboard

# Faster refresh rate
$ owt dashboard -r 1

# Compact mode (minimal UI)
$ owt dashboard --compact

# Hide token usage columns
$ owt dashboard --no-tokens
```

The dashboard shows:
- Real-time status indicators (● working, ○ idle, ■ blocked)
- Current task for each worktree
- Token usage and estimated costs
- Command counts and last activity times

### Token Tracking

Track AI token usage and costs across worktrees:

```bash
# View token usage for all worktrees
$ owt tokens show

# View for specific worktree
$ owt tokens show feature/api

# Manually update token usage (when parsing Claude output)
$ owt tokens update feature/api --input 1000 --output 500

# Reset token usage
$ owt tokens reset feature/api
```

Token tracking includes:
- Input and output token counts
- Cache read/write tokens
- Estimated cost (based on Claude Opus pricing)

### GitHub PR Integration

Link worktrees to GitHub PRs for better tracking:

```bash
# Link a worktree to a PR
$ owt pr link feature/auth --pr 123

# View PR status
$ owt pr status feature/auth

# List all worktrees with linked PRs
$ owt pr list

# Refresh PR info from GitHub
$ owt pr refresh feature/auth

# Clean up worktrees with merged PRs
$ owt pr cleanup
```

PRs are auto-detected from branch names matching patterns like `feature/123-description`.

### Status Change Hooks

Trigger actions when AI status changes:

```bash
# List configured hooks
$ owt hooks list

# Add a new hook (interactive)
$ owt hooks add

# Test a hook
$ owt hooks test <hook-id>

# View hook execution history
$ owt hooks history
```

Hook types:
- **Shell commands:** Run any command on status change
- **Notifications:** Desktop notifications (macOS/Linux)
- **Webhooks:** POST to URLs (Slack, Discord, etc.)
- **Logging:** Log status changes to file

### Shell Completion

Install tab completion for your shell:

```bash
# Show installation instructions
$ owt completion install

# Bash: Add to ~/.bashrc
eval "$(owt completion bash)"

# Zsh: Add to ~/.zshrc
eval "$(owt completion zsh)"

# Fish: Save to completions
owt completion fish > ~/.config/fish/completions/owt.fish
```

### Claude Code Skill Installation

Install the Open Orchestrator skill for Claude Code to get intelligent command suggestions:

```bash
# Install skill (creates symlink - recommended)
$ owt skill install
✓ Created ~/.claude/skills/open-orchestrator/
✓ Linked SKILL.md → /path/to/open-orchestrator/src/open_orchestrator/skills/open-orchestrator/SKILL.md
✓ Skill installed successfully!

# Or install as copy (independent file)
$ owt skill install --copy

# Check installation status
$ owt skill status
Open Orchestrator Skill
  Status:   Installed (symlink)
  Source:   /path/to/package/skills/open-orchestrator/SKILL.md
  Target:   ~/.claude/skills/open-orchestrator/SKILL.md
  Up-to-date: ✓

# Uninstall
$ owt skill uninstall
```

The skill provides Claude Code with context about Open Orchestrator commands, enabling it to suggest appropriate `owt` commands when you mention worktrees, parallel development, or AI orchestration.

### No-tmux Mode

For simpler setups without tmux:

```bash
# Start AI tool as background process
$ owt process start feature/api

# List running processes
$ owt process list

# View process logs
$ owt process logs feature/api

# Stop a process
$ owt process stop feature/api
```

Processes are tracked via PID files and logs are saved to `~/.cache/open-orchestrator/logs/`.

### Benefits

✅ **Stay in Flow:** No context switching - send tasks and continue working
✅ **Parallel Execution:** Multiple AI sessions work simultaneously
✅ **Visibility:** Always know what each AI is doing via `owt status`
✅ **Async Coordination:** Fire-and-forget task delegation
✅ **Audit Trail:** Full command history logged for each worktree

## Development

### Setup development environment

```bash
# Clone and install with dev dependencies
git clone https://github.com/gitpcl/openorchestrator.git
cd open-orchestrator
uv pip install -e ".[dev]"

# Or use make
make install-uv
```

### Running Tests

The project includes 290+ tests with 90%+ coverage. Use the Makefile for common tasks:

```bash
# Run all tests with coverage
make test

# Run tests excluding slow tests
make test-fast

# Run tests and open HTML coverage report
make test-cov

# Run linting
make lint

# Format code
make format

# Clean up test artifacts
make clean
```

### Docker Testing

For isolated, reproducible testing:

```bash
# Run tests in Docker container
make test-docker

# Interactive Docker shell for debugging
make test-docker-interactive
```

### Test Markers

```bash
# Run only tests requiring GitHub CLI
pytest -m gh_cli

# Run only tmux-dependent tests
pytest -m tmux

# Exclude slow tests
pytest -m "not slow"
```

See [TESTING.md](TESTING.md) for comprehensive testing documentation.

### Project Structure

```
open-orchestrator/
├── src/open_orchestrator/
│   ├── __init__.py
│   ├── cli.py                     # Main CLI entry point
│   ├── config.py                  # Configuration management
│   ├── core/
│   │   ├── worktree.py            # Git worktree operations
│   │   ├── project_detector.py    # Project type detection
│   │   ├── environment.py         # Dependency, .env & CLAUDE.md setup
│   │   ├── tmux_manager.py        # tmux session management
│   │   ├── tmux_cli.py            # tmux CLI commands
│   │   ├── cleanup.py             # Worktree cleanup/maintenance
│   │   ├── sync.py                # Upstream sync operations
│   │   ├── status.py              # AI activity status tracking
│   │   ├── hooks.py               # Status change hooks
│   │   ├── session.py             # Claude session management
│   │   ├── pr_linker.py           # GitHub PR integration
│   │   ├── process_manager.py     # Non-tmux process management
│   │   ├── dashboard.py           # Live TUI dashboard
│   │   └── skill_installer.py     # Claude Code skill installation
│   ├── skills/
│   │   └── open-orchestrator/
│   │       └── SKILL.md           # Claude Code skill definition
│   ├── models/
│   │   ├── worktree_info.py       # Worktree info models
│   │   ├── project_config.py      # Project configuration models
│   │   ├── maintenance.py         # Cleanup & sync models
│   │   ├── status.py              # AI status & token usage models
│   │   ├── hooks.py               # Hook configuration models
│   │   ├── session.py             # Session data models
│   │   └── pr_info.py             # PR info models
│   └── utils/
│       └── io.py                  # Safe file I/O utilities
├── tests/
│   ├── conftest.py                # Shared fixtures (30+ fixtures)
│   ├── test_cli.py                # CLI integration tests
│   ├── test_cleanup.py            # CleanupService tests
│   ├── test_dashboard.py          # Dashboard TUI tests
│   ├── test_environment.py        # Environment setup tests
│   ├── test_hooks.py              # HookService tests
│   ├── test_pr_linker.py          # PRLinker tests
│   ├── test_process_manager.py    # ProcessManager tests
│   ├── test_session.py            # SessionManager tests
│   ├── test_skill_installer.py    # SkillInstaller tests
│   ├── test_status.py             # StatusTracker tests
│   ├── test_sync.py               # SyncService tests
│   ├── test_tmux_manager.py       # TmuxManager tests
│   └── test_worktree.py           # WorktreeManager tests
├── Makefile                       # Common development tasks
├── Dockerfile.test                # Docker test environment
├── docker-compose.test.yml        # Docker compose for testing
├── TESTING.md                     # Comprehensive testing guide
├── scripts/
│   └── context-injector.py        # Claude Code context hook
├── .claude/
│   ├── CLAUDE.md                  # Project instructions for Claude Code
│   ├── commands/                  # Claude Code slash commands
│   └── settings.json              # Permissions configuration
├── pyproject.toml
└── .worktreerc.example
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Built for use with [Claude Code](https://claude.ai/claude-code)
- Inspired by the need for better parallel development workflows

# Open Orchestrator

A Git Worktree + AI coding tool orchestration tool combining a Python CLI with plugin integration for managing parallel development workflows. Supports Claude Code, OpenCode, and Droid.

## Overview

Open Orchestrator enables developers to work on multiple tasks simultaneously by creating isolated worktrees, each with its own Claude Code session and tmux pane. Perfect for parallel development workflows where you need to context-switch between features without losing your place.

## Features

- **Git Worktree Management**: Create, list, switch, and delete worktrees with automatic branch management
- **tmux Integration**: Auto-create tmux sessions with customizable layouts for each worktree
- **Multi-AI Tool Support**: Auto-launch Claude Code, OpenCode, or Droid in new sessions
- **Project Detection**: Automatically detect project type (Python, Node.js, Rust, Go, PHP) and package manager
- **Dependency Installation**: Auto-install dependencies when creating new worktrees
- **Environment Setup**: Copy `.env` files with path adjustments
- **Cleanup Service**: Detect and clean up stale worktrees with safety checks

## Installation

### Requirements

- Python 3.10 or higher
- Git
- tmux
- Claude Code CLI

### Install with uv (recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/open-orchestrator.git
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
| `-l, --layout <layout>` | tmux layout: `main-vertical`, `three-pane`, `quad`, `even-horizontal`, `even-vertical` |
| `--panes <n>` | Number of panes (default: 2) |
| `-a, --attach` | Attach to tmux session after creation |
| `--deps / --no-deps` | Install dependencies (default: enabled) |
| `--env / --no-env` | Copy .env file (default: enabled) |

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

#### `owt sync`

| Option | Description |
|--------|-------------|
| `-a, --all` | Sync all worktrees |
| `--strategy <merge\|rebase>` | Pull strategy (default: merge) |
| `--no-stash` | Don't auto-stash uncommitted changes |

#### `owt send`

| Option | Description |
|--------|-------------|
| `-p, --pane <n>` | Target pane index (default: 0) |
| `-w, --window <n>` | Target window index (default: 0) |
| `--no-enter` | Don't press Enter after sending |

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
auto_start_claude = true
ai_tool = "claude"  # Options: claude, opencode, droid
pane_count = 2

[environment]
auto_install_deps = true
copy_env_file = true
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
| `auto_start_claude` | `true` | Auto-start AI tool in first pane |
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

## Development

### Setup development environment

```bash
# Clone and install with dev dependencies
git clone https://github.com/yourusername/open-orchestrator.git
cd open-orchestrator
uv pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=open_orchestrator

# Run linting
ruff check .

# Run type checking
mypy src/open_orchestrator
```

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
│   │   ├── environment.py         # Dependency & .env setup
│   │   ├── tmux_manager.py        # tmux session management
│   │   ├── tmux_cli.py            # tmux CLI commands
│   │   ├── cleanup.py             # Worktree cleanup/maintenance
│   │   └── sync.py                # Upstream sync operations
│   ├── models/
│   │   ├── worktree_info.py       # Worktree info models
│   │   ├── project_config.py      # Project configuration models
│   │   └── maintenance.py         # Cleanup & sync models
│   └── utils/                     # Utility functions
├── tests/
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

# Claude Orchestrator

A Git Worktree + Claude Code orchestration tool combining a Python CLI with Claude Code plugin integration for managing parallel development workflows.

## Overview

Claude Orchestrator enables developers to work on multiple tasks simultaneously by creating isolated worktrees, each with its own Claude Code session and tmux pane. Perfect for parallel development workflows where you need to context-switch between features without losing your place.

## Features

- **Git Worktree Management**: Create, list, switch, and delete worktrees with automatic branch management
- **tmux Integration**: Auto-create tmux sessions with customizable layouts for each worktree
- **Auto Claude Code Launch**: Automatically start Claude Code in new worktree sessions
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
git clone https://github.com/yourusername/claude-orchestrator.git
cd claude-orchestrator

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
cwt create feature/add-login

# Create a worktree from an existing branch
cwt create feature/existing-branch --no-create-branch
```

### List worktrees

```bash
cwt list
```

### Switch to a worktree

```bash
cwt switch feature/add-login
```

### Delete a worktree

```bash
cwt delete feature/add-login
```

### Clean up stale worktrees

```bash
# Dry run (show what would be cleaned)
cwt cleanup --dry-run

# Actually clean up
cwt cleanup
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `cwt create <branch>` | Create worktree with deps & tmux session |
| `cwt list` | List all worktrees with status |
| `cwt switch <name>` | Switch to worktree & attach tmux |
| `cwt delete <name>` | Delete worktree & cleanup |
| `cwt cleanup` | Remove stale worktrees |
| `cwt sync [--all]` | Sync worktree(s) with upstream |
| `cwt tmux create` | Create tmux session for worktree |
| `cwt tmux attach` | Attach to existing session |
| `cwt tmux list` | List worktree tmux sessions |

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
| `auto_start_claude` | `true` | Auto-start Claude Code in first pane |
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

Claude Orchestrator automatically detects your project type and package manager:

| Project Type | Detected By | Package Manager Priority |
|-------------|-------------|-------------------------|
| Python | `pyproject.toml`, `requirements.txt` | uv > poetry > pipenv > pip |
| Node.js | `package.json` | bun > pnpm > yarn > npm |
| Rust | `Cargo.toml` | cargo |
| Go | `go.mod` | go |
| PHP | `composer.json` | composer |

## Claude Code Integration

### Slash Commands

Claude Orchestrator provides slash commands for use within Claude Code:

- `/worktree` - Main command wrapper
- `/wt-create` - Quick create shortcut
- `/wt-list` - List with formatting
- `/wt-cleanup` - Cleanup stale worktrees

### Hooks

Claude Orchestrator can inject worktree context into your Claude Code sessions via hooks:

```json
{
  "hooks": {
    "UserPromptSubmit": "python scripts/context-injector.py"
  }
}
```

## Development

### Setup development environment

```bash
# Clone and install with dev dependencies
git clone https://github.com/yourusername/claude-orchestrator.git
cd claude-orchestrator
uv pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=claude_orchestrator

# Run linting
ruff check .

# Run type checking
mypy src/claude_orchestrator
```

### Project Structure

```
claude-orchestrator/
├── src/claude_orchestrator/
│   ├── __init__.py
│   ├── cli.py                     # Main CLI entry point
│   ├── config.py                  # Configuration management
│   ├── core/
│   │   ├── worktree.py            # Git worktree operations
│   │   ├── project_detector.py    # Project type detection
│   │   ├── environment.py         # Dependency & .env setup
│   │   ├── tmux_manager.py        # tmux session management
│   │   └── cleanup.py             # Worktree cleanup/maintenance
│   ├── models/
│   │   ├── worktree_info.py       # Pydantic models
│   │   └── project_config.py      # Configuration models
│   └── utils/
│       ├── git_utils.py           # Git helper functions
│       ├── path_utils.py          # Path utilities
│       └── logger.py              # Structured logging
├── tests/
├── .claude/
│   ├── commands/                  # Claude Code slash commands
│   └── settings.json              # Hooks configuration
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

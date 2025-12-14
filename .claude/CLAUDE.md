# Claude Orchestrator

Git Worktree + Claude Code orchestration tool for parallel development workflows.

## Quick Commands

| Command | Description |
|---------|-------------|
| `cwt create <branch>` | Create worktree with tmux session and Claude Code |
| `cwt list` | List all worktrees with status |
| `cwt switch <name> --tmux` | Switch to worktree's tmux session |
| `cwt send <name> "cmd"` | Send command to another worktree's Claude |
| `cwt delete <name>` | Delete worktree and its tmux session |
| `cwt cleanup` | Remove stale worktrees (dry-run by default) |
| `cwt sync --all` | Sync all worktrees with upstream |

## tmux Commands

| Command | Description |
|---------|-------------|
| `cwt tmux create <name> -d <dir>` | Create tmux session |
| `cwt tmux attach <name>` | Attach to session |
| `cwt tmux list` | List cwt sessions |
| `cwt tmux kill <name>` | Kill session |

## Slash Commands

- `/worktree` - Main worktree management command
- `/wt-create` - Quick worktree creation
- `/wt-list` - List worktrees
- `/wt-cleanup` - Cleanup stale worktrees

## Development

```bash
# Install in development mode
uv pip install -e .

# Run tests
uv run pytest

# Run specific test
uv run pytest tests/test_worktree.py -v

# Type checking
uv run mypy src/

# Linting
uv run ruff check src/
```

## Project Structure

```
src/claude_orchestrator/
├── cli.py              # CLI entry point (click)
├── core/
│   ├── worktree.py     # Git worktree operations
│   ├── tmux_manager.py # tmux session management
│   ├── project_detector.py  # Project type detection
│   ├── environment.py  # Dependency & .env setup
│   ├── cleanup.py      # Worktree cleanup service
│   └── sync.py         # Upstream sync service
└── models/
    ├── worktree_info.py    # Worktree models
    ├── project_config.py   # Project config models
    └── maintenance.py      # Cleanup/sync models
```

## Guidelines

- Follow Python guidelines at `~/.claude/guidelines/python-guidelines.md`
- Use type hints (Python 3.10+ syntax)
- Use `rich` for terminal output
- Use `click` for CLI commands
- Use `pydantic` for data models

## Key Patterns

### Creating a worktree
```python
from claude_orchestrator.core import WorktreeManager

manager = WorktreeManager()
worktree = manager.create(branch="feature/new-feature", base_branch="main")
```

### Managing tmux sessions
```python
from claude_orchestrator.core import TmuxManager, TmuxLayout

tmux = TmuxManager()
session = tmux.create_worktree_session(
    worktree_name="my-feature",
    worktree_path="/path/to/worktree",
    layout=TmuxLayout.THREE_PANE,
    auto_start_claude=True
)
```

### Detecting project type
```python
from claude_orchestrator.core import ProjectDetector

detector = ProjectDetector()
config = detector.detect("/path/to/project")
print(f"Type: {config.project_type}, Manager: {config.package_manager}")
```

# Open Orchestrator

Git Worktree + Claude Code orchestration tool for parallel development workflows.

## Quick Commands

| Command | Description |
|---------|-------------|
| `owt create <branch>` | Create worktree with tmux session and Claude Code |
| `owt list` | List all worktrees with status |
| `owt switch <name> --tmux` | Switch to worktree's tmux session |
| `owt send <name> "cmd"` | Send command to another worktree's Claude |
| `owt status` | Show Claude activity across all worktrees |
| `owt delete <name>` | Delete worktree and its tmux session |
| `owt cleanup` | Remove stale worktrees (dry-run by default) |
| `owt sync --all` | Sync all worktrees with upstream |

## tmux Commands

| Command | Description |
|---------|-------------|
| `owt tmux create <name> -d <dir>` | Create tmux session |
| `owt tmux attach <name>` | Attach to session |
| `owt tmux list` | List owt sessions |
| `owt tmux kill <name>` | Kill session |

## Slash Commands

- `/worktree` - Main worktree management command
- `/wt-create` - Quick worktree creation
- `/wt-list` - List worktrees
- `/wt-status` - Show Claude activity across worktrees
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
src/open_orchestrator/
├── cli.py              # CLI entry point (click)
├── core/
│   ├── worktree.py     # Git worktree operations
│   ├── tmux_manager.py # tmux session management
│   ├── project_detector.py  # Project type detection
│   ├── environment.py  # Dependency & .env setup
│   ├── cleanup.py      # Worktree cleanup service
│   ├── sync.py         # Upstream sync service
│   └── status.py       # Claude activity status tracking
└── models/
    ├── worktree_info.py    # Worktree models
    ├── project_config.py   # Project config models
    ├── maintenance.py      # Cleanup/sync models
    └── status.py           # Claude status models
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
from open_orchestrator.core import WorktreeManager

manager = WorktreeManager()
worktree = manager.create(branch="feature/new-feature", base_branch="main")
```

### Managing tmux sessions
```python
from open_orchestrator.core import TmuxManager, TmuxLayout

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
from open_orchestrator.core import ProjectDetector

detector = ProjectDetector()
config = detector.detect("/path/to/project")
print(f"Type: {config.project_type}, Manager: {config.package_manager}")
```

### Tracking Claude status
```python
from open_orchestrator.core import StatusTracker
from open_orchestrator.models import ClaudeActivityStatus

tracker = StatusTracker()

# Initialize status for a worktree
tracker.initialize_status(
    worktree_name="my-feature",
    worktree_path="/path/to/worktree",
    branch="feature/my-feature",
    tmux_session="owt-my-feature"
)

# Update what Claude is working on
tracker.update_task("my-feature", "Implementing auth flow", ClaudeActivityStatus.WORKING)

# Get summary of all worktrees
summary = tracker.get_summary()
print(f"Active: {summary.active_claudes}, Blocked: {summary.blocked_claudes}")
```

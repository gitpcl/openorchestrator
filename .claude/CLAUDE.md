# Open Orchestrator

Git Worktree + Claude Code orchestration tool for parallel development workflows with on-demand workspace mode (dmux-like).

## Quick Commands

| Command | Description |
|---------|-------------|
| `owt create <branch>` | Create worktree with tmux session and Claude Code |
| `owt create <branch> --plan-mode` | Create worktree with Claude in plan mode |
| `owt pane add --branch <name>` | Add worktree pane on demand (also via `prefix+n`) |
| `owt pane remove --worktree <name>` | Remove pane + delete worktree (also via `prefix+X`) |
| `owt list` | List all worktrees with status |
| `owt switch <name> --tmux` | Switch to worktree's tmux session |
| `owt send <name> "cmd"` | Send command to another worktree's Claude |
| `owt status` | Show Claude activity across all worktrees |
| `owt delete <name>` | Delete worktree and its tmux session |
| `owt cleanup` | Remove stale worktrees (dry-run by default) |
| `owt cleanup --json` | Output cleanup report in JSON format |
| `owt sync --all` | Sync all worktrees with upstream |
| `owt sync --all --json` | Output sync report in JSON format |
| `owt pr link <worktree> --pr <num>` | Link worktree to GitHub PR |
| `owt pr status <worktree>` | Show PR status for worktree |
| `owt hooks list` | List configured status change hooks |
| `owt hooks add` | Add a new status change hook |
| `owt copy-session <src> <dest>` | Copy Claude session to new worktree |
| `owt resume <worktree>` | Get command to resume Claude session |
| `owt completion install` | Install shell auto-completion |
| `owt dashboard` | Launch live TUI dashboard |
| `owt tokens show` | Show token usage across worktrees |
| `owt process start <wt>` | Start AI tool without tmux |
| `owt process list` | List running AI tool processes |
| `owt skill install` | Install Claude Code skill (symlink) |
| `owt skill install --copy` | Install Claude Code skill (copy) |
| `owt skill status` | Check skill installation status |
| `owt skill uninstall` | Remove Claude Code skill |

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
├── config.py           # Configuration management
├── core/
│   ├── worktree.py     # Git worktree operations
│   ├── tmux_manager.py # tmux session management
│   ├── tmux_cli.py     # tmux CLI commands
│   ├── project_detector.py  # Project type detection
│   ├── environment.py  # Dependency, .env & CLAUDE.md setup
│   ├── cleanup.py      # Worktree cleanup service
│   ├── sync.py         # Upstream sync service
│   ├── status.py       # Claude activity status tracking
│   ├── hooks.py        # Status change hooks (notifications, webhooks)
│   ├── session.py      # Claude session copying & resume
│   ├── pr_linker.py    # GitHub PR linking integration
│   ├── process_manager.py  # Non-tmux process management
│   ├── dashboard.py    # Live TUI dashboard
│   └── skill_installer.py  # Claude Code skill installation
├── models/
│   ├── worktree_info.py    # Worktree models
│   ├── project_config.py   # Project config models
│   ├── maintenance.py      # Cleanup/sync models
│   ├── status.py           # Claude status models
│   ├── hooks.py            # Hook configuration models
│   ├── session.py          # Session data models
│   └── pr_info.py          # PR info models
├── popup/
│   └── picker.py           # Popup picker for on-demand pane creation (tmux display-popup)
├── skills/
│   └── open-orchestrator/
│       └── SKILL.md        # Claude Code skill definition
└── utils/
    └── io.py               # Safe file I/O utilities
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

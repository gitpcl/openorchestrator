# Open Orchestrator

Git Worktree + AI agent orchestration tool for parallel development workflows with curses-based switchboard UI.

## Commands (11 total)

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Switchboard** — the default experience |
| `owt new "task"` | `owt n` | Create worktree + tmux session + deps + AI agent. One command. |
| `owt list` | `owt ls` | Quick text list of worktrees (non-interactive, for scripts/pipes) |
| `owt switch <name>` | `owt s` | Jump to a worktree's tmux session |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt merge <name>` | `owt m` | Two-phase merge + auto-cleanup worktree + tmux session |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + tmux session + status |
| `owt sync [--all]` | | Sync worktree(s) with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees (dry-run by default) |
| `owt version` | `-v` | Show version |

## Slash Commands

- `/wt-create` - Quick worktree creation
- `/wt-list` - List worktrees
- `/wt-status` - Show AI activity across worktrees
- `/wt-cleanup` - Cleanup stale worktrees

## Development

```bash
uv pip install -e .
uv run pytest
uv run ruff check src/
uv run mypy src/
```

## Project Structure

```
src/open_orchestrator/
├── cli.py              # CLI entry point (click, ~660 LOC)
├── config.py           # Configuration management (~300 LOC)
├── core/
│   ├── worktree.py     # Git worktree operations
│   ├── tmux_manager.py # tmux session management (SINGLE + MAIN_VERTICAL layouts)
│   ├── switchboard.py  # Curses-based card grid UI (the killer feature)
│   ├── project_detector.py  # Project type detection
│   ├── environment.py  # Dependency, .env & CLAUDE.md setup
│   ├── cleanup.py      # Worktree cleanup service
│   ├── sync.py         # Upstream sync service
│   ├── status.py       # AI activity status tracking
│   ├── branch_namer.py # Branch name generation from task descriptions
│   ├── merge.py        # Two-phase merge logic
│   ├── pane_actions.py # Shared pane lifecycle (create/remove orchestration)
│   └── agent_detector.py  # Detect installed AI coding tools
├── models/
│   ├── worktree_info.py    # Worktree models
│   ├── project_config.py   # Project config models
│   ├── maintenance.py      # Cleanup/sync models
│   └── status.py           # AI status models
├── popup/
│   └── picker.py           # Popup picker for pane creation (tmux display-popup)
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
- Dependencies: click, pydantic, rich, toml, gitpython, libtmux (6 total)

## Key Patterns

### Creating a worktree
```python
from open_orchestrator.core.worktree import WorktreeManager

manager = WorktreeManager()
worktree = manager.create(branch="feature/new-feature", base_branch="main")
```

### Managing tmux sessions
```python
from open_orchestrator.core.tmux_manager import TmuxManager

tmux = TmuxManager()
session = tmux.create_worktree_session(
    worktree_name="my-feature",
    worktree_path="/path/to/worktree",
)
```

### Tracking AI status
```python
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.models.status import AIActivityStatus

tracker = StatusTracker()
tracker.initialize_status(
    worktree_name="my-feature",
    worktree_path="/path/to/worktree",
    branch="feature/my-feature",
    tmux_session="owt-my-feature"
)
tracker.update_task("my-feature", "Implementing auth flow", AIActivityStatus.WORKING)
summary = tracker.get_summary()
```

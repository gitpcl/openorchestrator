# Open Orchestrator

Git Worktree + AI agent orchestration tool for parallel development workflows with Textual switchboard UI.

## Commands (28 total)

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Switchboard** — the default experience |
| `owt new "task"` | `owt n` | Create worktree + tmux session + deps + AI agent. One command. |
| `owt new "task" --headless` | | Create worktree without tmux (CI/script use) |
| `owt list` | `owt ls` | Quick text list of worktrees (non-interactive, for scripts/pipes) |
| `owt switch <name>` | `owt s` | Jump to a worktree's tmux session |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt send --all "msg"` | | Broadcast to ALL worktrees |
| `owt send --working "msg"` | | Broadcast to WORKING worktrees only |
| `owt merge <name>` | `owt m` | Two-phase merge + conflict guard + auto-cleanup (`--rebase`, `--strategy`, `--leave-conflicts`) |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + tmux session + status |
| `owt queue` | | Show optimal merge order for completed worktrees |
| `owt queue --ship` | | Ship all completed worktrees in optimal order |
| `owt plan "goal"` | | AI-powered task decomposition into dependency DAG |
| `owt plan "goal" --start` | | Plan + start orchestrator in one shot |
| `owt batch tasks.toml` | | Autopilot: run batch tasks from TOML file (now with DAG support) |
| `owt orchestrate plan.toml` | | Orchestrate plan into feature branch with coordination |
| `owt orchestrate --resume` | | Resume orchestrator from saved state |
| `owt orchestrate --stop` | | Graceful stop (worktrees kept) |
| `owt orchestrate --status` | | Show orchestrator progress |
| `owt wait <name>` | | Poll until agent finishes (for CI/scripts) |
| `owt note "msg"` | | Share context across all agent sessions |
| `owt sync [--all]` | | Sync worktree(s) with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees (dry-run by default) |
| `owt config validate` | | Validate configuration file |
| `owt config show` | | Display effective config as TOML |
| `owt db purge [--days N]` | | Delete messages older than N days (default 30) |
| `owt db vacuum` | | Optimize and compact the database |
| `owt db health [--check]` | | Database health diagnostics with CI thresholds |
| `owt doctor [--fix]` | | Diagnose and fix orphaned resources |
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
├── cli.py              # CLI entry point + global options
├── config.py           # Hierarchical config (TOML) + schema validation
├── commands/           # Modular command registration
│   ├── worktree.py     # new, list, switch, delete
│   ├── agent.py        # send, wait, note, hook
│   ├── merge_cmds.py   # merge, ship, queue
│   ├── orchestrate_cmds.py  # plan, batch, orchestrate
│   ├── maintenance.py  # sync, cleanup, version
│   ├── config_cmd.py   # config validate/show
│   ├── db_cmd.py       # db purge/vacuum/health
│   └── doctor.py       # doctor diagnostic command
├── core/
│   ├── worktree.py     # Git worktree operations
│   ├── tmux_manager.py # tmux session management (SINGLE + MAIN_VERTICAL layouts)
│   ├── switchboard.py       # SwitchboardApp Textual app + card widgets
│   ├── switchboard_cards.py # Card data, constants, status detection, rendering
│   ├── switchboard_modals.py # Modal screens (input, confirm, detail, searchable select)
│   ├── switchboard_tmux.py  # Switchboard tmux session lifecycle + keybindings
│   ├── prompt_builder.py    # Context-aware prompt builder (task-type detection)
│   ├── tool_protocol.py     # AIToolProtocol + CustomTool (plugin interface)
│   ├── tool_registry.py     # Singleton tool registry (discover/register AI tools)
│   ├── project_detector.py  # Project type detection
│   ├── environment.py  # Dependency, .env & CLAUDE.md setup
│   ├── cleanup.py      # Worktree cleanup service
│   ├── sync.py         # Upstream sync service
│   ├── status.py       # AI activity status tracking (SQLite + WAL)
│   ├── branch_namer.py # Branch name generation from task descriptions
│   ├── intelligence.py # Agno intelligence layer (planner, quality gate, conflict resolver, coordinator)
│   ├── merge.py        # Two-phase merge logic + merge queue + conflict guard + AI resolution
│   ├── batch.py        # Autopilot loop + DAG scheduler + AI planner (Agno or subprocess)
│   ├── batch_models.py # Batch data models + Pydantic validation for TOML parsing
│   ├── orchestrator.py # Orchestrator agent (plan → execute → merge → feature branch)
│   ├── pane_actions.py # Shared pane lifecycle (create/remove orchestration)
│   ├── agent_detector.py  # Detect installed AI coding tools
│   └── mcp_peer.py    # MCP peer communication server (optional, FastMCP)
├── models/
│   ├── intelligence.py     # Agno structured output models (TaskPlan, QualityVerdict, etc.)
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
    ├── io.py               # Safe file I/O utilities
    ├── logging.py          # Structured logging (correlation IDs, JSON output)
    └── lazy.py             # LazyModule proxy for deferred imports
```

## Guidelines

- Follow Python guidelines at `~/.claude/guidelines/python-guidelines.md`
- Use type hints (Python 3.10+ syntax)
- Use `rich` for terminal output
- Use `click` for CLI commands
- Use `pydantic` for data models
- Dependencies: click, pydantic, rich, textual, toml, gitpython, libtmux (7 total + optional agno, mcp)

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
# Interactive session (user-facing)
session = tmux.create_worktree_session(
    worktree_name="my-feature",
    worktree_path="/path/to/worktree",
)
# Automated session (orchestrator/batch — runs prompt, exits when done)
session = tmux.create_worktree_session(
    worktree_name="my-feature",
    worktree_path="/path/to/worktree",
    auto_exit=True,
    prompt="Implement auth and commit when done",
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

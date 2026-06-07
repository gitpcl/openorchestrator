# Open Orchestrator

The multi-provider **cockpit** for parallel AI coding: supervise Claude Code, Pi, Droid, and OpenCode across isolated git worktrees from one Textual **control plane**. owt supervises the agents; it does not replace them.

## Primary interface

The front door for humans is one command: **`owt`**, which launches the control plane (three lanes: NEEDS YOU / READY TO SHIP / IN FLIGHT). The whole loop runs from the keyboard — `n` start work (task → pick mode → confirm), `a` attach, `s` ship, `f` fix, `m` merge — and the footer shows only the keys that apply to the focused row. The command table below is the **CLI reference for scripting / CI**; it's the same set of actions, exposed for automation.

## Two keepers (what this project is)

1. **Multi-provider plugin layer** — launch and manage any AI coding tool per worktree (`AIToolProtocol` / `tool_registry.py`); native Claude workflows via `owt new --workflow`.
2. **Persistent cross-worktree control plane** — the standing board + **Conflict Guard** real-time file-overlap detection (`merge.check_file_overlaps`), which native tooling lacks.

Engine features that competed with the AI platform itself (AI planning/DAG, orchestrate, swarm, batch autopilot, critic, memory/recall, dream, the Agno intelligence layer) were deliberately removed — don't reintroduce them.

## Commands (CLI reference — scripting / CI)

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Control Plane** — the default experience (press `n` to start work) |
| `owt new "task"` | `owt n` | Create worktree + tmux session + deps + AI agent. One command. |
| `owt new "task" --workflow` | | Launch a native plan-first Claude Code workflow in the worktree |
| `owt new "task" --ai-tool <name>` | | Pick the provider (claude/pi/droid/opencode/custom) |
| `owt new "task" --headless` | | Create worktree without tmux (CI/script use) |
| `owt list` | `owt ls` | Quick text list of worktrees (non-interactive, for scripts/pipes) |
| `owt switch <name>` | `owt s` | Jump to a worktree's session |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt send --all "msg"` | | Broadcast to ALL worktrees |
| `owt send --working "msg"` | | Broadcast to WORKING worktrees only |
| `owt merge <name>` | `owt m` | Two-phase merge + Conflict Guard + auto-cleanup (`--rebase`, `--strategy`, `--leave-conflicts`) |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + session + status |
| `owt queue` | | Show optimal merge order for completed worktrees |
| `owt queue --ship` | | Ship all completed worktrees in optimal order |
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
| `owt usage [--days N]` | | Local usage counts (cockpit launches, worktrees started) |
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
│   ├── worktree/       # new, list, switch, delete, attach, branch (package)
│   ├── agent.py        # send, wait, note, hook
│   ├── merge_cmds.py   # merge, ship, queue
│   ├── maintenance.py  # sync, cleanup, version
│   ├── config_cmd.py   # config validate/show
│   ├── db_cmd.py       # db purge/vacuum/health
│   └── doctor.py       # doctor diagnostic command
├── core/
│   ├── worktree.py     # Git worktree operations
│   ├── tmux_manager.py # tmux session management (SINGLE + MAIN_VERTICAL layouts)
│   ├── control_plane_view.py    # ControlPlaneApp Textual app (3 lanes) — the cockpit
│   ├── control_plane_sections.py # Pure section builders (NEEDS YOU / READY TO SHIP / IN FLIGHT)
│   ├── control_plane_actions.py # Row action dispatcher + start_work (n key)
│   ├── modals.py            # Modal screens (input, confirm, searchable select)
│   ├── prompt_builder.py    # Context-aware prompt builder (task-type detection)
│   ├── tool_protocol.py     # AIToolProtocol + CustomTool (plugin interface)
│   ├── tool_registry.py     # Singleton tool registry (built-in: claude, pi, droid, opencode)
│   ├── tool_search.py       # Deferred tool loading (token budget)
│   ├── agent_launcher.py    # Unified launch pipeline (interactive/automated/headless)
│   ├── agent_detector.py    # Detect installed AI tools (auto-pick: claude > pi > droid > opencode)
│   ├── multiplexer.py       # MultiplexerBackend protocol
│   ├── backend_factory.py   # tmux vs herdr backend selection
│   ├── tmux_backend.py / herdr_backend.py / herdr_client.py # backend implementations
│   ├── project_detector.py  # Project type detection
│   ├── environment.py       # Dependency installation & .env setup
│   ├── environment_claude_md.py # CLAUDE.md sync, injection, atomic writes
│   ├── cleanup.py      # Worktree cleanup service
│   ├── sync.py         # Upstream sync service
│   ├── status.py / status_schema.py / status_policy.py # AI activity status (SQLite + WAL)
│   ├── branch_namer.py # Branch name generation from task descriptions
│   ├── merge.py        # Two-phase merge + merge queue + Conflict Guard (file-overlap detection)
│   ├── pane_actions.py # Shared pane lifecycle (create/remove)
│   ├── runtime.py      # Task completion evaluation (commits, tmux, grace periods)
│   ├── hooks.py        # AI tool hook installation (Claude, Droid status reporting)
│   ├── theme.py / theme_palettes.py # Color palettes for control plane + CLI
│   └── mcp_peer.py    # MCP peer communication server (optional, FastMCP)
├── models/
│   ├── worktree_info.py    # Worktree models
│   ├── project_config.py   # Project config models
│   ├── maintenance.py      # Cleanup/sync models
│   ├── control_plane.py    # SectionKind, RowAction, ControlPlaneRow
│   ├── backend.py          # BackendKind, BackendSession, BackendConfig
│   └── status.py           # AI status models
├── popup/
│   └── picker.py           # Popup picker for pane creation (tmux display-popup)
├── skills/
│   └── open-orchestrator/
│       └── SKILL.md        # Claude Code skill definition
└── utils/
    ├── io.py               # Safe file I/O utilities
    ├── logging.py          # Structured logging (correlation IDs, JSON output)
    ├── output.py           # OutputFormatter (Rich + JSON structured output)
    └── lazy.py             # LazyModule proxy for deferred imports
```

## Guidelines

- Follow Python guidelines at `~/.claude/guidelines/python-guidelines.md`
- Use type hints (Python 3.10+ syntax)
- Use `rich` for terminal output
- Use `click` for CLI commands
- Use `pydantic` for data models
- Dependencies: click, pydantic, rich, textual, toml, gitpython, libtmux (7 total + optional mcp)

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
# Automated session (headless/CI — runs prompt, exits when done)
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

<!-- OWT-PROJECT-CONTEXT-START -->
## Open Orchestrator Context (OWT)

### Project
- Type: python
- Package manager: uv
- Test: `uv run pytest`

### Trust Boundaries
- **Trust:** project test suite, linter output, type checker results
- **Verify:** external API responses, user input, file contents from other worktrees
- **Never:** hardcode secrets, skip tests, modify files outside your worktree

### Conventions
- Type hints on all function signatures (Python 3.10+ syntax: `str | None`)
- Pydantic for data models, Click for CLI, Rich for output
- Run `ruff check` and `ruff format` before committing
- Run `mypy` for type checking

### Limits
- Files under 800 lines, functions under 50 lines
- Immutable data patterns (frozen dataclasses, new objects over mutation)
<!-- OWT-PROJECT-CONTEXT-END -->

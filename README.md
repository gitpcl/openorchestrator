# Open Orchestrator

[![CI](https://github.com/gitpcl/openorchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/gitpcl/openorchestrator/actions/workflows/ci.yml) [![License](https://img.shields.io/github/license/gitpcl/openorchestrator)](LICENSE)

A lean Git Worktree + AI agent orchestration tool for parallel development workflows. Coordinate multiple AI coding sessions across isolated branches from a Textual-based **control plane** — a prioritized decision surface (NEEDS YOU / READY TO SHIP / IN FLIGHT / BACKGROUND) where every row carries a verb action. Supports Claude Code, Pi, OpenCode, and Droid. Optional **herdr multiplexer backend** swaps the rendering surface; optional **Agno intelligence layer** adds AI-powered planning, quality gating, and merge conflict resolution; optional **MCP peer communication** enables agent-to-agent messaging and coordination.

## Overview

Open Orchestrator enables developers to work on multiple tasks simultaneously by creating isolated worktrees, each with its own AI coding session and tmux session. Start with `owt new "task description"` — it auto-generates a branch name, creates the worktree, installs dependencies, copies `.env`, and starts the AI tool. Run `owt` to launch the **control plane** — four prioritized sections, verb-per-row actions, empty sections hidden so you always see the most important thing first. The legacy card grid is still available behind `owt --legacy-cards` for one release.

![Open Orchestrator demo](assets/demo.gif)

> **Agent Teams vs Open Orchestrator:** [Claude Code's Agent Teams](https://code.claude.com/docs/en/agent-teams) coordinate multiple AI agents within the *same codebase*. Open Orchestrator manages multiple *isolated worktrees* (different branches, different directories, independent environments). They're complementary — use Agent Teams for intra-branch collaboration, Open Orchestrator for cross-branch orchestration.

## Features

- **40+ commands** — focused CLI surface, no bloat
- **Control Plane UI** — Textual sectioned decision surface (NEEDS YOU / READY TO SHIP / IN FLIGHT / BACKGROUND); each row exposes verb actions (`[s]hip`, `[r]eview`, `[a]ttach`, `[f]ix`, `[m]erge`, `[x] dismiss`); empty sections hidden
- **Pluggable multiplexer backends** — tmux by default, **[herdr](https://herdr.dev)** opt-in via `--herdr` or `[backend] mode = "herdr" | "auto"`; one `MultiplexerBackend` protocol, status forwarding to herdr's sidebar via `pane.report_agent` (non-fatal, SQLite stays source of truth)
- **Background event surface** — dream / memory / critic emit events into the control plane's BACKGROUND section so invisible work (consolidation, auto-pass verdicts, fact captures) is visible
- **Conflict Guard** — real-time file overlap detection between parallel agents; warns before merge when two branches touch the same files
- **AI-Powered Planning** — `owt plan "Build auth system"` decomposes a goal into a dependency-aware DAG, spawns agents in parallel, auto-injects parent context into child tasks
- **Orchestrator Agent** — `owt orchestrate` drives a plan end-to-end into a feature branch with coordination, user presence detection, and stop/resume
- **Patchable automated sessions** — orchestrated and batch agents run in live tmux-backed provider sessions, receive their task automatically, stay patchable via `owt attach`, and still export `OWT_AUTOMATED=1` so hooks can treat them as automation
- **MCP Peer Communication** — agents discover each other via `list_peers`, exchange messages via `send_message`/`check_messages`, and coordinate file edits via `get_peer_files` — all through native MCP tools backed by shared SQLite
- **Session init protocol** — agents receive a structured 6-step prompt (orient → explore → implement → test → verify → commit) based on Anthropic's harness design research, with project test/dev commands auto-injected
- **Retry + timeouts** — failed tasks retry once with failure context; 30-min default timeout prevents hung agents from blocking the DAG
- **Autopilot Loops** — `owt batch tasks.toml` runs Karpathy-style autonomous loops with DAG-aware scheduling
- **Agent Broadcast** — `owt send --all "Run tests"` fans out instructions to all active agents
- **Merge Queue** — `owt queue` shows optimal merge order; `owt queue --ship` ships all completed work intelligently
- **Context Bridge** — `owt note "msg"` shares context across all agent sessions via CLAUDE.md injection
- **Memory System** — `owt memory add/search/consolidate/list/mine` stores persistent cross-worktree knowledge with auto-classification and grep-based transcript search
- **Recall** — SQLite + FTS5 backed structured fact store with a 4-layer token-budgeted stack (L0 identity / L1 critical / L2 topics / L3 search), AAAK shorthand compression for L1, a temporal knowledge graph with point-in-time queries, contradiction detection, and `owt memory mine` for git/progress/comment fact extraction. L0+L1 payload auto-injects into CLAUDE.md on every `owt new`. Pure stdlib `sqlite3` — zero new dependencies.
- **Swarm Mode** — `owt swarm start "goal" -w worktree` launches a coordinator + specialized workers (researcher, implementer, reviewer, tester) in tmux panes within one worktree. Role prompts enforce constraints (researcher/reviewer read-only, tester limited to `tests/`). `owt send --swarm <id> "msg"` broadcasts to every worker.
- **Critic Pattern** — `owt critic ship|merge|delete <name>` runs a pre-action safety review (file overlaps, uncommitted changes, empty branches, unmerged commits) with denial tracking — falls back to user confirmation after 3 consecutive or 20 total denials per session
- **Dream Mode** — `owt dream enable` starts a background daemon that periodically wakes to consolidate memory, surface stale worktrees, and detect knowledge-graph contradictions across worktrees; reports saved under `.owt/dream_reports/`
- **Multi-palette Theming** — auto-detects terminal background via OSC 11 (with `$COLORFGBG` fallback), four palettes (`dark`, `light`, `dark-ansi`, `light-ansi`); both control plane and legacy switchboard use native Textual `$variable` references for instant palette swaps; `--theme` global flag overrides detection
- **Headless Mode** — `owt new "task" --headless` for CI/CD; `owt wait` polls until agent finishes
- **One-command setup** — `owt new "task"` does everything: branch → worktree → deps → .env → tmux → AI tool
- **Quality Gate** — `owt ship` optionally runs AI quality review before merging (with Agno); checks code quality, cross-worktree conflicts
- **AI Conflict Resolution** — merge conflicts can be resolved semantically by an AI agent before falling back to manual resolution
- **Ship in one shot** — `owt ship` auto-commits, merges to main, and tears down worktree + session
- **Two-phase merge** — `owt merge` catches conflicts early with file overlap warnings, then auto-cleans. Supports `--rebase` for linear history, `--strategy ours|theirs` for auto-resolution, and `--leave-conflicts` for manual resolution
- **Full teardown** — `owt delete` kills tmux session + removes worktree + cleans status
- **Live status detection** — the status tracker + pane scraper detects when agents are waiting for input, blocked, or done; the control plane surfaces these as `NEEDS YOU` rows
- **Plugin Architecture** — register custom AI tools via config without code changes; built-in support for Claude, Pi, OpenCode, and Droid
- **Structured Logging** — correlation IDs, per-worktree context, and JSON output (`--log-format json`) for log aggregation
- **Task-Aware Prompts** — context-aware prompt builder with task-type detection (feature, bugfix, refactor, test, docs) and structured 5–6 step protocols per type
- **Diagnostics** — `owt doctor` finds orphaned worktrees/sessions/status entries; `owt config validate` checks config; `owt db health` reports database stats
- **Lazy Imports** — deferred heavy imports and `LazyModule` proxy for fast CLI startup
- **AI tool auto-detection** — detects Claude, Pi, OpenCode, Droid with picker when multiple found
- **Project detection** — auto-detects Python, Node.js, Rust, Go, PHP and installs deps
- **7 dependencies** — click, pydantic, rich, textual, toml, gitpython, libtmux (+ optional agno for intelligence, mcp for peer communication)

## Installation

### Requirements

- Python 3.10+
- Git
- tmux
- An AI coding tool (Claude Code, Pi, OpenCode, or Droid)

### Install from PyPI

```bash
pip install open-orchestrator

# With Agno intelligence layer (AI-powered planning, quality gate, conflict resolution)
pip install open-orchestrator[agno]

# With MCP peer communication (agent-to-agent messaging)
pip install open-orchestrator[mcp]

# Both
pip install open-orchestrator[agno,mcp]
```

### Install from source

```bash
git clone https://github.com/gitpcl/openorchestrator.git
cd openorchestrator
uv pip install -e .

# With optional features
uv pip install -e ".[agno]"        # Intelligence layer
uv pip install -e ".[mcp]"         # Peer communication
uv pip install -e ".[agno,mcp]"    # Both
```

## Quick Start

```bash
# Launch the control plane (default) — sectioned decision surface
owt

# Create a worktree with AI agent (one command does everything)
owt new "Add user authentication with JWT"

# Hand off to the agent's session (via the active backend — tmux or herdr)
owt attach auth-jwt
# Or press 'a' on the row in the control plane

# Interact from the CLI
owt send auth-jwt "Fix the failing tests"
owt switch auth-jwt    # Jump to that tmux session

# Ship when done (commit + merge + delete in one shot)
owt ship auth-jwt
# Or press 's' on the READY TO SHIP row in the control plane
```

## Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Control Plane** — prioritized sections with verb-per-row actions |
| `owt --legacy-cards` | | Launch the deprecated card-grid switchboard (one-release migration) |
| `owt new "task"` | `owt n` | Create worktree + tmux + deps + AI agent |
| `owt new "task" --headless` | | Create worktree without tmux (CI/script use) |
| `owt new "task" --herdr` | | Use the herdr multiplexer backend instead of tmux |
| `owt new "task" --tmux` | | Force tmux backend (override `[backend]` config) |
| `owt list` | `owt ls` | List worktrees with status |
| `owt switch <name>` | `owt s` | Jump to a worktree's tmux session |
| `owt attach <name>` | | Hand off to the worktree's session via the active backend (`--herdr` / `--tmux` to override) |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt send --all "msg"` | | Broadcast to ALL worktrees |
| `owt send --working "msg"` | | Broadcast to WORKING worktrees only |
| `owt merge <name>` | `owt m` | Two-phase merge + conflict guard + cleanup (`--rebase`, `--strategy`, `--leave-conflicts`) |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + tmux + status |
| `owt queue` | | Show optimal merge order for completed worktrees |
| `owt queue --ship` | | Ship all completed worktrees in optimal order |
| `owt plan "goal"` | | AI-powered task decomposition into dependency DAG |
| `owt plan "goal" --start` | | Plan + start orchestrator in one shot |
| `owt batch tasks.toml` | | Autopilot: run batch tasks from TOML (DAG-aware) |
| `owt orchestrate plan.toml` | | Orchestrate plan into feature branch with coordination |
| `owt orchestrate --resume` | | Resume orchestrator from saved state |
| `owt orchestrate --stop` | | Graceful stop (worktrees kept) |
| `owt orchestrate --status` | | Show orchestrator progress |
| `owt wait <name>` | | Poll until agent finishes (for CI/scripts) |
| `owt note "msg"` | | Share context across all agent sessions |
| `owt sync [--all]` | | Sync with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees |
| `owt config validate` | | Validate configuration file |
| `owt config show` | | Display effective config as TOML |
| `owt db purge [--days N]` | | Delete messages older than N days (default 30) |
| `owt db vacuum` | | Optimize and compact the database |
| `owt db health [--check]` | | Database health diagnostics with CI thresholds |
| `owt memory add "fact"` | | Store a fact with auto-classification |
| `owt memory search "q"` | | Search index, topics, and transcripts |
| `owt memory consolidate` | | Dedup, prune, and index untracked topics |
| `owt memory list` | | List all memory entries |
| `owt memory mine` | | Mine facts from git history, progress files, and code comments |
| `owt swarm start "goal" -w wt` | | Launch coordinator + specialist workers in a worktree |
| `owt swarm list` | | List all active swarms |
| `owt swarm send <id> "msg"` | | Broadcast a message to all workers in a swarm |
| `owt swarm stop <id>` | | Stop a swarm and kill its worker panes |
| `owt critic ship|merge|delete <name>` | | Pre-action safety review (overlaps, uncommitted, empty branch) |
| `owt dream enable` | | Start the background dream daemon |
| `owt dream disable` | | Stop the dream daemon |
| `owt dream status` | | Show daemon state and last heartbeat |
| `owt dream consolidate` | | Run consolidation immediately |
| `owt dream reports` | | List recent dream reports |
| `owt doctor [--fix]` | | Diagnose and fix orphaned resources |
| `owt --theme <name>` | | Override UI theme (auto, dark, light, dark-ansi, light-ansi) |
| `owt --json <cmd>` | | Machine-readable JSON output for `list`, `queue`, `doctor`, `db health` |
| `owt version` | | Show version |

## The Control Plane

Running `owt` with no arguments launches the **control plane** — a prioritized decision surface. Four sections render top-to-bottom in priority order; empty sections are hidden so you always see the most important thing first.

```
  open-orchestrator · 5 rows · 14:32:08
  ▸ NEEDS YOU      (1)
  ▶ auth-jwt        merge conflict — needs manual resolution   [f] [a]
  ▸ READY TO SHIP  (2)
    fix-login       +3 commits · queued #1/2                   [s] [r] [a]
    docs-update     +1 commits · queued #2/2                   [s] [r] [a]
  ▸ IN FLIGHT      (1)
    api-refactor    45m · opencode · Refactoring REST routes   [a] [r]
  ▸ BACKGROUND     (1)
    14:20 dream     consolidated · memory=3 stale=0            [x]

  ↑↓ nav | s ship | r review | a attach | f fix | m merge | x dismiss | q quit
```

**Sections (priority order):**
- **NEEDS YOU** — merge conflicts, critic-blocking verdicts, BLOCKED/ERROR status
- **READY TO SHIP** — completed worktrees in optimal merge order with the `[s]hip` action
- **IN FLIGHT** — WORKING agents with elapsed time + last task message
- **BACKGROUND** — recent dream / memory / critic events (≤10, newest first); `[x]` to dismiss

**Row verbs:**

| Key | Action | Where it applies |
|-----|--------|------------------|
| `s` | ship (commit + merge + delete via confirm) | READY TO SHIP |
| `r` | review (inline critic verdict panel) | NEEDS YOU, READY TO SHIP, IN FLIGHT |
| `a` | attach (hand off via active backend) | every section except BACKGROUND |
| `f` | fix (open conflicted files in `$EDITOR`) | NEEDS YOU |
| `m` | merge (without ship's cleanup) | READY TO SHIP |
| `x` | dismiss | BACKGROUND |

**Navigation:** `↑/↓` or `j/k` for previous/next row across sections; `q` to quit; `Esc` closes the inline review panel.

**Header bar:** when `owt orchestrate` is active, the header shows DAG progress (`X/Y done · Z running`). Otherwise it shows the project name, row count, and a clock.

**Architecture:** section builders are pure functions in `core/control_plane_sections.py` (fully testable without a Textual Pilot); the action dispatcher in `core/control_plane_actions.py` is a `(SectionKind, RowAction) → coroutine` table; the view in `core/control_plane_view.py` is dumb — it only knows about rows and key presses.

## Multiplexer Backends (tmux / herdr)

By default owt uses **tmux** to host agent sessions. You can opt in to **[herdr](https://herdr.dev)** as the multiplexer backend — owt becomes the orchestration brain, herdr the rendering surface. tmux remains the default; herdr is purely additive.

```bash
# one-off
owt new "Refactor billing" --herdr
owt attach my-feature --herdr

# project-wide via .worktreerc.toml
[backend]
mode = "auto"               # tmux | herdr | auto
herdr_session = "default"   # named herdr session (selects which socket)
```

**Selection precedence:**
1. `--herdr` / `--tmux` on the command line (per invocation)
2. `[backend] mode` in `.worktreerc.toml`
3. `tmux` as the safe default

`mode = "auto"` picks herdr when installed and reachable, otherwise tmux. Status updates from owt's tracker are forwarded to herdr's sidebar via `pane.report_agent` (non-fatal — SQLite is source of truth).

**What owt sends to herdr:**

| owt action            | herdr RPC                              |
|-----------------------|----------------------------------------|
| `owt new --herdr`     | `workspace.create` + `pane.send_text`  |
| `owt send`            | `pane.send_text`                       |
| `owt attach --herdr`  | `herdr agent attach <pane_id>` (exec)  |
| status update         | `pane.report_agent` (non-fatal)        |
| `owt delete`          | `pane.close` + `workspace.close`       |

**Architecture:** call sites depend only on `core/multiplexer.py::MultiplexerBackend`; concrete adapters live behind `core/tmux_backend.py` (wraps `TmuxManager`) and `core/herdr_backend.py` (wraps `HerdrClient` JSON-RPC over Unix socket). The factory at `core/backend_factory.py` is the single resolution point.

See [`docs/herdr-integration.md`](docs/herdr-integration.md) for the full configuration, troubleshooting, named-session walkthrough, and protocol reference.

## Legacy Switchboard (`--legacy-cards`)

`owt --legacy-cards` launches the original card-grid switchboard. It will be removed in the next minor release; a deprecation banner is printed on every legacy invocation.

```
  SWITCHBOARD (legacy) · 4  ●3  ○1

  ┌─ auth-jwt ──────────────┐   ┌─ fix-login ──────────────┐
  │ ● WORKING        12m    │   │ ○ IDLE              3h    │
  │ feat/auth-jwt           │   │ fix/login-redirect        │
  │ claude        +142 -37  │   │ claude                    │
  │ Implementing JWT auth   │   │ —                         │
  └─────────────────────────┘   └───────────────────────────┘

  legacy · ↑↓←→ nav · Enter patch · n new · S ship · q quit
```

Keys: arrows nav, `Enter` patch into tmux, `s` send, `a` broadcast, `n` new, `S` ship, `m` merge, `d` delete, `q` quit. Detail/info modals were removed in Sprint 024 — `f` (overlap) and `i` (info) now surface as toasts.

**Global tmux keybindings (work from any agent session):**
- **Alt+s** — switch back to the switchboard
- **Alt+m** — merge current worktree
- **Alt+d** — delete current worktree
- **Alt+c** — create a new worktree (opens popup)

## Workflow Templates

Three built-in templates for common workflows:

```bash
owt new "Add payments" --template feature   # Plan mode, TDD workflow
owt new "Fix crash" --template bugfix       # Root cause focus, minimal changes
owt new "Patch CVE" --template hotfix       # Emergency, production stability
```

## Agno Intelligence Layer (Optional)

Install with `pip install open-orchestrator[agno]` to enable AI-powered intelligence features. Without it, everything works exactly as before — all three features gracefully degrade.

### Intelligent Planner

`owt plan` uses an Agno agent with codebase awareness — it reads the file tree and git history to produce better task decompositions with Pydantic-validated structured output (no regex parsing). Falls back to subprocess-based planning if Agno is not installed.

### Quality Gate

`owt ship` runs an AI quality review before merging. Checks for:
- Code completeness (TODOs, partial implementations, debug code)
- Security issues (hardcoded secrets, injection vulnerabilities)
- Cross-worktree conflicts (files modified by other active agents)

If the quality gate flags issues, you're prompted to ship anyway or abort. Skipped with `--yes`.

### Merge Conflict Resolution

When `auto_resolve_conflicts = true` in config, merge conflicts are resolved semantically by an AI agent before falling back to manual resolution. Only applies resolved content when confidence exceeds 0.8.

### Cross-Worktree Coordination

The orchestrator detects file overlaps between running worktrees and injects context into each agent's CLAUDE.md. With Agno, a coordinator agent generates intelligent, targeted messages. Without Agno, template-based warnings are used. Coordination runs on a 120s cooldown per event to avoid noise.

### Agno Configuration

```toml
[agno]
enabled = true                           # Toggle intelligence features
model_id = "claude-sonnet-4-20250514"    # Default model (Claude, OpenAI, Gemini)
planner_model_id = "claude-sonnet-4-20250514"  # Override for planner
quality_gate_model_id = "claude-sonnet-4-20250514"  # Override for gate
coordinator_model_id = "claude-haiku-4-5-20251001"  # Cost-effective for coordination
quality_gate_threshold = 0.7             # Minimum score to pass (0.0-1.0)
auto_resolve_conflicts = false           # Auto-apply AI conflict resolutions
```

API keys use standard env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) — no OWT-specific config needed.

## MCP Peer Communication (Optional)

Install with `pip install open-orchestrator[mcp]` to enable agent-to-agent communication via MCP. Each agent's Claude Code session gets an MCP server providing peer discovery and messaging tools.

### How It Works

When `owt new` creates a worktree, an `owt-peers` MCP server config is injected into `.claude/settings.local.json`. Claude Code spawns the server process (stdio), which reads/writes to the shared SQLite database. No broker daemon needed — all coordination happens through the existing `status.db` with WAL mode.

```
Agent A (feat/auth)              Agent B (feat/api)
     |                                |
  Claude Code                     Claude Code
     |                                |
  MCP Server (stdio)             MCP Server (stdio)
     |                                |
     +-------> status.db <------------+
```

### Agent Tools

| Tool | Purpose |
|------|---------|
| `list_peers` | Discover active agents (name, branch, status, summary) |
| `send_message` | Send to a peer (`to_peer="*"` broadcasts) |
| `check_messages` | Read unread messages from peers |
| `set_summary` | Update visible status for coordination |
| `get_peer_files` | Check what files a peer is editing |

### Example Agent Conversation

```
Agent A (auth-jwt):
  list_peers() → [{name: "api-refactor", branch: "refactor/api-v2", status: "working"}]
  send_message("api-refactor", "I'm adding auth middleware to server.py — are you touching it?")

Agent B (api-refactor):
  check_messages() → [{from: "auth-jwt", message: "...are you touching it?"}]
  send_message("auth-jwt", "No, only routes.py and models.py. Go ahead.")
```

Gracefully degrades — if MCP SDK is not installed, worktrees are created without the peer server config. Claude Code handles missing MCP servers without errors.

## Configuration

Config files are loaded in priority order:
1. `.worktreerc` in current directory
2. `.worktreerc.toml`
3. `~/.config/open-orchestrator/config.toml`
4. `~/.worktreerc`

```toml
[worktree]
base_directory = "../"
auto_cleanup_days = 14

[tmux]
auto_start_ai = true
ai_tool = "claude"        # claude, pi, opencode, droid
mouse_mode = true

[environment]
auto_install_deps = true
copy_env_file = true

[switchboard]
background_color = "#1a1b2e"  # match your terminal background (auto-detected if omitted)

[backend]
mode = "tmux"                  # tmux | herdr | auto — picks the multiplexer backend
herdr_session = "default"      # named herdr session (selects which socket)
# herdr_socket = "/custom/path/to/herdr.sock"   # override socket location
```

### Environment Variables

| Variable | Set by | Purpose |
|----------|--------|---------|
| `OWT_AUTOMATED` | OWT (in orchestrated panes) | Lets user hooks distinguish automated agents from interactive sessions. Check `[ -n "$OWT_AUTOMATED" ]` in hooks to skip restrictions for agents. |
| `OWT_WORKTREE_NAME` | OWT (in all panes) | Current worktree name. Used by MCP peer servers and hooks for identification. |
| `OWT_DB_PATH` | OWT hook/MCP wiring or user override | Points hooks, MCP peer servers, and in-process status tracking at the same SQLite DB. If `~/.open-orchestrator/status.db` is not writable, orchestrator/batch fall back to repo-local or temp-backed storage. |
| `OWT_RECALL_DB_PATH` | User override | Override the recall memory store SQLite path (defaults to `~/.open-orchestrator/recall.db`). |
| `OWT_BACKGROUND` | OWT (auto-detected) or user override | Terminal background hex color for the legacy switchboard. Auto-detected via OSC 11 at launch; set manually if detection fails. |

## AI Tool Support

Open Orchestrator auto-detects installed AI tools and offers a picker when multiple are found:

| Tool | Binary | Notes |
|------|--------|-------|
| Claude Code | `claude` | Default, `--dangerously-skip-permissions`; orchestrated agents use `-p` with cat-piped prompts |
| Pi | `pi` | `npm install -g @earendil-works/pi-coding-agent`; orchestrated agents use `-p` with cat-piped prompts; live status via pane scraping |
| OpenCode | `opencode` | Go-based |
| Droid | `droid` | `--skip-permissions-unsafe` by default |

Auto-pick priority when multiple are installed: `claude > pi > droid > opencode`.

```bash
owt new "task" --ai-tool claude --plan-mode
owt new "task" --ai-tool pi
owt new "task" --ai-tool opencode
owt new "task" --ai-tool droid
```

### Branch Mode (No Worktree)

For quick tasks where a full clone is overkill, use `--in-place` or the `owt branch` alias:

```bash
# Create a branch in the current checkout (faster, zero extra disk)
owt branch "Fix login bug"
owt new "Add tests" --in-place

# All lifecycle commands auto-detect branch mode
owt merge fix-login-bug
owt ship fix-login-bug

# Control plane shows branch sessions in IN FLIGHT just like worktree sessions
owt
```

### Custom AI Tools

Register any AI coding tool via config — no code changes needed:

```toml
[tools.mytool]
binary = "my-ai-tool"
command_template = "{binary} --interactive"
prompt_flag = "-p"
supports_hooks = false
install_hint = "Install from https://..."
known_paths = ["~/.local/bin/mytool"]
```

## Project Detection

Automatically detects project type and installs dependencies:

| Type | Detection | Package Manager |
|------|-----------|----------------|
| Python | `pyproject.toml`, `uv.lock`, `requirements.txt` | uv > poetry > pipenv > pip |
| Node.js | `package.json`, `bun.lockb`, `pnpm-lock.yaml` | bun > pnpm > yarn > npm |
| Rust | `Cargo.toml` | cargo |
| Go | `go.mod` | go |
| PHP | `composer.json` | composer |

## Common Patterns

### Parallel Feature Development
```bash
owt new "Build Stripe integration"
owt new "Write payment tests"
owt new "Add payment docs"
# -> Three agents working in parallel, visible in the control plane's IN FLIGHT section
# -> Conflict Guard warns if agents touch the same files; conflicts surface in NEEDS YOU
```

### AI-Powered Planning (DAG Execution)
```bash
owt plan "Build JWT auth with refresh tokens and admin dashboard"
# -> AI decomposes into dependency-aware tasks, saves plan.toml

owt plan "Add auth" --start --branch feat/auth-v2
# -> Generate plan + orchestrate into feature branch

owt plan "Add rate limiting" --execute
# -> Generate plan + run in batch mode (ships to main)

owt plan "Fix auth bugs" --execute --auto-ship
# -> Generate plan + run + auto-merge completed tasks
```

Tasks with dependencies run in topological order. Independent tasks run in parallel. Parent task context (git log summaries) is auto-injected into child worktrees' CLAUDE.md.

### Orchestrator (Feature Branch Mode)
```bash
# Plan + start orchestration in one shot
owt plan "Add JWT auth" --start --branch feat/auth-v2

# Or plan first, orchestrate later
owt plan "Add JWT auth"                              # generates plan.toml
owt orchestrate plan.toml --branch feat/auth-v2      # starts orchestration

# Control the orchestrator
owt orchestrate --resume                              # resume from saved state
owt orchestrate --stop                                # graceful stop
owt orchestrate --status                              # show progress

# User jumps in to help (orchestrator pauses that worktree)
owt switch auth-models
# -> orchestrator detects user, skips auto-actions on auth-models
# -> user leaves → orchestrator resumes coordination

# When all tasks complete:
# "All 5 tasks merged into feat/auth-v2. Ready for review."
# User opens PR: feat/auth-v2 → main
```

The orchestrator merges completed tasks into a **feature branch** (not main), persists state for stop/resume, detects user presence to pause auto-actions, and coordinates agents when file overlaps are detected (Agno or template fallback). Orchestrated and batch agents start as live provider sessions, receive the structured session-init prompt through the active multiplexer backend, and remain patchable via `owt attach` (which routes through tmux or herdr). The shared runtime evaluator watches both hook updates and pane state to detect waiting, blocked, exited, and silent-failure cases. Safety nets: auto-commits uncommitted work, optional quality gate, empty-branch guard, retry with failure context, and per-task timeouts (30 min default).

### Overnight Autopilot (Batch Mode)
```toml
# tasks.toml — now supports dependency DAGs
[batch]
max_concurrent = 3
auto_ship = true
min_agent_runtime = 60

[[tasks]]
id = "models"
description = "Create User and Token models"
depends_on = []

[[tasks]]
id = "auth-api"
description = "Build auth endpoints"
depends_on = ["models"]

[[tasks]]
id = "auth-tests"
description = "Write auth integration tests"
depends_on = ["auth-api"]
```
```bash
owt batch tasks.toml --auto-ship
# -> Respects dependency order, injects parent context
# -> Auto-ships completed work, starts next task
```

### Broadcasting Instructions
```bash
owt send --all "Run tests and fix any failures"
owt send --working "Wrap up and commit your changes"
# In the legacy switchboard (--legacy-cards), press 'a' to broadcast
```

### Sharing Context Across Agents
```bash
owt note "The users table now has a verified_at column"
owt note "API endpoints moved from /api/v1 to /api/v2"
# -> Injected into each worktree's CLAUDE.md
```

### Smart Merge Order
```bash
owt queue              # Show optimal merge order
owt queue --ship       # Ship all completed worktrees, smallest first
owt queue --ship --yes # No confirmation
```

### Merge Strategies
```bash
owt merge auth-jwt                     # Standard merge + auto-cleanup
owt merge auth-jwt --rebase            # Rebase for linear history
owt merge auth-jwt --strategy theirs   # Auto-resolve conflicts (ours|theirs)
owt merge auth-jwt --leave-conflicts   # Keep merge in-progress for manual resolution
owt merge auth-jwt --keep              # Keep worktree after merging
```

### CI/CD Headless Mode
```bash
owt new "Run security audit" --headless
owt wait security-audit --timeout 1200
# -> Polls until agent finishes, exits 0 on success
```
Headless mode requires Claude (Droid and OpenCode lack non-interactive mode and hook integration).

### Bug Investigation + Fix
```bash
owt new "Profile memory usage in user service" --plan-mode
# -> Agent investigates in plan mode (read-only)
# Later: owt merge memory-profile
```

### Delegating Tasks
```bash
owt send auth-jwt "Now add refresh token support"
owt send api-refactor "Focus on the /users endpoint first"
```

## Development

```bash
uv pip install -e .
uv run pytest              # 1411+ tests
uv run ruff check src/
uv run mypy src/
```

## Claude Code Integration

Use these slash commands in Claude Code sessions:

- `/wt-create` — Quick worktree creation
- `/wt-list` — List all worktrees
- `/wt-status` — Check AI activity
- `/wt-cleanup` — Clean stale worktrees

## Architecture

```
src/open_orchestrator/
├── cli.py                     # CLI entry point + global options (--theme, --json)
├── config.py                  # Hierarchical config (TOML) + AgnoConfig + BackendConfig + schema validation
├── commands/                  # Modular command registration
│   ├── _shared.py             # Shared helpers (console, managers, formatters)
│   ├── worktree.py            # new (with --herdr/--tmux), list, switch, delete, attach, branch
│   ├── agent.py               # send (--all/--working/--swarm), wait, note, hook
│   ├── merge_cmds.py          # merge, ship, queue
│   ├── orchestrate_cmds.py    # plan, batch, orchestrate
│   ├── maintenance.py         # sync, cleanup, version
│   ├── config_cmd.py          # config validate, config show
│   ├── db_cmd.py              # db purge, db vacuum, db health
│   ├── doctor.py              # doctor diagnostic command
│   ├── memory_cmd.py          # memory add/search/consolidate/list/mine
│   ├── critic_cmd.py          # critic ship/merge/delete (pre-action safety review)
│   ├── dream_cmd.py           # dream enable/disable/status/consolidate/reports
│   └── swarm_cmd.py           # swarm start/list/stop/send
├── core/
│   ├── control_plane_view.py     # Textual ControlPlaneApp — default UI (Sprint 024)
│   ├── control_plane_sections.py # Pure section builders (needs_you/ready_to_ship/in_flight/background)
│   ├── control_plane_actions.py  # (SectionKind, RowAction) → coroutine dispatcher
│   ├── multiplexer.py            # MultiplexerBackend protocol (Sprint 025)
│   ├── tmux_backend.py           # TmuxManager → MultiplexerBackend adapter
│   ├── herdr_client.py           # Async JSON-RPC client over Unix socket
│   ├── herdr_backend.py          # HerdrClient → MultiplexerBackend adapter
│   ├── backend_factory.py        # select_backend(config, override) + detect_herdr
│   ├── switchboard.py            # Legacy card-grid UI (behind --legacy-cards; deprecated)
│   ├── switchboard_cards.py      # Card data, status detection, swarm grouping (SwarmGroup)
│   ├── switchboard_modals.py     # Input/Confirm/SearchableSelect modals (DetailModal removed in S024)
│   ├── switchboard_tmux.py       # Legacy switchboard tmux session lifecycle + global keybindings
│   ├── intelligence.py        # Agno intelligence layer (planner, quality gate, conflict resolver, coordinator)
│   ├── orchestrator.py        # Orchestrator agent (plan → execute → merge → feature branch)
│   ├── prompt_builder.py      # Context-aware prompt builder (task-type + swarm role templates)
│   ├── tool_protocol.py       # AIToolProtocol + CustomTool (plugin interface)
│   ├── tool_registry.py       # Singleton tool registry (discover, register, look up AI tools)
│   ├── tool_search.py         # Deferred tool loading (token budget, lazy MCP)
│   ├── worktree.py            # Git worktree CRUD
│   ├── tmux_manager.py        # tmux session management
│   ├── merge.py               # Two-phase merge + merge queue + conflict guard + AI resolution
│   ├── batch.py               # Autopilot loop + DAG scheduler + AI planner (Agno or subprocess)
│   ├── batch_models.py        # Pydantic batch task models
│   ├── environment.py         # Deps, .env install
│   ├── environment_claude_md.py # CLAUDE.md sync, atomic injection (recall, project, DAG, coordination)
│   ├── status.py              # AI activity tracking (SQLite + WAL)
│   ├── hooks.py               # AI tool hook installer (status push + MCP config)
│   ├── mcp_peer.py            # MCP peer communication server (optional)
│   ├── cleanup.py             # Stale worktree removal
│   ├── sync.py                # Upstream sync
│   ├── branch_namer.py        # Task → branch name
│   ├── project_detector.py    # Auto-detect project type
│   ├── pane_actions.py        # Create/remove orchestration (PaneTransaction)
│   ├── runtime.py             # Task completion evaluation (commits, tmux, grace periods)
│   ├── agent_detector.py      # Detect installed AI tools
│   ├── memory.py              # MemoryManager (MEMORY.md index, topic files, grep search)
│   ├── memory_store.py        # SQLite + FTS5 recall store + temporal knowledge graph
│   ├── memory_miner.py        # FactMiner — git log, progress files, code-comment extraction
│   ├── aaak.py                # AAAK shorthand encoder/decoder for L1 critical facts
│   ├── critic.py              # CriticAgent (pre-action safety: overlaps, uncommitted, empty branch)
│   ├── denial_tracker.py      # Denial tracking (SQLite, consecutive/total thresholds)
│   ├── dream.py               # DreamDaemon (background consolidation, KG contradictions)
│   ├── compaction.py          # Context compaction (snip, microcompact, reactive_compact)
│   ├── subagent.py            # SubagentManager (fork-join, context inheritance, timeout)
│   ├── swarm.py               # SwarmManager (coordinator + specialist workers)
│   └── theme.py               # Multi-palette theme system (dark/light/dark-ansi/light-ansi)
├── models/
│   ├── control_plane.py       # SectionKind, RowAction, ControlPlaneRow, BackgroundEvent, OrchestrationHeader (S024)
│   ├── backend.py             # BackendKind, BackendSession, BackendConfig (S025)
│   ├── intelligence.py        # Agno structured output models (TaskPlan, QualityVerdict, etc.)
│   ├── worktree_info.py       # Worktree models
│   ├── project_config.py      # Project config models
│   ├── maintenance.py         # Cleanup/sync models
│   ├── status.py              # AI status models
│   ├── memory.py              # MemoryType, MemoryLayer, Fact, Triple, ContradictionGroup
│   ├── subagent.py            # SubagentRole, SubagentState, ForkJoinRequest
│   ├── compaction.py          # Message, MessageRole, CompactionResult
│   └── swarm.py               # SwarmRole, SwarmWorker, SwarmState, SwarmWorkerStatus
├── popup/                     # tmux popup picker (theme-aware curses)
├── skills/                    # Claude Code skill definition
└── utils/
    ├── io.py                  # Safe file I/O
    ├── logging.py             # Structured logging (correlation IDs, JSON output)
    ├── output.py              # OutputFormatter (Rich + JSON envelope)
    └── lazy.py                # LazyModule proxy for deferred imports
```

## License

MIT

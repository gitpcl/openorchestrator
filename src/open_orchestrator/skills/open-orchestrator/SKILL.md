---
name: open-orchestrator
description: "Git worktree + AI agent orchestration with Textual switchboard UI, persistent cross-worktree recall memory, swarm mode, dream daemon, critic safety reviews, multi-palette theming, plugin architecture, optional Agno intelligence layer, and MCP peer communication. Use when: (1) Creating isolated dev environments from task descriptions (owt new), (2) Viewing all agent worktrees in a switchboard card grid (owt), (3) Jumping between agent sessions (owt switch), (4) Sending messages to agents (owt send), (5) Broadcasting to all agents (owt send --all/--working/--swarm), (6) Merging worktree branches with conflict guard (owt merge), (7) Shipping worktrees in one shot with quality gate (owt ship), (8) AI-powered task decomposition into dependency DAGs (owt plan), (9) Running batch autopilot tasks with DAG scheduling (owt batch), (10) Viewing optimal merge order (owt queue), (11) Sharing context across agents (owt note), (12) Headless CI/CD mode (owt new --headless, owt wait), (13) Orchestrating AI tools across branches (auto-detects claude, opencode, droid), (14) Agno-powered intelligent planning with codebase awareness, (15) Quality gate review before shipping, (16) AI-powered merge conflict resolution, (17) End-to-end orchestration into feature branch (owt orchestrate), (18) Stop/resume orchestration with persistent state, (19) User presence detection pauses auto-actions, (20) Cross-worktree coordination with Agno or template fallback, (21) MCP-based agent-to-agent peer communication (list_peers, send_message, check_messages), (22) Registering custom AI tools via config (plugin architecture), (23) Diagnosing orphaned resources (owt doctor), (24) Config validation and inspection (owt config validate/show), (25) Database maintenance (owt db purge/vacuum/health), (26) Structured logging with correlation IDs and JSON output (--json), (27) Task-aware prompt building with type-specific protocols, (28) Persistent cross-worktree memory with auto-classification (owt memory add/search/consolidate/list/mine), (29) SQLite + FTS5 recall store with 4-layer token-budgeted stack (L0 identity / L1 critical / L2 topics / L3 search), AAAK shorthand compression for L1, temporal knowledge graph with point-in-time queries and contradiction detection, (30) Auto-injection of L0+L1 recall payload into CLAUDE.md on every owt new, (31) Mining facts from git history, progress files, and code comments (owt memory mine), (32) Coordinator + specialist worker swarms with role constraints (owt swarm start/list/send/stop), (33) Pre-action critic safety review with denial tracking (owt critic ship/merge/delete), (34) Background KAIROS-style dream daemon for memory consolidation, stale worktree surfacing, and KG contradiction detection (owt dream enable/disable/status/consolidate/reports), (35) Multi-palette theming with terminal background OSC 11 detection (--theme auto/dark/light/dark-ansi/light-ansi). Triggers: worktree, parallel development, multi-branch, AI orchestration, switchboard, owt commands, owt new, owt merge, owt ship, owt delete, owt switch, owt send, owt plan, owt batch, owt queue, owt note, owt wait, owt orchestrate, owt doctor, owt config, owt db, owt memory, owt swarm, owt critic, owt dream, auto-detect agents, conflict guard, autopilot, DAG, task planning, agno, quality gate, conflict resolution, intelligent planner, orchestrator, feature branch, coordination, stop resume, MCP, peer communication, agent messaging, plugin, custom tool, structured logging, correlation ID, prompt builder, recall, memory store, fact mining, knowledge graph, AAAK, swarm mode, coordinator, specialist worker, critic, denial tracking, dream daemon, KAIROS, theming, palette, OSC 11."
---

# Open Orchestrator - Git Worktree + AI Orchestration

Open Orchestrator (`owt`) enables developers to manage parallel development workflows with isolated git worktrees and a **Textual-based switchboard UI**. The simplest way to start: `owt new "add user authentication"` — it auto-generates a branch name, creates the worktree, installs deps, and starts the AI tool in a tmux session. Run `owt` with no arguments to launch the switchboard — a card grid showing all active agents with status lights, diff stats, and file overlap warnings.

## Commands (40+ total)

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Switchboard** — card grid with status lights, navigate + act |
| `owt new "task"` | `owt n` | Create worktree + tmux session + deps + AI agent. One command. |
| `owt new "task" --headless` | | Create worktree without tmux (CI/script use) |
| `owt list` | `owt ls` | Quick text list of worktrees (non-interactive, for scripts/pipes) |
| `owt switch <name>` | `owt s` | Jump to a worktree's tmux session |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt send --all "msg"` | | Broadcast to ALL worktrees |
| `owt send --working "msg"` | | Broadcast to WORKING worktrees only |
| `owt send --swarm <id> "msg"` | | Broadcast to all workers in a swarm |
| `owt merge <name>` | `owt m` | Two-phase merge + conflict guard + auto-cleanup (`--rebase`, `--strategy`, `--leave-conflicts`) |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + tmux session + status |
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
| `owt sync [--all]` | | Sync worktree(s) with upstream |
| `owt cleanup [--force]` | | Remove stale worktrees (dry-run by default) |
| `owt config validate` | | Validate configuration file |
| `owt config show` | | Display effective config as TOML |
| `owt db purge [--days N]` | | Delete messages older than N days (default 30) |
| `owt db vacuum` | | Optimize and compact the database |
| `owt db health [--check]` | | Database health diagnostics with CI thresholds |
| `owt memory add "fact"` | | Store a fact with auto-classification (identity/critical/topic) |
| `owt memory search "q"` | | Search recall store, MEMORY.md index, topics, and transcripts |
| `owt memory consolidate` | | Dedup, prune, and index untracked topics |
| `owt memory list` | | List all memory entries |
| `owt memory mine` | | Mine facts from git history, progress files, and code comments |
| `owt swarm start "goal" -w wt` | | Launch coordinator + specialist workers in a worktree |
| `owt swarm list` | | List all active swarms |
| `owt swarm send <id> "msg"` | | Broadcast a message to all workers in a swarm |
| `owt swarm stop <id>` | | Stop a swarm and kill its worker panes |
| `owt critic ship\|merge\|delete <name>` | | Pre-action safety review (overlaps, uncommitted, empty branch) |
| `owt dream enable` | | Start the background dream daemon |
| `owt dream disable` | | Stop the dream daemon |
| `owt dream status` | | Show daemon state and last heartbeat |
| `owt dream consolidate` | | Run consolidation immediately |
| `owt dream reports` | | List recent dream reports |
| `owt doctor [--fix]` | | Diagnose and fix orphaned resources |
| `owt --theme <name>` | | Override UI theme (auto, dark, light, dark-ansi, light-ansi) |
| `owt --json <cmd>` | | Machine-readable JSON output for `list`, `queue`, `doctor`, `db health` |
| `owt version` | | Show version |

## The Switchboard

Run `owt` to launch the switchboard — your command center for multi-agent orchestration:

```
  SWITCHBOARD                          4 lines  *3 active  !1 overlap

  +- auth-jwt --------------+   +- fix-login --------------+
  | * WORKING        12m    |   | o IDLE              3h    |
  | feat/auth-jwt           |   | fix/login-redirect        |
  | claude        +142 -37  |   | claude                    |
  | Implementing JWT auth   |   | -                         |
  +-------------------------+   +---------------------------+

  +- api-refactor ----------+   +- db-migration ------------+
  | * WORKING        45m    |   | ! BLOCKED           5m    |
  | refactor/api-v2         |   | feat/db-migration         |
  | opencode       +89 -12  |   | claude          +23 -5    |
  | [! 2 overlap]           |   | Waiting for input         |
  +-------------------------+   +---------------------------+

  [arrows] nav [Enter] patch [s] send [a] all [n] new [S] ship [f] files [i] info [q] quit
```

**Switchboard keys:**
- Arrow keys: navigate between cards
- `Enter`: patch into that agent's tmux session (switchboard stays alive)
- `s`: send a message to the selected agent
- `a`: broadcast a message to ALL agents
- `n`: create a new worktree + agent
- `S`: ship the selected worktree (commit + merge + delete)
- `d`: delete the selected worktree
- `m`: merge the selected worktree
- `f`: show file overlap detail for the selected card
- `i`: show detail panel (commits, diff stats, overlaps)
- `q`: quit back to terminal

**Global tmux keybindings (work from any agent session):**
- `Alt+s`: switch back to the switchboard
- `Alt+m`: merge current worktree
- `Alt+d`: delete current worktree
- `Alt+c`: create a new worktree (opens popup)

**Navigation flow:** `owt` -> switchboard -> `Enter` -> agent -> `Alt+s` -> switchboard -> `q` -> terminal

## Core Workflow

### 1. Create a Worktree
```bash
owt new "Add user authentication with JWT"
# -> Generates branch: feat/add-user-auth-jwt
# -> Creates worktree, installs deps, copies .env
# -> Creates tmux session with Claude running
# -> Sends task description as initial prompt

owt new --branch feat/my-branch    # Explicit branch name
owt new "Fix login" --plan-mode    # Start Claude in plan mode
owt new "Quick fix" --template bugfix  # Use workflow template
owt new "Audit" --headless         # No tmux (CI/scripts)
```

### 2. Monitor via Switchboard
```bash
owt           # Launch switchboard in persistent tmux session
owt list      # Quick text table for scripts/pipes
owt wait auth-jwt --timeout 600    # Poll until done (CI/scripts)
```

### 3. Interact with Agents
```bash
owt send auth-jwt "Fix the failing tests"
owt send --all "Run tests"          # Broadcast to all
owt send --working "Wrap up"        # Broadcast to working only
owt note "Users table has verified_at column"  # Share context
owt switch auth-jwt                 # Jump to that tmux session
```

### 4. Complete Work
```bash
owt queue              # Show optimal merge order
owt queue --ship       # Ship all completed, smallest first
owt ship auth-jwt      # Commit + merge + delete in one shot
owt merge auth-jwt     # Two-phase merge + conflict guard + cleanup
owt merge auth-jwt --rebase           # Rebase for linear history
owt merge auth-jwt --strategy theirs  # Auto-resolve conflicts
owt merge auth-jwt --leave-conflicts  # Leave in-progress for manual resolution
owt delete fix-login   # Delete worktree + session + status
owt cleanup --force    # Delete stale worktrees
```

### 5. AI-Powered Planning (DAG Execution)
```bash
owt plan "Build JWT auth with refresh tokens"           # Generate plan.toml
owt plan "Add rate limiting" --execute                   # Generate + run (batch mode)
owt plan "Add auth" --start --branch feat/auth-v2       # Plan + orchestrate into feature branch
owt plan "Fix auth bugs" --execute --auto-ship           # Generate + run + auto-merge
```

Tasks run in dependency order. Independent tasks run in parallel. Parent context is auto-injected into child worktrees.

### 6. Orchestrator (Feature Branch Mode)
```bash
owt plan "Add JWT auth" --start --branch feat/auth-v2    # Plan + start in one shot
owt orchestrate plan.toml --branch feat/auth-v2          # Start from existing plan
owt orchestrate --resume                                  # Resume from saved state
owt orchestrate --stop                                    # Graceful stop (worktrees kept)
owt orchestrate --status                                  # Show progress table
```

The orchestrator merges completed tasks into a **feature branch** (not main), persists state for stop/resume, detects user presence to pause auto-actions, and coordinates agents when file overlaps are detected. Batch mode and orchestrator mode share the same runtime completion evaluator, so grace-period checks, premature-exit detection, commit checks, and retry behavior stay aligned.

**Agent execution model:** Orchestrated and batch agents now start as live provider sessions inside tmux, then receive the structured session-init prompt through `send-keys`. That keeps the session patchable from the switchboard while preserving `OWT_AUTOMATED=1` for hook-aware automation. Runtime completion uses shared hook plus pane-state detection rather than relying on the pane to auto-exit.

**Safety nets:** Before shipping, the orchestrator (1) auto-commits any uncommitted work left by agents (`feat(auto):` prefix), (2) runs an optional Agno quality gate, (3) refuses to ship branches with zero new commits, and (4) retries failed tasks once with failure context injected into the prompt. Per-task timeouts (default 30 min) prevent hung agents from blocking the DAG.

**Agent prompts:** Agents receive a structured session init protocol (orient → explore → implement → test → verify → commit) and project context (detected test/dev commands) via CLAUDE.md injection. Progress is tracked via incremental `wip:` commits visible in the switchboard.

### 7. Batch Autopilot (DAG-Aware)
```bash
owt batch tasks.toml               # Run batch from TOML (supports depends_on)
owt batch tasks.toml --auto-ship   # Auto-ship completed work
```

`[batch].min_agent_runtime` is available in `tasks.toml` to tune the shared silent-exit guard. `OWT_DB_PATH` can be used to point hooks, MCP peers, and in-process status tracking at the same SQLite DB; if the default home-directory DB path is unavailable, orchestrator/batch fall back to repo-local or temp-backed storage.

## Templates

Three built-in templates: `feature`, `bugfix`, `hotfix`.

```bash
owt new "Add payments" --template feature   # Plan mode, TDD workflow
owt new "Fix crash" --template bugfix       # Root cause focus
owt new "Patch CVE" --template hotfix       # Minimal changes, production focus
```

## Configuration

Config files (priority order): `.worktreerc`, `.worktreerc.toml`, `~/.config/open-orchestrator/config.toml`, `~/.worktreerc`

```toml
[worktree]
base_directory = "../"
auto_cleanup_days = 14

[tmux]
auto_start_ai = true
ai_tool = "claude"    # claude, opencode, droid

[environment]
auto_install_deps = true
copy_env_file = true
```

## Agno Intelligence Layer (Optional)

Install with `pip install open-orchestrator[agno]` to enable AI-powered intelligence features. Without it, everything works exactly as before.

### Intelligent Planner
`owt plan` uses an Agno agent with codebase awareness — it reads the file tree and git history to produce better task decompositions with Pydantic-validated structured output (no regex parsing).

### Quality Gate
`owt ship` runs an AI quality review before merging. Checks code quality, completeness, security issues, and cross-worktree conflicts. Prompts if issues are found (skipped with `--yes`).

### Merge Conflict Resolution
When `auto_resolve_conflicts = true`, merge conflicts are resolved semantically by an AI agent before falling back to manual resolution. Only applies resolved content when confidence > 0.8.

### Cross-Worktree Coordination
The orchestrator detects file overlaps between running worktrees and injects context into each agent's CLAUDE.md. With Agno, a coordinator agent generates intelligent, targeted messages. Without Agno, template-based warnings are used. Coordination runs on a 120s cooldown per event to avoid noise.

### Configuration
```toml
[agno]
enabled = true
model_id = "claude-sonnet-4-20250514"
quality_gate_threshold = 0.8
auto_resolve_conflicts = false
coordinator_model_id = "claude-haiku-4-5-20251001"  # Cost-effective for coordination
```

All three features are model-agnostic (Claude, OpenAI, Gemini) and gracefully degrade — import failures or runtime errors silently fall back to existing behavior.

## AI Tool Support

Auto-detects installed tools: Claude Code, OpenCode, Droid. Offers picker when multiple found.

```bash
owt new "task" --ai-tool claude
owt new "task" --ai-tool opencode
owt new "task" --ai-tool droid
```

### Custom AI Tools (Plugin Architecture)

Register any AI coding tool via config without code changes:

```toml
[tools.mytool]
binary = "my-ai-tool"
command_template = "{binary} --interactive"
prompt_flag = "-p"
supports_hooks = false
install_hint = "Install from https://..."
known_paths = ["~/.local/bin/mytool"]
```

Built-in tools (`claude`, `opencode`, `droid`) use the same `AIToolProtocol` interface. The `ToolRegistry` singleton handles discovery, registration, and lookup.

## Diagnostics & Maintenance

```bash
owt doctor              # Find orphaned worktrees, tmux sessions, status entries
owt doctor --fix        # Auto-fix orphaned resources
owt config validate     # Validate config file against schema
owt config show         # Display effective config as TOML
owt db health           # Database size, row counts, WAL status
owt db health --check   # Exit non-zero if thresholds exceeded (CI-friendly)
owt db purge --days 7   # Delete messages older than 7 days
owt db vacuum           # Compact and optimize the database
owt --json list         # Machine-readable JSON output (also: queue, doctor, db health)
```

## Structured Logging

Correlation IDs and per-worktree context are injected into every log record via `ContextVar`-based tracking. JSON output is available for log aggregation:

```bash
owt --log-format json orchestrate plan.toml   # JSON output for jq/log pipelines
owt --verbose new "task"                       # DEBUG-level output
```

## Task-Aware Prompt Builder

Agents receive structured protocols based on task type (detected from keywords):

- **Feature:** ORIENT → EXPLORE → IMPLEMENT → TEST → VERIFY → COMMIT
- **Bugfix:** REPRODUCE → DIAGNOSE → FIX → VERIFY → REGRESSION → COMMIT
- **Refactor:** BASELINE → PLAN → REFACTOR → VERIFY → CLEANUP → COMMIT
- **Test:** SURVEY → IDENTIFY → WRITE → RUN → COVERAGE → COMMIT
- **Docs:** READ → DRAFT → EXAMPLES → REVIEW → COMMIT

The `PromptBuilder` assembles sections by priority with budget-aware truncation (drops lowest-priority sections first when approaching token limits).

## Project Detection

Auto-detects: Python (uv/poetry/pip), Node.js (bun/pnpm/yarn/npm), Rust (cargo), Go, PHP (composer).

## Memory & Recall

Persistent cross-worktree knowledge with two complementary backends:

**MemoryManager** (file-based, stdlib only) — `MEMORY.md` index + per-topic files with grep search and auto-classification (identity / critical / topic).

**Recall store** (SQLite + FTS5, also stdlib `sqlite3`, zero new deps) — a 4-layer token-budgeted stack:

- **L0 identity** — pinned facts (user, project, role)
- **L1 critical** — high-importance facts compressed via **AAAK shorthand** (encoder/decoder under `core/aaak.py`)
- **L2 topics** — categorized facts grouped by subject
- **L3 search** — full FTS5 over the entire fact corpus

Backed by a **temporal knowledge graph** with point-in-time queries, contradiction detection, and provenance triples. The L0+L1 payload auto-injects into each worktree's CLAUDE.md on every `owt new`, so agents start with the user's pinned context.

```bash
owt memory add "API uses bearer tokens, not session cookies"   # auto-classifies
owt memory search "auth"                                        # FTS5 + grep + transcripts
owt memory list                                                 # full inventory
owt memory consolidate                                          # dedup, prune, re-index
owt memory mine                                                 # FactMiner: extract from git log, progress files, code comments
```

`OWT_RECALL_DB_PATH` overrides the recall SQLite path (default `~/.open-orchestrator/recall.db`).

## Swarm Mode

`owt swarm start "goal" -w <worktree>` launches a **coordinator + specialist worker** swarm inside a single worktree. Workers run as tmux panes with role-constrained prompts:

- **researcher** — read-only, gathers context
- **implementer** — writes code
- **reviewer** — read-only, audits implementer output
- **tester** — limited to `tests/` directory

Role prompts enforce constraints, and the coordinator brokers handoffs between workers. The switchboard groups swarm workers under a `SwarmGroup` card.

```bash
owt swarm start "Add JWT auth" -w auth-jwt           # default: researcher + implementer + reviewer + tester
owt swarm list                                        # all active swarms
owt swarm send <swarm-id> "wrap up and commit"        # broadcast to every worker
owt send --swarm <swarm-id> "msg"                     # alias via owt send
owt swarm stop <swarm-id>                             # kill worker panes, keep worktree
```

## Critic (Pre-Action Safety Review)

`owt critic ship|merge|delete <name>` runs a pre-action safety review before destructive operations. Checks include:

- File overlaps with other active worktrees
- Uncommitted changes that would be lost
- Empty branches (zero new commits)
- Unmerged commits on the parent branch

Findings are scored by severity. The critic uses a **DenialTracker** (SQLite-backed) — after 3 consecutive or 20 total denials in a session, the critic falls back to user confirmation rather than continuing to block automated workflows.

```bash
owt critic ship auth-jwt    # safety review without performing the action
owt critic merge fix-login
owt critic delete stale-wt
```

The critic is also invoked automatically by `owt ship` and `owt merge` when enabled in config.

## Dream Mode (Background Consolidation)

`owt dream enable` starts a **KAIROS-style background daemon** that periodically wakes to consolidate memory, surface stale worktrees, and detect knowledge-graph contradictions across worktrees. Reports are saved under `.owt/dream_reports/`.

```bash
owt dream enable        # start background daemon
owt dream disable       # stop daemon
owt dream status        # daemon state + last heartbeat
owt dream consolidate   # run consolidation immediately (foreground)
owt dream reports       # list recent dream reports
```

The daemon uses a heartbeat file for liveness, runs on a cooldown to avoid noise, and integrates with the recall store + MemoryManager for cross-session memory hygiene.

## Theming

The switchboard auto-detects terminal background via **OSC 11** (with `$COLORFGBG` fallback) and selects from four palettes:

- `dark` — true-color dark backgrounds
- `light` — true-color light backgrounds
- `dark-ansi` — 16-color dark fallback for limited terminals
- `light-ansi` — 16-color light fallback

```bash
owt --theme auto         # default, OSC 11 detection
owt --theme dark
owt --theme light-ansi
```

Switchboard CSS uses native Textual `$variable` references so palette swaps are instant. Set `OWT_BACKGROUND` to override the detected hex if OSC 11 detection fails.

## MCP Peer Communication (Optional)

Install with `pip install open-orchestrator[mcp]` to enable agent-to-agent communication via MCP (Model Context Protocol). Each agent's Claude session gets an MCP server with peer discovery and messaging tools.

**Tools available to agents:**
- `list_peers` — discover all active agents (name, branch, status, summary)
- `send_message` — send a message to a peer agent (or broadcast with `to_peer="*"`)
- `check_messages` — read unread messages from other agents
- `set_summary` — update this agent's visible status for coordination
- `get_peer_files` — check what files a peer is editing (avoid conflicts)

**How it works:** When `owt new` creates a worktree, an MCP server config (`owt-peers`) is injected into `.claude/settings.local.json`. Claude Code spawns the server process (stdio transport), which reads/writes to the shared SQLite status database (WAL mode for concurrent access). No broker daemon needed.

**Example agent conversation:**
```
Agent A: list_peers() → [{name: "api-refactor", status: "working", summary: "REST endpoints"}]
Agent A: send_message("api-refactor", "I'm adding auth middleware to server.py — are you touching it?")
Agent B: check_messages() → [{from: "auth-jwt", message: "...are you touching it?"}]
Agent B: send_message("auth-jwt", "No, only routes.py. Go ahead.")
```

## Dependencies

7 production deps: click, pydantic, rich, textual, toml, gitpython, libtmux. The recall store, AAAK encoder, knowledge graph, dream daemon, swarm, critic, and theming all use stdlib only — **zero new dependencies** for v0.4.0 features. Optional: agno (intelligence layer), mcp (peer communication).

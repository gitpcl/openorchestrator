---
name: open-orchestrator
description: "The multi-provider cockpit for parallel AI coding. Supervise Claude Code, Pi, Droid, and OpenCode across isolated git worktrees from one Textual control plane (three lanes: NEEDS YOU / READY TO SHIP / IN FLIGHT) with real-time cross-worktree Conflict Guard (file-overlap detection), pluggable multiplexer backends (tmux default, herdr opt-in), a multi-provider plugin layer, native plan-first workflow launch, and optional MCP peer communication. Use when: (1) Creating isolated dev environments from task descriptions (owt new), (2) Launching a native plan-first Claude Code workflow in a worktree (owt new --workflow), (3) Supervising different AI providers per worktree (owt new --ai-tool claude|pi|droid|opencode), (4) Viewing all agent worktrees in a prioritized control plane (owt), (5) Handing off to an agent session via the active backend (owt attach), (6) Jumping between agent sessions (owt switch), (7) Sending messages to agents (owt send, owt send --all/--working), (8) Merging worktree branches with Conflict Guard (owt merge), (9) Shipping worktrees in one shot (owt ship), (10) Viewing optimal merge order with overlap counts (owt queue), (11) Sharing context across agents (owt note), (12) Headless CI/CD mode (owt new --headless, owt wait), (13) Branch-mode sessions without git worktrees (owt branch, owt new --in-place), (14) Opt-in herdr multiplexer backend (owt new --herdr, [backend] mode = 'tmux'|'herdr'|'auto'), (15) Registering custom AI tools via config (plugin architecture), (16) Diagnosing orphaned resources (owt doctor), (17) Config validation and inspection (owt config validate/show), (18) Database maintenance (owt db purge/vacuum/health), (19) Structured logging with correlation IDs and JSON output (--json), (20) MCP-based agent-to-agent peer communication (list_peers, send_message, check_messages). Triggers: worktree, parallel development, multi-branch, multi-provider, AI cockpit, control plane, supervise agents, owt commands, owt new, owt new --workflow, owt attach, owt merge, owt ship, owt delete, owt switch, owt send, owt queue, owt note, owt wait, owt branch, owt doctor, owt config, owt db, auto-detect agents, Conflict Guard, file overlap, claude pi droid opencode, plugin, custom tool, multiplexer backend, herdr, MultiplexerBackend protocol, MCP, peer communication, structured logging, theming, palette."
---

# Open Orchestrator — the multi-provider cockpit for parallel AI coding

Open Orchestrator (`owt`) supervises parallel AI coding sessions across isolated git worktrees. It does **not** try to be the agent — it hosts whatever AI coding tool you point it at (Claude Code, Pi, Droid, OpenCode, or a custom tool) and gives you one place to watch and steer them all.

**Two things make it worth using, and native tooling lacks both:**

1. **A persistent cross-worktree control plane** — a standing board across long-lived worktrees, three lanes deep (NEEDS YOU / READY TO SHIP / IN FLIGHT), driven entirely from the keyboard.
2. **Conflict Guard** — real-time file-overlap detection that warns you the moment two agents start editing the same files, long before they collide at merge.

**The primary interface is one command: `owt`** — it launches the control plane. Press **`n`** to start work (type a task, pick one worktree or a native plan-first workflow, confirm), **`a`** to attach, **`s`** to ship, **`f`** to fix conflicts, **`m`** to merge. Every row carries verb actions and the footer shows only the keys that apply to the focused row.

You own the cockpit; the AI tools own the engine.

## Commands (scripting / CI reference)

Humans drive the [control plane](#the-control-plane) with `owt` and the keys above; the verbs below are the same actions exposed for scripts, pipelines, and automation.

| Command | Alias | Description |
|---------|-------|-------------|
| `owt` | | **Launch the Control Plane** — prioritized lanes, row verbs |
| `owt new "task"` | `owt n` | Create worktree + session + deps + AI agent. One command. |
| `owt new "task" --workflow` | | Launch a native plan-first Claude Code workflow in the worktree |
| `owt new "task" --ai-tool <name>` | | Pick the provider (claude/pi/droid/opencode/custom) |
| `owt new "task" --herdr` | | Use the herdr multiplexer backend instead of tmux |
| `owt new "task" --tmux` | | Force tmux backend (default; useful to override config) |
| `owt new "task" --headless` | | Create worktree without tmux (CI/script use) |
| `owt branch "task"` | | Create branch in current checkout instead of worktree (faster, zero disk) |
| `owt list` | `owt ls` | Quick text list of worktrees (non-interactive, for scripts/pipes) |
| `owt switch <name>` | `owt s` | Jump to a worktree's session |
| `owt attach <name>` | | Hand off to the worktree's session via the active backend (`--herdr` / `--tmux` to override) |
| `owt send <name> "msg"` | | Send command to a worktree's AI agent |
| `owt send --all "msg"` | | Broadcast to ALL worktrees |
| `owt send --working "msg"` | | Broadcast to WORKING worktrees only |
| `owt merge <name>` | `owt m` | Two-phase merge + Conflict Guard + auto-cleanup (`--rebase`, `--strategy`, `--leave-conflicts`) |
| `owt ship <name>` | | Commit + merge + delete in one shot |
| `owt delete <name>` | `owt rm` | Delete worktree + session + status |
| `owt queue` | | Show optimal merge order (with overlap counts) for completed worktrees |
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
| `owt --theme <name>` | | Override UI theme (auto, dark, light, dark-ansi, light-ansi) |
| `owt --json <cmd>` | | Machine-readable JSON output for `list`, `queue`, `doctor`, `db health` |
| `owt version` | | Show version |

## The Control Plane

Run `owt` to launch the control plane — a prioritized decision surface. Three lanes render top-to-bottom in priority order; empty lanes are hidden so you always see the most important thing first.

```
  open-orchestrator · 4 rows · 14:32:08
  ▸ NEEDS YOU      (1)
  ▶ auth-jwt        merge conflict — needs manual resolution   [f] [a]
  ▸ READY TO SHIP  (2)
    fix-login       +3 commits · queued #1/2                   [s] [a]
    docs-update     +1 commits · queued #2/2 · 1 overlap       [s] [a]
  ▸ IN FLIGHT      (1)
    api-refactor    45m · opencode · Refactoring REST routes   [a]

  ↑↓ nav | n new | s ship | a attach | q quit
```

**Starting work (`n`):** press `n` to start a task without leaving the UI — type the task, pick the run mode (**One worktree + agent** → `owt new`, or **Native Claude workflow (plan-first)** → `owt new --workflow`), confirm the resolved command, and it runs in the background. The mode picker is explicit; the `owt new` verbs remain for scripts.

**Lanes:**
- **NEEDS YOU** — merge conflicts and BLOCKED/ERROR status (priority section)
- **READY TO SHIP** — `MergeManager.plan_merge_order()` output with `[s]hip` action and per-worktree overlap counts
- **IN FLIGHT** — WORKING agents with elapsed time + provider + last task message

**Row verbs:**
- `n` — new (start work: task → mode pick → confirm); always available
- `s` — ship (commit + merge + delete via confirm modal)
- `a` — attach (hand off via active multiplexer backend, see below)
- `f` — fix (open conflicted files in `$EDITOR`)
- `m` — merge (without delete/cleanup)

**Context-sensitive footer:** always shows `↑↓ nav`, `n new`, `q quit`; between them it lists only the verbs that apply to the focused row, so the UI teaches itself.

**Navigation:** `↑/↓` or `j/k` for previous/next row across lanes; `q` to quit.

**Header bar:** project name + total row count + a clock.

**Design notes:** Section builders are pure functions in `core/control_plane_sections.py` (fully testable without a Textual Pilot); the action dispatcher in `core/control_plane_actions.py` is a `(SectionKind, RowAction) → coroutine` table; the view in `core/control_plane_view.py` is dumb — it only knows about rows and key presses.

## Conflict Guard

`MergeManager.check_file_overlaps()` compares the files each worktree has modified (tracked in the status DB) against every other worktree and reports the intersection. `plan_merge_order()` surfaces an overlap count per worktree, rendered in the READY TO SHIP lane and the `owt queue` output, so you see brewing collisions before you merge. The detection is pure and AI-free.

## Multi-provider plugin layer

owt launches and manages any AI coding tool through the `AIToolProtocol` interface (`core/tool_protocol.py`), resolved via the `ToolRegistry` singleton (`core/tool_registry.py`). Built-in tools: **claude, pi, droid, opencode**. Auto-detection (`core/agent_detector.py`) picks the best installed tool (order: claude > pi > droid > opencode) when you don't specify one.

```bash
owt new "task" --ai-tool claude
owt new "task" --ai-tool pi
owt new "task" --ai-tool droid
owt new "task" --ai-tool opencode
```

### Native workflow launch

`owt new "task" --workflow` launches a **plan-first native Claude Code workflow** in a managed worktree (Claude starts in plan mode and is given a plan-then-execute protocol), then tracks it on the board alongside Pi/Droid/OpenCode sessions in other worktrees. owt supervises native; it doesn't replace it.

### Custom AI tools

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

## Multiplexer backends (tmux / herdr)

owt defaults to **tmux** for hosting agent sessions. Opt in to **[herdr](https://herdr.dev)** as the multiplexer backend — owt becomes the orchestration brain, herdr the rendering surface. Purely additive; tmux remains the default.

```bash
owt new "Refactor billing" --herdr
owt attach my-feature --herdr

# project-wide via .worktreerc.toml
[backend]
mode = "auto"               # tmux | herdr | auto
herdr_session = "default"   # named herdr session (selects which socket)
```

**Selection precedence:** `--herdr`/`--tmux` on the command line → `[backend] mode` in config → `tmux` default. `mode = "auto"` reaches for herdr first and falls back to tmux when unreachable.

**Status DB is source of truth.** `StatusTracker.update_task(..., backend=)` writes SQLite first, then forwards to `backend.report_agent_state` so herdr's sidebar reflects owt's state. If herdr is down the SQLite write is unaffected and the control plane keeps working.

`AgentLauncher`, `commands/agent.py:send`, the `commands/worktree/` package, and `commands/doctor.py` resolve a backend through `core/backend_factory.py` and never touch `TmuxManager` directly — the protocol is the only seam.

See [`docs/herdr-integration.md`](../../../../docs/herdr-integration.md) for the full configuration, troubleshooting, and named-session walkthrough.

## Core workflow

### 1. Create a worktree
```bash
owt new "Add user authentication with JWT"
# -> Generates branch: feat/add-user-auth-jwt
# -> Creates worktree, installs deps, copies .env
# -> Creates session with the AI tool running
# -> Sends task description as initial prompt

owt new --branch feat/my-branch    # Explicit branch name
owt new "Fix login" --workflow      # Native plan-first Claude workflow
owt new "Quick fix" --template bugfix  # Use workflow template
owt new "Audit" --headless         # No tmux (CI/scripts)
```

### 2. Monitor via the control plane
```bash
owt           # Launch the control plane
owt list      # Quick text table for scripts/pipes
owt wait auth-jwt --timeout 600    # Poll until done (CI/scripts)
```

### 3. Interact with agents
```bash
owt send auth-jwt "Fix the failing tests"
owt send --all "Run tests"          # Broadcast to all
owt send --working "Wrap up"        # Broadcast to working only
owt note "Users table has verified_at column"  # Share context
owt switch auth-jwt                 # Jump to that session
```

### 4. Complete work
```bash
owt queue              # Show optimal merge order (with overlap counts)
owt queue --ship       # Ship all completed, smallest first
owt ship auth-jwt      # Commit + merge + delete in one shot
owt merge auth-jwt     # Two-phase merge + Conflict Guard + cleanup
owt merge auth-jwt --rebase           # Rebase for linear history
owt merge auth-jwt --strategy theirs  # Bias conflict resolution
owt merge auth-jwt --leave-conflicts  # Leave in-progress for manual resolution
owt delete fix-login   # Delete worktree + session + status
owt cleanup --force    # Delete stale worktrees
```

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
ai_tool = "claude"    # claude, pi, droid, opencode

[environment]
auto_install_deps = true
copy_env_file = true

[backend]
mode = "tmux"         # tmux | herdr | auto
```

## Branch-mode sessions

`owt branch` / `owt new --in-place` create a branch in the current checkout instead of a separate worktree — faster setup, zero extra disk. `owt send`, `owt switch`, and `owt delete` work on branch sessions by falling back to the status DB when `WorktreeManager.get` raises. `owt doctor` reconciles branch rows against `git branch --list`, so a healthy in-place branch session is never flagged as an orphan.

## Diagnostics & maintenance

```bash
owt doctor              # Find orphaned worktrees, sessions, status entries
owt doctor --fix        # Auto-fix orphaned resources
owt config validate     # Validate config file against schema
owt config show         # Display effective config as TOML
owt db health           # Database size, row counts, WAL status
owt db health --check   # Exit non-zero if thresholds exceeded (CI-friendly)
owt db purge --days 7   # Delete messages older than 7 days
owt db vacuum           # Compact and optimize the database
owt --json list         # Machine-readable JSON output (also: queue, doctor, db health)
```

## Structured logging

Correlation IDs and per-worktree context are injected into every log record via `ContextVar`-based tracking. JSON output is available for log aggregation:

```bash
owt --log-format json new "task"   # JSON output for jq/log pipelines
owt --verbose new "task"           # DEBUG-level output
```

## Task-aware prompt builder

Agents receive structured protocols based on task type (detected from keywords):

- **Feature:** ORIENT → EXPLORE → IMPLEMENT → TEST → VERIFY → COMMIT
- **Bugfix:** REPRODUCE → DIAGNOSE → FIX → VERIFY → REGRESSION → COMMIT
- **Refactor:** BASELINE → PLAN → REFACTOR → VERIFY → CLEANUP → COMMIT
- **Test:** SURVEY → IDENTIFY → WRITE → RUN → COVERAGE → COMMIT
- **Docs:** READ → DRAFT → EXAMPLES → REVIEW → COMMIT

The `PromptBuilder` assembles sections by priority with budget-aware truncation.

## Project detection

Auto-detects: Python (uv/poetry/pip), Node.js (bun/pnpm/yarn/npm), Rust (cargo), Go, PHP (composer).

## Theming

The control plane auto-detects terminal background via **OSC 11** (with `$COLORFGBG` fallback) and selects from four palettes: `dark`, `light`, `dark-ansi`, `light-ansi`.

```bash
owt --theme auto         # default, OSC 11 detection
owt --theme dark
owt --theme light-ansi
```

Set `OWT_BACKGROUND` to override the detected hex if OSC 11 detection fails.

## MCP peer communication (optional)

Install with `pip install open-orchestrator[mcp]` to enable agent-to-agent communication via MCP. Each agent's Claude session gets an MCP server with peer discovery and messaging tools.

**Tools available to agents:**
- `list_peers` — discover all active agents (name, branch, status, summary)
- `send_message` — send a message to a peer agent (or broadcast with `to_peer="*"`)
- `check_messages` — read unread messages from other agents
- `set_summary` — update this agent's visible status for coordination
- `get_peer_files` — check what files a peer is editing (avoid conflicts)

The MCP server reads/writes the shared SQLite status database (WAL mode), so no broker daemon is needed.

## Dependencies

7 production deps: click, pydantic, rich, textual, toml, gitpython, libtmux. Optional: mcp (peer communication).

---
name: open-orchestrator
description: "Git worktree + AI coding tool orchestration for parallel development with on-demand workspace mode (dmux-like). Use when: (1) Viewing multiple worktrees in a single tmux session (like Agent Teams), (2) Creating isolated development environments from task descriptions (owt new), (3) Adding worktree panes on demand with prefix+n popup picker, (4) Merging worktree branches with two-phase merge (owt merge), (5) Closing worktrees atomically (owt close), (6) Managing multiple AI coding sessions, (7) Delegating tasks to parallel worktrees, (8) Orchestrating AI tools across branches (auto-detects claude, codex, gemini-cli, aider, amp, kilo-code, opencode, droid), (9) Monitoring worktree health and detecting issues, (10) Optimizing AI tool costs and comparing pricing, (11) Cleaning up stale worktrees, (12) Syncing worktrees with upstream, (13) Monitoring with live dashboard and tmux status bar, (14) Tracking token usage and costs, (15) Linking worktrees to GitHub PRs, (16) Managing status change hooks/notifications, (17) Copying/resuming Claude sessions, (18) Using workflow templates for common patterns, (19) Updating to latest version. Triggers: worktree, parallel development, multi-branch, AI orchestration, workspace mode, on-demand panes, dmux, popup picker, unified view, owt commands, owt new, owt merge, owt close, templates, health monitoring, cost optimization, dashboard, token tracking, PR integration, hooks, session management, update, version, auto-detect agents."
---

# Open Orchestrator - Git Worktree + AI Orchestration

Open Orchestrator (`owt`) enables developers to manage parallel development workflows with isolated git worktrees in an **on-demand workspace mode** (dmux-like). The simplest way to start: `owt new "add user authentication"` — it auto-generates a branch name, creates the worktree, detects installed AI tools, and starts working. Press `prefix+n` to add more panes on demand. When done, `owt merge <worktree>` handles two-phase merge and cleanup, or `owt close <worktree>` removes everything atomically.

## Quick Reference

### Common Commands (with aliases)

| Command | Alias | Description |
|---------|-------|-------------|
| `owt new "task description"` | `owt n` | Create worktree from task description (prompt-first, auto-names branch) |
| `owt list` | `owt ls` | List worktrees with status |
| `owt status` | `owt st` | Track AI activity across worktrees |
| `owt merge <worktree>` | `owt m` | Two-phase merge worktree branch into base + cleanup |
| `owt close <worktree>` | `owt x` | Atomic close: remove pane + delete worktree |
| `owt delete <name>` | `owt rm` | Delete worktree and cleanup |

### Worktree Creation

| Command | Description |
|---------|-------------|
| `owt new "add user auth"` | Prompt-first: auto-generates branch name, starts AI with task |
| `owt new` | Interactive: prompts for description, picks AI tool if multiple installed |
| `owt create <branch>` | Power-user: create worktree from explicit branch name |
| `owt create <branch> --separate-session` | Create separate tmux session (opt-out of workspace) |
| `owt create <branch> --plan-mode` | Create with Claude in plan mode |
| `owt create <branch> --template <name>` | Create with workflow template |
| `owt create <branch> --auto-optimize` | Auto-select cost-effective AI tool |

### Lifecycle Management

| Command | Description |
|---------|-------------|
| `owt merge <worktree>` | Two-phase merge: base→feature then feature→base, auto-cleanup |
| `owt merge <worktree> --keep` | Merge without deleting worktree afterward |
| `owt merge <worktree> --json` | JSON output for scripting |
| `owt close <worktree>` | Atomic: remove pane + stop processes + delete worktree |
| `owt close <worktree> -y` | Skip confirmation |

### Workspace & Pane Management

| Command | Description |
|---------|-------------|
| `owt pane add --branch <name>` | Add worktree pane on demand (also via `prefix+n`) |
| `owt pane remove --worktree <name>` | Remove pane + delete worktree (also via `prefix+X`) |
| `owt workspace list` | List all unified workspaces |
| `owt workspace show <name>` | Show workspace details and panes |
| `owt workspace attach <name>` | Attach to workspace tmux session |
| `owt workspace destroy <name>` | Destroy workspace (keeps worktrees) |

### Monitoring & Maintenance

| Command | Description |
|---------|-------------|
| `owt switch <name>` | Switch to worktree (use `--tmux` for session) |
| `owt send <name> "task"` | Send command to AI in another worktree |
| `owt health [name]` | Check worktree health and detect issues |
| `owt cost [name]` | Compare AI tool costs and show savings |
| `owt cleanup [--dry-run]` | Remove stale worktrees |
| `owt sync [--all]` | Sync worktrees with upstream |
| `owt dashboard` | Live TUI monitoring of all worktrees |
| `owt tokens show` | View token usage and costs |

### Integrations

| Command | Description |
|---------|-------------|
| `owt pr link <name> --pr <num>` | Link worktree to GitHub PR |
| `owt pr status` | Show PR status for all worktrees |
| `owt hooks list` | List status change hooks |
| `owt copy-session <src> <dest>` | Copy Claude session between worktrees |
| `owt resume <name>` | Resume Claude session in worktree |
| `owt process start <name>` | Start AI tool without tmux |
| `owt process list` | List running AI processes |
| `owt template list` | List available workflow templates |
| `owt template show <name>` | Show template details |
| `owt completion install` | Install shell auto-completion |
| `owt version [--full]` | Show version and installation info |
| `owt update [--check]` | Update to latest version |

## On-Demand Workspace Mode (Default)

Open Orchestrator uses **on-demand workspace mode** by default (dmux-like). Start with 1 pane, add worktree panes dynamically via popup picker or CLI.

### How It Works

```bash
# Create first worktree (auto-creates workspace with single pane)
owt create feature/api
# ✅ Created workspace: owt-myproject
# ✅ Added pane for feature/api
# Press prefix+n to add worktree panes on demand.

# Inside tmux, press prefix+n → popup picker appears:
#   Select AI tool → Enter branch name → Optional template
#   → New pane appears to the right

# Or add panes from CLI:
owt pane add --branch bugfix/login --ai-tool claude --workspace owt-myproject --repo /path

# Layout grows on demand:
# Start:          After 1st add:         After 2nd add:
# ┌──────────┐    ┌─────┬──────────┐    ┌─────┬──────────┐
# │          │    │     │ Worktree │    │     │ WT 1     │
# │  Main    │ →  │Main │ 1        │ →  │Main ├──────────┤
# │          │    │     │          │    │     │ WT 2     │
# └──────────┘    └─────┴──────────┘    └─────┴──────────┘
```

### Keybindings (tmux >= 3.2)

| Key | Action |
|-----|--------|
| `prefix + n` | Open popup picker → create worktree + pane |
| `prefix + X` | Close current pane + delete its worktree (with confirmation) |

### Pane Commands

```bash
# Add pane from popup result (used by keybinding internally)
owt pane add --from-popup /tmp/owt-popup-session.json --workspace owt-proj --repo /path

# Add pane directly
owt pane add --branch feature/x --ai-tool claude --workspace owt-proj --repo /path

# Remove pane and delete worktree
owt pane remove --worktree feature/x --workspace owt-proj

# Remove pane but keep worktree
owt pane remove --worktree feature/x --workspace owt-proj --keep-worktree
```

### Workspace Commands

```bash
# List workspaces
owt workspace list

# Show workspace details
owt workspace show owt-myproject

# Attach to workspace
owt workspace attach owt-myproject

# Destroy workspace (keeps worktrees)
owt workspace destroy owt-myproject
```

### Opt-Out: Separate Sessions

Use `--separate-session` for standalone tmux sessions:

```bash
owt create feature/standalone --separate-session
# ✅ tmux session created: owt-feature-standalone
# (Not added to workspace)
```

## Core Workflow

### 1. Create a Worktree (Workspace Mode)

```bash
# Create worktree (adds pane to workspace by default)
owt create feature/new-auth
# ✅ Created workspace or added pane to existing workspace

# Create with Claude in plan mode (safe exploration)
owt create feature/new-auth --plan-mode

# Create separate session (opt-out of workspace mode)
owt create feature/new-auth --separate-session

# Create with workflow template (pre-configured settings)
owt create feature/new-auth --template feature
owt create bugfix/login --template bugfix
owt create research/options --template research

# Create with cost optimization (auto-select cheap AI tool)
owt create feature/simple-fix --template bugfix --auto-optimize

# Create with specific AI tool
owt create feature/api-refactor --tool opencode

# Create with specific tmux layout
owt create bugfix/login --layout three-pane

# Create with CLAUDE.md synced from main repo
owt create feature/new-auth --sync-claude-md
```

This will:
- Create git worktree in configured base directory
- Create/checkout the branch
- Auto-detect project type (Python, Node, Rust, Go, PHP)
- Install dependencies using detected package manager
- Copy and adjust `.env` files
- Copy `CLAUDE.md` from main repo (if `--sync-claude-md` or config enabled)
- Create tmux session with AI tool ready

### 2. Delegate Tasks to Parallel Sessions

```bash
# Send a task to the AI in another worktree
owt send feature/new-auth "Implement JWT authentication middleware"

# Send to multiple worktrees
owt send feature/api "Add rate limiting to all endpoints"
owt send feature/tests "Write integration tests for user registration"
```

### 3. Monitor AI Activity

```bash
# Check status of all worktrees
owt status

# Check specific worktree
owt status feature/new-auth

# Output as JSON for scripting
owt status --json

# Example output:
# ┌──────────────┬─────────┬────────────────────────────┐
# │ Worktree     │ Status  │ Current Task               │
# ├──────────────┼─────────┼────────────────────────────┤
# │ feature/auth │ WORKING │ Implementing JWT middleware│
# │ feature/api  │ IDLE    │ -                          │
# │ bugfix/login │ BLOCKED │ Waiting for clarification  │
# └──────────────┴─────────┴────────────────────────────┘
```

### 4. Workflow Templates

Use pre-configured templates for common workflows:

```bash
# List all available templates
owt template list

# Filter by tags
owt template list --tags security
owt template list --tags development

# Show template details
owt template show bugfix
owt template show feature

# Create worktree with template
owt create bugfix/fix-123 --template bugfix
# ✓ Uses main as base branch
# ✓ Uses three-pane layout
# ✓ Runs: git log --oneline -10, git diff main
# ✓ Sends AI: "Focus on root cause, tests first, minimal changes"

owt create feature/auth --template feature
# ✓ Uses develop as base branch
# ✓ Starts in plan mode
# ✓ Uses quad layout
# ✓ Sends AI: "Plan first, TDD workflow, document as you go"
```

**Built-in Templates:**
- `bugfix` - Quick bugfixes with minimal changes
- `feature` - Full TDD feature development
- `research` - Safe read-only exploration
- `security-audit` - Security review workflow
- `refactor` - Code refactoring with tests
- `hotfix` - Emergency production fixes
- `experiment` - Isolated prototyping
- `docs` - Documentation updates

### 5. Health Monitoring

Check worktree health and detect issues automatically:

```bash
# Check current worktree health
owt health

# Check specific worktree
owt health feature/api

# Check all worktrees
owt health --all

# Custom thresholds
owt health --all --stuck-threshold 60 --cost-threshold 5.0

# JSON output for automation
owt health --all --json
```

**Detected Issues:**
- **Stuck tasks** - Same task for too long (default: 30 min)
- **High token usage** - Possible infinite loops (> 100K tokens)
- **High costs** - Expensive sessions (> $10 USD)
- **Repeated errors** - Multiple failed commands
- **Stale worktrees** - No activity for days (default: 7 days)
- **Idle too long** - No productive work (> 24 hours)
- **Blocked state** - AI is blocked and needs guidance

**Example Output:**
```
✗ feature/api-refactor

Critical Issues:
  ✗ Very high token usage detected: 120,000 tokens
    → Check for infinite loops or consider switching to a cheaper AI tool

Warnings:
  ⚠ High cost session: $15.50
    → Consider switching to a cheaper AI tool (claude-haiku, gpt-4o-mini)
```

### 6. Cost Optimization

Compare AI tool costs and find savings:

```bash
# Show cost comparison for current worktree
owt cost

# Show cost for specific worktree
owt cost feature/api

# JSON output
owt cost --json

# Create with automatic cost optimization
owt create feature/simple --template bugfix --auto-optimize
```

**Example Output:**
```
Cost Analysis: feature/api

Current AI tool: claude-opus
Total tokens: 45,320
Current cost: $4.0740

Cost by AI Tool:
  opencode             $0.0000
  claude-haiku         $0.0680
  gpt-4o-mini          $0.0611
  claude-sonnet        $0.8154
  gpt-4o               $1.3580
→ claude-opus          $4.0740

💰 Potential Savings:
  Cheapest: claude-haiku ($0.0680)
  Savings: $4.0060 (98.3%)

  Tip: Use --auto-optimize when creating new worktrees to save costs
```

### 7. Switch Between Worktrees

```bash
# Switch to worktree directory
owt switch feature/new-auth

# Attach to tmux session directly
owt switch feature/new-auth --tmux
```

### 5. Cleanup and Maintenance

```bash
# Preview stale worktrees
owt cleanup --dry-run

# Remove worktrees older than 14 days (default)
owt cleanup

# Output cleanup report as JSON
owt cleanup --json

# Sync all worktrees with upstream
owt sync --all

# Sync specific worktree
owt sync feature/new-auth

# Output sync report as JSON
owt sync --all --json
```

## Live Dashboard

Monitor all worktrees in real-time with an interactive TUI dashboard.

```bash
# Launch live dashboard (updates every 2 seconds)
owt dashboard

# Custom refresh rate (1 second)
owt dashboard -r 1
owt dashboard --refresh 1

# Compact mode (minimal UI without summary panel)
owt dashboard --compact

# Hide token usage columns
owt dashboard --no-tokens

# Combine options
owt dashboard -r 1 --compact --no-tokens
```

The dashboard shows:
- Worktree name and branch
- AI status (WORKING, IDLE, BLOCKED, etc.)
- Current task description
- Token usage and estimated costs
- PR link status (if linked)

## Token Tracking

Track token usage and costs per worktree.

```bash
# View token usage for all worktrees
owt tokens show

# View specific worktree
owt tokens show feature/new-auth

# Manually update token counts (if auto-tracking disabled)
owt tokens update feature/new-auth --input 1000 --output 500

# Reset token counts for a worktree
owt tokens reset feature/new-auth

# Reset all worktrees
owt tokens reset --all

# Output as JSON
owt tokens show --json
```

## GitHub PR Integration

Link worktrees to GitHub pull requests for integrated workflow.

```bash
# Link worktree to PR
owt pr link feature/new-auth --pr 123

# Show PR status for all linked worktrees
owt pr status

# Show status for specific worktree
owt pr status feature/new-auth

# Refresh PR info from GitHub
owt pr refresh feature/new-auth

# Refresh all linked worktrees
owt pr refresh --all

# Clean up worktrees whose PRs have been merged
owt pr cleanup

# Preview what would be cleaned
owt pr cleanup --dry-run
```

## Status Change Hooks

Configure notifications and webhooks when AI status changes.

```bash
# List configured hooks
owt hooks list

# Add a hook
owt hooks add notify-blocked --type on_blocked --action notification --title "Claude Blocked"
owt hooks add log-changes --type on_status_changed --action shell --command "echo $OWT_WORKTREE"

# Test hook execution
owt hooks test <hook-name>

# View hook execution history
owt hooks history

# Enable/disable hooks
owt hooks enable <hook-name>
owt hooks disable <hook-name>

# Remove a hook
owt hooks remove <hook-name>
```

Hook configuration is stored in `~/.open-orchestrator/hooks.json`.

### Hook Events
- `status_change` - When AI status changes (IDLE → WORKING, etc.)
- `task_complete` - When a task is marked complete
- `blocked` - When AI becomes blocked
- `error` - When an error occurs

### Hook Actions
- `notify` - Send desktop notification
- `webhook` - POST to URL
- `command` - Run shell command

## Session Management

Copy and resume Claude sessions across worktrees.

```bash
# Copy session from one worktree to another
owt copy-session feature/auth feature/auth-v2

# Get command to resume session in a worktree
owt resume feature/new-auth

# View session info
owt session feature/new-auth

# Example output shows session ID and resume command:
# Session ID: abc123
# Resume with: claude --resume abc123
```

## No-tmux Mode (Process Management)

Run AI tools as background processes without tmux.

```bash
# Start AI tool as background process
owt process start feature/new-auth

# Start with specific AI tool
owt process start feature/new-auth --tool opencode

# List running AI processes
owt process list

# View process logs
owt process logs feature/new-auth

# Follow logs in real-time
owt process logs feature/new-auth --follow

# Stop a process
owt process stop feature/new-auth

# Stop all processes
owt process stop --all
```

## Shell Completion

Enable auto-completion for bash, zsh, or fish.

```bash
# Show installation instructions for your shell
owt completion install

# Generate bash completion script
owt completion bash

# Generate zsh completion script
owt completion zsh

# Generate fish completion script
owt completion fish

# Install to default location (auto-detected shell)
owt completion install --auto
```

## Configuration

### Config File Locations (Priority Order)
1. `--config` flag
2. `.worktreerc` in current directory
3. `.worktreerc.toml`
4. `~/.config/open-orchestrator/config.toml`
5. `~/.worktreerc`

### Sample Configuration

```toml
[worktree]
base_directory = "../"                    # Relative to repo root
naming_pattern = "{project}-{branch}"     # Worktree naming
auto_cleanup_days = 14                    # Stale threshold
sync_claude_md = true                     # Copy CLAUDE.md to new worktrees

[tmux]
default_layout = "single"                 # single (on-demand), main-vertical, three-pane, quad
auto_start_ai = true
ai_tool = "claude"                        # claude, opencode, droid
pane_count = 2

[environment]
auto_install_deps = true
copy_env_file = true
adjust_env_paths = true

[dashboard]
refresh_rate = 2                          # Seconds between updates
show_tokens = true                        # Show token usage columns
compact = false                           # Use compact UI mode

[tokens]
auto_track = true                         # Automatically track token usage
cost_per_1k_input = 0.003                 # Cost per 1K input tokens
cost_per_1k_output = 0.015                # Cost per 1K output tokens

[droid]
default_auto_level = "medium"             # low, medium, high

[opencode]
config_path = "~/.config/opencode.json"
```

### Hook Configuration

Hooks are stored in `~/.open-orchestrator/hooks.json`:

```json
{
  "hooks": [
    {
      "id": "slack-notify",
      "event": "status_change",
      "action": "webhook",
      "target": "https://hooks.slack.com/...",
      "filter": {
        "status": ["BLOCKED", "ERROR"]
      }
    }
  ]
}
```

## tmux Layouts

| Layout | Description |
|--------|-------------|
| `single` | Single pane, on-demand mode (default for workspaces) |
| `main-vertical` | Large left pane (editor), smaller right panes |
| `main-focus` | 1/3 left main + right column of worktree panes |
| `three-pane` | Main top pane, two bottom panes |
| `quad` | Four equal panes |
| `even-horizontal` | Equal horizontal split |
| `even-vertical` | Equal vertical split |

## Project Detection

Automatically detects project type and package manager:

| Type | Detection | Package Manager Priority |
|------|-----------|-------------------------|
| Python | `pyproject.toml`, `uv.lock`, `requirements.txt` | uv > poetry > pipenv > pip |
| Node.js | `package.json`, `bun.lockb`, `pnpm-lock.yaml` | bun > pnpm > yarn > npm |
| Rust | `Cargo.toml` | cargo |
| Go | `go.mod` | go |
| PHP | `composer.json` | composer |

## AI Tool Support

Open Orchestrator **auto-detects installed AI tools** and offers a picker when multiple are found. Supported tools:

| Tool | Detection | Notes |
|------|-----------|-------|
| Claude Code | `claude` binary | Default, supports plan mode |
| OpenCode | `opencode` binary | Go-based |
| Droid | `droid` binary | Supports autonomy levels |
| Codex | `codex` binary | OpenAI CLI |
| Gemini CLI | `gemini` binary | Google CLI |
| Aider | `aider` binary | Terminal-based pair programming |
| Amp | `amp` binary | Sourcegraph CLI |
| Kilo Code | `kilo-code` binary | VS Code extension CLI |

```bash
# Auto-detect and use default tool
owt new "add user auth"

# Specify tool explicitly
owt create feature/x --ai-tool claude --plan-mode
owt create feature/x --ai-tool opencode
owt create feature/x --ai-tool droid --auto-level high
```

## Common Patterns

### Pattern 1: On-Demand Workspace Development (Default)
```bash
# Create first worktree (creates workspace with single pane)
owt create feature/api

# Add more panes on demand from inside tmux:
# Press prefix+n → select AI tool → enter branch → pane appears

# Or add from CLI:
owt pane add --branch bugfix/auth --workspace owt-myproject --repo .
owt pane add --branch research/perf --workspace owt-myproject --repo .

# Your workspace grows naturally:
# ┌─────┬──────────┐
# │     │ feature/api       │
# │Main ├──────────┤
# │     │ bugfix/auth       │
# │     ├──────────┤
# │     │ research/perf     │
# └─────┴──────────┘

# Remove a pane: prefix+X (with confirmation)
# Navigate: Ctrl+b → arrow keys or click (mouse enabled)
```

### Pattern 2: Template-Based Development
```bash
# Use templates for consistent workflows
owt template list

# Create bugfix with pre-configured settings
owt create bugfix/memory-leak --template bugfix
# Auto-runs: git log, git diff main
# AI gets: "Focus on root cause, tests first, minimal changes"

# Create feature with TDD workflow
owt create feature/payments --template feature
# Starts in plan mode
# AI gets: "Plan first, implement with tests, document"

# Create with cost optimization
owt create feature/simple --template bugfix --auto-optimize
# Auto-selects claude-haiku instead of opus (85% cheaper)
```

### Pattern 3: Health-Monitored Development
```bash
# Create multiple worktrees
owt create feature/api --template feature
owt create feature/frontend --template feature
owt create bugfix/critical --template hotfix

# Monitor health across all worktrees
owt health --all

# Check specific worktree if issues detected
owt health feature/api

# Use dashboard for real-time monitoring
owt dashboard
```

### Pattern 4: Cost-Aware Development
```bash
# Check current costs
owt cost feature/api

# Create new worktrees with cost optimization
owt create feature/docs --template docs --auto-optimize
# Uses claude-haiku for simple documentation tasks

owt create feature/complex --template feature
# Uses claude-opus for complex features (quality over cost)

# Review savings across all worktrees
owt tokens show
```

### Pattern 5: Parallel Feature Development
```bash
# Main worktree: Core feature
owt create feature/payments

# Delegate related work
owt send feature/payments "Build Stripe integration service"
owt create feature/payments-tests
owt send feature/payments-tests "Write tests for payment service"
```

### Pattern 6: Bug Investigation + Fix
```bash
# Create investigation worktree
owt create bugfix/memory-leak

# Send investigation task
owt send bugfix/memory-leak "Profile memory usage and identify leaks in user service"

# Check status periodically
owt status bugfix/memory-leak
```

### Pattern 7: Refactoring Across Modules
```bash
owt create refactor/api-layer
owt create refactor/data-layer
owt create refactor/tests

owt send refactor/api-layer "Migrate REST endpoints to use new DTO pattern"
owt send refactor/data-layer "Implement repository pattern for database access"
owt send refactor/tests "Update all integration tests for new patterns"
```

### Pattern 8: PR-Centric Development
```bash
# Create worktree and link to PR
owt create feature/payments
owt pr link feature/payments --pr 456

# Delegate work
owt send feature/payments "Implement payment processing"

# Monitor with PR status visible
owt status

# After PR merged, cleanup
owt pr cleanup
```

### Pattern 9: Monitored Parallel Development
```bash
# Terminal 1: Launch dashboard for real-time monitoring
owt dashboard

# Terminal 2+: Create and delegate work
owt create feature/auth
owt create feature/api
owt send feature/auth "Build OAuth flow"
owt send feature/api "Add REST endpoints"

# Dashboard auto-updates showing all activity
```

### Pattern 10: No-tmux Workflow
```bash
# Create worktree without tmux session
owt create feature/simple --no-tmux

# Start AI as background process
owt process start feature/simple

# Check progress via logs
owt process logs feature/simple

# When done, stop the process
owt process stop feature/simple
```

### Pattern 11: Session Continuity
```bash
# Work on feature, then need to restart
owt create feature/complex-refactor
owt send feature/complex-refactor "Start refactoring auth module"

# Later, resume the exact session
owt resume feature/complex-refactor
# Outputs: claude --resume abc123

# Or copy session to new worktree
owt copy-session feature/complex-refactor feature/complex-refactor-v2
```

## tmux Commands

Direct tmux management when needed:

```bash
# List all owt sessions
owt tmux list

# Attach to session
owt tmux attach feature-new-auth

# Kill specific session
owt tmux kill feature-new-auth

# Create session manually
owt tmux create feature-new-auth --layout quad
```

## Status Tracking

Status is persisted in `~/.open-orchestrator/ai_status.json`:

```bash
# View detailed status
owt status --verbose

# Status values:
# - IDLE: AI ready for tasks
# - WORKING: AI actively processing
# - BLOCKED: Waiting for input/clarification
# - WAITING: Paused, awaiting external dependency
# - COMPLETED: Task finished
# - ERROR: Something went wrong
```

## JSON Output

Most commands support `--json` for machine-readable output:

```bash
owt status --json
owt list --json
owt cleanup --json
owt sync --all --json
owt tokens show --json
owt pr status --json
```

## Troubleshooting

### Worktree Creation Fails
```bash
# Check git worktree state
git worktree list

# Prune stale entries
git worktree prune

# Retry creation
owt create feature/x
```

### AI Tool Not Starting
```bash
# Verify tool installation
which claude
which opencode
which droid

# Check tmux session
owt tmux list
tmux attach -t owt-feature-x
```

### Dependencies Not Installing
```bash
# Check project detection
owt create feature/x --verbose

# Manual install in worktree
cd $(owt switch feature/x --path-only)
uv sync  # or npm install, etc.
```

### Dashboard Not Showing Data
```bash
# Check status file exists
cat ~/.open-orchestrator/ai_status.json

# Verify worktrees exist
owt list --all

# Try verbose mode
owt dashboard --verbose
```

## Integration with Claude Code

Use the slash commands in Claude Code sessions:

- `/worktree` - Main orchestration command
- `/wt-create` - Quick worktree creation
- `/wt-list` - List all worktrees
- `/wt-status` - Check AI activity
- `/wt-cleanup` - Clean stale worktrees

## References

- [CLI Reference](references/cli-reference.md) - Complete command documentation
- [Configuration](references/configuration.md) - All config options
- [Workflows](references/workflows.md) - Advanced workflow patterns

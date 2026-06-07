# Configuration

How Open Orchestrator is configured. For an overview, see the [README](../README.md). For multiplexer backend details (`[backend]`), see [multiplexer-backends.md](multiplexer-backends.md).

## Config File Discovery

Config files are loaded in priority order:
1. `.worktreerc` in current directory
2. `.worktreerc.toml`
3. `~/.config/open-orchestrator/config.toml`
4. `~/.worktreerc`

## Schema

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

## Environment Variables

| Variable | Set by | Purpose |
|----------|--------|---------|
| `OWT_AUTOMATED` | OWT (in automated panes) | Lets user hooks distinguish automated agents from interactive sessions. Check `[ -n "$OWT_AUTOMATED" ]` in hooks to skip restrictions for agents. |
| `OWT_WORKTREE_NAME` | OWT (in all panes) | Current worktree name. Used by MCP peer servers and hooks for identification. |
| `OWT_DB_PATH` | OWT hook/MCP wiring or user override | Points hooks, MCP peer servers, and in-process status tracking at the same SQLite DB. If `~/.open-orchestrator/status.db` is not writable, owt falls back to repo-local or temp-backed storage. |
| `OWT_BACKGROUND` | OWT (auto-detected) or user override | Terminal background hex color for the control plane. Auto-detected via OSC 11 at launch; set manually if detection fails. |

## AI Tool Support

Open Orchestrator auto-detects installed AI tools and offers a picker when multiple are found:

| Tool | Binary | Notes |
|------|--------|-------|
| Claude Code | `claude` | Default, `--dangerously-skip-permissions`; automated/headless agents use `-p` with cat-piped prompts |
| Pi | `pi` | `npm install -g @earendil-works/pi-coding-agent`; automated/headless agents use `-p` with cat-piped prompts; live status via pane scraping |
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

## Conflict Guard

Conflict Guard is always on — no configuration needed. owt tracks the files each worktree modifies (in the status DB) and reports overlaps between worktrees so you see brewing collisions before they reach a merge:

- The **READY TO SHIP** lane and `owt queue` show a per-worktree overlap count.
- Active merge conflicts (MERGE_HEAD / rebase-in-progress) surface in the **NEEDS YOU** lane.

The detection lives in `MergeManager.check_file_overlaps()` / `plan_merge_order()` — pure and AI-free.

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

Open Orchestrator - Implementation Plan

 Overview

 A Git Worktree + Claude Code orchestration tool combining a Python CLI with Claude Code plugin integration for managing
 parallel development workflows.

 Core Value Proposition: Enable developers to work on multiple tasks simultaneously by creating isolated worktrees, each with
  its own Claude Code session and tmux pane.

 ---
 Project Structure

 /Users/pedrolopes/dev/projects/open-orchestrator/
 ├── src/
 │   └── open_orchestrator/
 │       ├── __init__.py
 │       ├── cli.py                     # Main CLI entry point (click)
 │       ├── config.py                  # Configuration management
 │       ├── core/
 │       │   ├── worktree.py            # Git worktree operations
 │       │   ├── project_detector.py    # Project type detection
 │       │   ├── environment.py         # Dependency & .env setup
 │       │   ├── tmux_manager.py        # tmux session management
 │       │   └── cleanup.py             # Worktree cleanup/maintenance
 │       ├── models/
 │       │   ├── worktree_info.py       # Pydantic models
 │       │   └── project_config.py      # Configuration models
 │       └── utils/
 │           ├── git_utils.py           # Git helper functions
 │           ├── path_utils.py          # Path utilities
 │           └── logger.py              # Structured logging
 ├── tests/
 │   ├── test_worktree.py
 │   ├── test_project_detector.py
 │   └── conftest.py
 ├── scripts/
 │   └── context-injector.py            # Hook script
 ├── .claude/
 │   ├── commands/
 │   │   ├── worktree.md                # /worktree main command
 │   │   ├── wt-create.md               # /wt-create shortcut
 │   │   ├── wt-list.md                 # /wt-list shortcut
 │   │   └── wt-cleanup.md              # /wt-cleanup shortcut
 │   ├── settings.json                  # Hooks configuration
 │   └── CLAUDE.md                      # Project instructions
 ├── pyproject.toml
 ├── .worktreerc.example
 └── README.md

 ---
 CLI Commands

 | Command             | Description                              |
 |---------------------|------------------------------------------|
 | owt create <branch> | Create worktree with deps & tmux session |
 | owt list            | List all worktrees with status           |
 | owt switch <name>   | Switch to worktree & attach tmux         |
 | owt delete <name>   | Delete worktree & cleanup                |
 | owt cleanup         | Remove stale worktrees                   |
 | owt sync [--all]    | Sync worktree(s) with upstream           |
 | owt tmux create     | Create tmux session for worktree         |
 | owt tmux attach     | Attach to existing session               |
 | owt tmux list       | List worktree tmux sessions              |

 ---
 Implementation Steps

 Phase 1: Project Setup & Core CLI

 Step 1.1: Initialize Project
 - Create pyproject.toml with dependencies: click, pydantic, rich, gitpython, libtmux, toml
 - Configure uv for development
 - Set up directory structure

 Step 1.2: Implement WorktreeManager
 - File: src/open_orchestrator/core/worktree.py
 - Methods: create(), list_all(), delete(), _find_worktree()
 - Use gitpython for git operations
 - Generate worktree paths: {project}-{branch-name}

 Step 1.3: Implement CLI Entry Point
 - File: src/open_orchestrator/cli.py
 - Commands: create, list, delete
 - Use click decorators
 - Use rich for output formatting

 Phase 2: Project Detection & Environment

 Step 2.1: Implement ProjectDetector
 - File: src/open_orchestrator/core/project_detector.py
 - Detect: Python (uv/pip/poetry), Node (npm/yarn/pnpm/bun), PHP, Rust, Go
 - Map project types to install commands

 Step 2.2: Implement EnvironmentSetup
 - File: src/open_orchestrator/core/environment.py
 - Methods: install_dependencies(), setup_env_file()
 - Copy .env with path adjustments

 Phase 3: tmux Integration

 Step 3.1: Implement TmuxManager
 - File: src/open_orchestrator/core/tmux_manager.py
 - Use libtmux library
 - Methods: create_session(), attach(), list_sessions(), kill_session()
 - Layouts: main-vertical, three-pane, quad

 Step 3.2: Add tmux CLI Subcommands
 - Add tmux command group to CLI
 - Commands: create, attach, list

 Step 3.3: Integrate tmux with Worktree Creation
 - Auto-create tmux session on owt create
 - Auto-start Claude Code in first pane

 Phase 4: Claude Code Plugin

 Step 4.1: Create Slash Commands
 - /worktree - Main command wrapper
 - /wt-create - Quick create shortcut
 - /wt-list - List with formatting
 - /wt-cleanup - Cleanup stale worktrees

 Step 4.2: Configure Hooks
 - UserPromptSubmit - Inject worktree context
 - Stop - Sync on session end (quiet)

 Step 4.3: Create Project CLAUDE.md
 - Document commands and workflows
 - Reference guidelines

 Phase 5: Maintenance Features

 Step 5.1: Implement Cleanup Service
 - File: src/open_orchestrator/core/cleanup.py
 - Track usage statistics
 - Detect stale worktrees (configurable days)
 - Dry-run support

 Step 5.2: Implement Sync Command
 - Sync single or all worktrees
 - Handle upstream tracking

 Phase 6: Testing & Documentation

 Step 6.1: Write Unit Tests
 - Test worktree operations
 - Test project detection
 - Test configuration loading

 Step 6.2: Write Documentation
 - README with installation and usage
 - Example configurations

 ---
 Critical Files to Implement

 1. pyproject.toml - Project config with dependencies
 2. src/open_orchestrator/cli.py - CLI entry point
 3. src/open_orchestrator/core/worktree.py - Core worktree logic
 4. src/open_orchestrator/core/project_detector.py - Project detection
 5. src/open_orchestrator/core/tmux_manager.py - tmux management
 6. src/open_orchestrator/core/environment.py - Environment setup
 7. src/open_orchestrator/config.py - Configuration management
 8. .claude/commands/worktree.md - Main slash command
 9. .claude/settings.json - Hooks configuration

 ---
 Dependencies

 [project]
 dependencies = [
     "click>=8.1.0",
     "pydantic>=2.0.0",
     "rich>=13.0.0",
     "toml>=0.10.0",
     "gitpython>=3.1.0",
     "libtmux>=0.25.0",
 ]

 ---
 Configuration Format (.worktreerc)

 [worktree]
 base_directory = "../"
 naming_pattern = "{project}-{branch}"
 auto_cleanup_days = 14

 [tmux]
 default_layout = "main-vertical"
 auto_start_claude = true
 pane_count = 2

 [environment]
 auto_install_deps = true
 copy_env_file = true

 ---
 Slash Command Pattern

 ---
 allowed-tools: Bash
 description: Create a new git worktree with full environment setup
 ---

 # Create Worktree

 $ARGUMENTS - Branch name (e.g., feature/add-login)

 ## Context
 - Current branch: !`git branch --show-current`
 - Worktrees: !`git worktree list`

 ## Instructions
 1. Run: `owt create $ARGUMENTS`
 2. Report created path and status

 ---
 Bootstrap Strategy: Parallel Development

 To build this tool using multiple Claude Code windows from the start:

 Initial Setup (Before Tool Exists)

 1. Create worktrees manually for each feature track:
 # From main project directory
 cd /Users/pedrolopes/dev/projects/open-orchestrator
 git init  # If new repo

 # Create worktrees for parallel development
 git worktree add ../open-orchestrator-core core/worktree
 git worktree add ../open-orchestrator-cli core/cli
 git worktree add ../open-orchestrator-tmux feature/tmux
 git worktree add ../open-orchestrator-plugin feature/plugin
 2. Create tmux sessions manually:
 # Create sessions for each worktree
 tmux new-session -d -s co-core -c ~/dev/projects/open-orchestrator-core
 tmux new-session -d -s co-cli -c ~/dev/projects/open-orchestrator-cli
 tmux new-session -d -s co-tmux -c ~/dev/projects/open-orchestrator-tmux
 tmux new-session -d -s co-plugin -c ~/dev/projects/open-orchestrator-plugin
 3. Start Claude Code in each:
 tmux send-keys -t co-core "claude" Enter
 tmux send-keys -t co-cli "claude" Enter
 tmux send-keys -t co-tmux "claude" Enter
 tmux send-keys -t co-plugin "claude" Enter
 4. Attach to sessions as needed:
 tmux attach -t co-core
 # Detach with Ctrl+B, D
 tmux attach -t co-cli

 Parallel Development Tracks

 | Track  | Worktree                   | Focus                            |
 |--------|----------------------------|----------------------------------|
 | Core   | open-orchestrator-core   | worktree.py, project_detector.py |
 | CLI    | open-orchestrator-cli    | cli.py, config.py, models        |
 | tmux   | open-orchestrator-tmux   | tmux_manager.py                  |
 | Plugin | open-orchestrator-plugin | .claude/commands/, hooks         |

 Quick Bootstrap Script

 Create this script to set up everything at once:

 #!/bin/bash
 # bootstrap-dev.sh - Set up parallel development environment

 PROJECT_DIR="/Users/pedrolopes/dev/projects/open-orchestrator"
 PARENT_DIR="$(dirname "$PROJECT_DIR")"

 # Initialize git if needed
 cd "$PROJECT_DIR"
 [ ! -d .git ] && git init && git add -A && git commit -m "Initial commit"

 # Create worktrees
 git worktree add "$PARENT_DIR/open-orchestrator-core" -b core/worktree main 2>/dev/null || true
 git worktree add "$PARENT_DIR/open-orchestrator-cli" -b core/cli main 2>/dev/null || true
 git worktree add "$PARENT_DIR/open-orchestrator-tmux" -b feature/tmux main 2>/dev/null || true
 git worktree add "$PARENT_DIR/open-orchestrator-plugin" -b feature/plugin main 2>/dev/null || true

 # Create tmux sessions with Claude Code
 for track in core cli tmux plugin; do
     session="co-$track"
     dir="$PARENT_DIR/open-orchestrator-$track"

     if ! tmux has-session -t "$session" 2>/dev/null; then
         tmux new-session -d -s "$session" -c "$dir"
         tmux send-keys -t "$session" "claude" Enter
         echo "Created session: $session"
     fi
 done

 echo "Worktrees and sessions ready! Use: tmux attach -t co-core"

 Self-Hosting Milestone

 Once Phase 1-3 complete, switch to using the tool itself:
 # Install the tool in dev mode
 cd /Users/pedrolopes/dev/projects/open-orchestrator
 uv pip install -e .

 # Now use owt instead of manual commands
 owt create feature/cleanup
 owt list
 owt switch feature/cleanup

 ---
 Success Criteria

 - owt create feature/test creates worktree with deps installed
 - owt list shows all worktrees with status
 - owt switch <name> attaches to tmux session
 - /worktree create works in Claude Code
 - tmux sessions auto-created with Claude Code running
 - Stale worktrees detected and cleaned
 - Tool can be used to continue its own development (self-hosting)
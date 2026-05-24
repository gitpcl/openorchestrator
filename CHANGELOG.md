# Changelog

All notable changes to **open-orchestrator** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `AIActivityStatus.STALLED` enum value plus `WorktreeAIStatus.mark_stalled` and
  `StatusTracker.mark_stalled` for flagging subprocess-timeout casualties.
- `core/_subprocess.run_with_class_timeout` — single chokepoint that routes
  every subprocess call through the correct timeout class (tmux / git / gh /
  ai_cli / fast) and fires an `on_timeout` callback before re-raising.
- Structured JSON heartbeats from the dream daemon (`core/dream.py`); tail with
  `jq` against the daemon log.
- `pip-audit` step + Dependabot configuration in CI; macOS runner added to the
  test matrix; coverage ratchet enforced at 68% with a path to 80%.
- Authoritative project metadata: author email, `Source`, and `Funding` URLs
  in `pyproject.toml`.
- `CHANGELOG.md` (this file), backfilled from git tags.

### Changed
- Every `subprocess.run` / `check_call` / `check_output` site under `src/` now
  carries an explicit `timeout=` argument; the regression guard fails CI if a
  new site omits one.
- `SyncService` accepts an optional `StatusTracker`; a git timeout during
  `sync_worktree` now flips the worktree to `STALLED` with a friendly message.
- All SQLite connections route through `core/_db.open_db`, which sets WAL +
  `busy_timeout=5000` + `synchronous=NORMAL` consistently — no more
  `database is locked` under contention from the switchboard + dream + hooks.
- `mcp_peer.py` is now fully type-checked (mypy override removed).
- Textual floor in `pyproject.toml` reconciled to `>=8.0.0` to match the
  ship lockfile.

### Fixed
- Subprocess timeouts surface as `TimeoutExpired` with a logged class +
  command head instead of hanging the switchboard or dream daemon
  indefinitely.

## [0.4.0] — 2026-05

### Added
- Multi-palette theme system with terminal capability detection
  (`feat(theme): multi-palette system with terminal detection`).
- Swarm mode: coordinator + specialist worker fan-out for parallel exploration.
- Recall stack: SQLite memory store with FTS5 + knowledge graph, AAAK
  compression codec, fact miner, dream-mode integration, and automatic
  `CLAUDE.md` injection.
- Pi tool support: registry entry, detection, and prompt delivery wired
  through the same launcher path as Claude / opencode / droid.
- Branch mode (`SessionType.BRANCH`, `--in-place` flag, `owt branch` alias,
  branch-aware `merge` / `ship`, switchboard prompting, 24 lifecycle tests).
- Control-plane switchboard layout replacing the card grid.
- Optional herdr multiplexer backend (Sprint 025) with submit-mode override.

### Changed
- AgentLauncher unifies worktree + agent provisioning; the tool registry is
  the single source of truth.
- Activity status policy centralized; `WAITING` now treated as terminal in
  `owt wait` / headless flows.

### Fixed
- Worktree base branch resolved to a SHA before `git worktree add` to avoid
  ambiguous-ref failures.
- Headless mode rejects non-Claude providers and forwards `plan_mode`
  correctly; template prepended to the first prompt.
- Three Dependabot CVEs patched.

## [0.3.0] — 2026-04

### Added
- Initial v0.3.0 release line (memory subsystem foundations, MCP peer support,
  CI typing tightening). See `git log v0.2.0..v0.3.0` for the full history.

## [0.2.0] — 2026-03-14

### Added
- CONTRIBUTING guide.
- PyPI / CI / license / download badges in the README.
- GitHub Actions workflow (`test + lint` matrix across Python 3.10 → 3.12).
- Demo GIF (mocked CLI + switchboard recording).
- Textual + SQLite + async refactor of the switchboard (replaces the
  earlier `curses` + JSON file-locking design).
- "Killer features": conflict guard, autopilot, broadcast, queue, shared
  notes, headless mode, dashboard.
- Push-based status detection via AI-tool hooks; staleness timeout for the
  hook trust window; idle-state detection from Claude Code's status bar.
- `owt ship` (commit + merge + delete in one shot), live status detection,
  `--skip-permissions`, global keybindings.
- Bug-report + feature-request issue templates.

### Fixed
- Switchboard no longer false-positives `BLOCKED` while an agent is thinking.
- Pane scraping skipped for hook-capable tools in the `WORKING` state.
- `git diff` skipped for missing worktree paths.

## [0.1.0] — 2026-02-05

### Added
- Initial public release with unified workspace mode.
- `owt version` (plain + `--full`) and `owt update` (`--check`, `--version`)
  commands; `Updater` class with GitHub releases integration; `__version__.py`
  module.
- README and SKILL.md initial coverage of update commands and feature listing.

[Unreleased]: https://github.com/gitpcl/openorchestrator/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/gitpcl/openorchestrator/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/gitpcl/openorchestrator/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/gitpcl/openorchestrator/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/gitpcl/openorchestrator/releases/tag/v0.1.0

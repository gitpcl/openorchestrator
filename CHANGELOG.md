# Changelog

All notable changes to **open-orchestrator** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **ClawCore provider** — `owt new --ai-tool clawcore "<task>"` launches
  [ClawCore](https://github.com/clawco-io/clawcore), a code-as-action engine,
  as a one-shot agent: OWT runs `clawcore run "<task>" "<worktree>" --json`
  with the task as a positional argument and skips the prompt paste.
- **Task-via-args tool capability** — the tool model now supports one-shot
  agents whose task is passed as argv rather than pasted into a TUI. Tools
  (built-in or `[tools.<name>]` config) can set `task_via_args = true` and use
  `{{task}}` / `{{worktree}}` placeholders in `command_template`; both are
  shell-quoted and substituted, and the prompt paste/stdin write is skipped on
  the interactive, automated, and headless launch paths. The REPL paste/stdin
  path for Claude/Pi/Droid/OpenCode is unchanged.

### Security
- Resolved all 12 Dependabot advisories. Bumped the `gitpython` floor to
  `>=3.1.50` (CVE-2026-42215 / -42284 / -44244 / GHSA-mv93-w799-cj2w — the only
  vuln in a default install) and re-locked the optional `[mcp]`-extra and dev
  transitives to their patched releases: `python-multipart` 0.0.29,
  `urllib3` 2.7.0, `cryptography` 48.0.0, `idna` 3.15, `starlette` 1.0.1,
  `pyjwt` 2.13.0. The project `[tool.uv] exclude-newer` cutoff was advanced to
  `2026-05-22` (just past the latest patch) so the resolver admits the fixes
  while staying reproducible. Full gate stays green (1274 tests, 83.54% cov).

## [0.5.0] — 2026-06

Repositioned open-orchestrator as a **multi-provider cockpit**: supervise
Claude Code, Pi, Droid, and OpenCode across isolated git worktrees from one
control plane. The engine features that competed with the AI platforms
themselves were deliberately cut (~18k lines); see **Removed**. This release
also folds in the Sprint 027 production-readiness hardening.

### Removed (breaking)
- Commands cut: `plan`, `orchestrate`, `swarm`, `batch`, `critic`, `memory`,
  `dream` — the AI planning/DAG, batch autopilot, critic safety-review,
  recall/memory, and dream daemon subsystems are gone.
- The Agno intelligence layer and its optional `[agno]` extra.
- The legacy switchboard UI and the `owt --legacy-cards` flag; the control
  plane is now the only board.
- Config keys, each reported with a friendly "removed in 0.5.0, safe to
  delete" migration message (never a typo hint): `[switchboard]`,
  `[agno]`/`[intelligence]`, `critic`/`critic_enabled`,
  `recall`/`recall_enabled`/`memory`, `swarm`, and the `dream_*` family.

### Added
- `owt new --workflow` — launch a native plan-first Claude Code workflow
  (plan mode + plan-then-execute protocol) directly in the worktree.
- `owt usage [--days N]` — local-only usage counts (control-plane launches,
  worktrees started, native workflows), backed by a new `usage_events` table.
  Nothing leaves the machine; it's a keep-or-archive gauge.
- `AIActivityStatus.STALLED` enum value plus `WorktreeAIStatus.mark_stalled` and
  `StatusTracker.mark_stalled` for flagging subprocess-timeout casualties.
- `core/_subprocess.run_with_class_timeout` — single chokepoint that routes
  every subprocess call through the correct timeout class (tmux / git / gh /
  ai_cli / fast) and fires an `on_timeout` callback before re-raising.
- `pip-audit` step + Dependabot configuration in CI; macOS runner added to the
  test matrix.
- Authoritative project metadata: author email, `Source`, and `Funding` URLs
  in `pyproject.toml`.
- `CHANGELOG.md` (this file), backfilled from git tags.

### Changed
- Repositioned to a multi-provider cockpit; the control plane went from four
  lanes to three (NEEDS YOU / READY TO SHIP / IN FLIGHT).
- `pyproject.toml` description + keywords reframed around the cockpit
  (cockpit / control-plane / multi-provider), dropping the engine
  "orchestration" framing.
- CI coverage floor re-baselined to the true post-cut total (`--cov-fail-under`
  raised to **83%**); the cut removed several sub-80% modules and their tests.
- Every `subprocess.run` / `check_call` / `check_output` site under `src/` now
  carries an explicit `timeout=` argument; the regression guard fails CI if a
  new site omits one.
- `SyncService` accepts an optional `StatusTracker`; a git timeout during
  `sync_worktree` now flips the worktree to `STALLED` with a friendly message.
- All SQLite connections route through `core/_db.open_db`, which sets WAL +
  `busy_timeout=5000` + `synchronous=NORMAL` consistently — no more
  `database is locked` under contention from the control plane + hooks.
- `mcp_peer.py` is now fully type-checked (mypy override removed).
- Textual floor in `pyproject.toml` reconciled to `>=8.0.0` to match the
  ship lockfile.
- PyPI classifier: Alpha → Beta (`Development Status :: 4 - Beta`).
- Dependency bumps: `click` 8.3.1 → 8.3.2, `pytest` 9.0.2 → 9.0.3, `textual` 8.1.1 → 8.2.3, `pydantic` 2.12.2 → 2.12.5 (pydantic-core pinned at 2.41.5).

### Fixed
- Subprocess timeouts surface as `TimeoutExpired` with a logged class +
  command head instead of hanging the control plane or a daemon indefinitely.

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

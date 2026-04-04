"""Switchboard card data, constants, status detection, and rendering.

Extracted from switchboard.py to keep file sizes manageable.
Provides the Card dataclass, status detection helpers, card building,
and rendering functions used by the SwitchboardApp.
"""

from __future__ import annotations

import asyncio
import collections.abc
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime

from open_orchestrator.core.status import StatusTracker
from open_orchestrator.core.theme import STATUS_COLORS
from open_orchestrator.core.tmux_manager import detect_activity_from_pane_output
from open_orchestrator.core.worktree import WorktreeManager
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Status light characters and colors (Rich markup)
STATUS_LIGHTS: dict[str, tuple[str, str]] = {
    "working": ("\u25cf", STATUS_COLORS["working"]),
    "idle": ("\u25cb", STATUS_COLORS["idle"]),
    "blocked": ("\u26a0", STATUS_COLORS["blocked"]),
    "waiting": ("\u26a0", STATUS_COLORS["waiting"]),
    "completed": ("\u2713", STATUS_COLORS["completed"]),
    "error": ("\u25cf", STATUS_COLORS["error"]),
    "unknown": ("?", STATUS_COLORS["unknown"]),
}

CARD_WIDTH = 30
CARD_HEIGHT = 4
SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827"
SPINNER_COLORS = ["#666666", "#888888", "#aaaaaa", "#888888"]
TICK_MS = 150
HEAVY_EVERY = 14  # _build_cards runs every HEAVY_EVERY ticks (~2.1s at 150ms)
RECHECKABLE_STATUSES = {AIActivityStatus.WORKING, AIActivityStatus.WAITING, AIActivityStatus.BLOCKED, AIActivityStatus.IDLE}
HOOK_FRESHNESS_SECONDS = 10  # Trust hook-set status if updated within this window
HOOK_TRUST_MAX_SECONDS = 15  # After 15s with no hook update, let scraper recover stale WORKING
SHELL_TIMEOUT = 120  # seconds — max time for owt ship/merge/delete/new


def _hook_capable_tools() -> set[str]:
    """Get tools that support hooks from the registry."""
    from open_orchestrator.core.tool_registry import get_registry

    return {name for name in get_registry().list_names() if get_registry().supports_hooks(name)}


# Backward-compatible constant populated from registry
HOOK_CAPABLE_TOOLS = _hook_capable_tools()

# Pre-compiled regex patterns imported from tmux_manager for pane detection
from open_orchestrator.core.tmux_manager import (  # noqa: E402
    TMUX_ALLOW_PROMPT_RE,
    TMUX_ANSI_RE,
    TMUX_BLOCKED_PROMPT_RE,
    TMUX_INTERRUPTED_RE,
    TMUX_PROMPT_RE,
    TMUX_SEPARATOR_RE,
    TMUX_STATUS_BAR_RE,
    TMUX_TOOL_HEADER_RE,
)

_BLOCKED_RE = TMUX_BLOCKED_PROMPT_RE
_ALLOW_PROMPT_RE = TMUX_ALLOW_PROMPT_RE
_STATUS_BAR_RE = TMUX_STATUS_BAR_RE
_SEPARATOR_RE = TMUX_SEPARATOR_RE
_PROMPT_RE = TMUX_PROMPT_RE
_TOOL_HEADER_RE = TMUX_TOOL_HEADER_RE
_INTERRUPTED_RE = TMUX_INTERRUPTED_RE
_ANSI_RE = TMUX_ANSI_RE


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Card:
    """A switchboard card representing one worktree/agent."""

    name: str
    status: AIActivityStatus
    branch: str
    ai_tool: str
    task: str | None
    elapsed: str
    tmux_session: str | None
    flash_until: int = 0  # tick number until which to show A_REVERSE flash
    overlap_count: int = 0  # number of files overlapping with other worktrees
    overlap_names: list[str] | None = None  # worktree names with overlap
    diff_stat: str = ""  # e.g. "+142 -37"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _tmux_session_exists_raw(session_name: str) -> bool:
    """Check tmux session existence via raw subprocess (safe for asyncio.to_thread)."""
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            timeout=2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _format_elapsed(status: WorktreeAIStatus) -> str:
    """Format time elapsed since last update."""
    if not status.updated_at:
        return ""
    delta = datetime.now() - status.updated_at
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m"
    hours = total_seconds // 3600
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def _detect_pane_status(tmux_session: str | None) -> tuple[AIActivityStatus, bool] | None:
    """Detect agent status by capturing tmux pane content.

    Returns (status, high_confidence) or None if inconclusive.
    high_confidence=True means the signal is unambiguous (e.g. "Interrupted" text)
    and should override hook trust guards.
    """
    if not tmux_session:
        return None
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", tmux_session, "-p", "-J"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return detect_activity_from_pane_output(result.stdout)


# Files that are commonly touched by all worktrees and should not count as
# meaningful overlaps (lock files, package manifests, init modules, etc.).
_OVERLAP_IGNORE_NAMES: frozenset[str] = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "Cargo.lock",
        "Cargo.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "__init__.py",
        "CLAUDE.md",
        ".env",
        ".env.example",
    }
)


def _get_diff_info(worktree_path: str, branch: str) -> tuple[list[str], str]:
    """Get modified files AND diff stat in a single git call.

    Uses ``git merge-base`` to find the common ancestor with the upstream
    branch, then diffs from that ancestor to HEAD so we only see commits
    that belong to this worktree.  Falls back to raw three-dot diff if
    ``merge-base`` is unavailable.

    Returns (modified_files, diff_stat_str).
    """
    if not os.path.isdir(worktree_path):
        return [], ""
    try:
        for base in ("main", "master", "develop"):
            # Find the common ancestor for accurate diffs
            mb_result = subprocess.run(
                ["git", "merge-base", base, "HEAD"],
                capture_output=True,
                text=True,
                cwd=worktree_path,
                timeout=5,
            )
            if mb_result.returncode != 0:
                continue
            merge_base = mb_result.stdout.strip()
            if not merge_base:
                continue

            result = subprocess.run(
                ["git", "diff", "--numstat", f"{merge_base}...HEAD"],
                capture_output=True,
                text=True,
                cwd=worktree_path,
                timeout=5,
            )
            if result.returncode == 0:
                files: list[str] = []
                total_ins = 0
                total_dels = 0
                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    parts = line.split("\t", 2)
                    if len(parts) >= 3:
                        ins, dels, name = parts
                        files.append(name)
                        if ins != "-":
                            total_ins += int(ins)
                        if dels != "-":
                            total_dels += int(dels)
                stat_parts = []
                if total_ins:
                    stat_parts.append(f"+{total_ins}")
                if total_dels:
                    stat_parts.append(f"-{total_dels}")
                return files, " ".join(stat_parts)
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("git diff failed for worktree: %s", e)
    return [], ""


def _filter_overlap_files(files: set[str]) -> set[str]:
    """Remove noise files that should not trigger overlap warnings."""
    return {f for f in files if os.path.basename(f) not in _OVERLAP_IGNORE_NAMES}


def _compute_overlaps(
    cards: list[Card],
    file_map: dict[str, list[str]],
) -> None:
    """Compute pairwise file overlaps and annotate cards.

    Only meaningful source files are considered — lock files, manifests, and
    other commonly-modified noise files are excluded.
    """
    # Pre-compute filtered file sets to avoid O(n^2) _filter_overlap_files calls
    filtered_map = {card.name: _filter_overlap_files(set(file_map.get(card.name, []))) for card in cards}
    for i, card in enumerate(cards):
        my_files = filtered_map[card.name]
        if not my_files:
            continue
        overlap_names = []
        overlap_files: set[str] = set()
        for j, other in enumerate(cards):
            if i == j:
                continue
            other_files = filtered_map[other.name]
            common = my_files & other_files
            if common:
                overlap_names.append(other.name)
                overlap_files |= common
        card.overlap_count = len(overlap_files)
        card.overlap_names = overlap_names if overlap_names else None


# ---------------------------------------------------------------------------
# Card building
# ---------------------------------------------------------------------------


def _gather_statuses(
    tracker: StatusTracker,
    wt_manager: WorktreeManager | None = None,
) -> list[WorktreeAIStatus]:
    """Gather worktree statuses, merging git worktrees with status DB."""
    try:
        if wt_manager is None:
            wt_manager = WorktreeManager()
        worktrees = [wt for wt in wt_manager.list_all() if not wt.is_main]
    except Exception:
        logger.debug("Failed to list worktrees from git", exc_info=True)
        worktrees = []

    status_map = {s.worktree_name: s for s in tracker.get_all_statuses()}

    if not worktrees:
        return list(status_map.values())

    valid_names = {wt.name for wt in worktrees}
    statuses: list[WorktreeAIStatus] = []
    for wt in worktrees:
        s = status_map.get(wt.name)
        if s is None:
            s = WorktreeAIStatus(
                worktree_name=wt.name,
                worktree_path=str(wt.path),
                branch=wt.branch,
                activity_status=AIActivityStatus.IDLE,
            )
        statuses.append(s)

    orphan_names = set(status_map.keys()) - valid_names
    if orphan_names:
        try:
            tracker.cleanup_orphans(list(valid_names))
            logger.debug("Pruned %d orphaned status entries: %s", len(orphan_names), orphan_names)
        except Exception as e:
            logger.debug("Orphan cleanup failed: %s", e)

    return statuses


def _schedule_io_tasks(
    statuses: list[WorktreeAIStatus],
    now: datetime,
) -> tuple[list[collections.abc.Awaitable[object]], list[tuple[str, int]]]:
    """Schedule parallel I/O tasks for session checks, pane detection, and diffs."""
    tasks: list[collections.abc.Awaitable[object]] = []
    task_meta: list[tuple[str, int]] = []

    for i, s in enumerate(statuses):
        if s.tmux_session:
            tasks.append(asyncio.to_thread(_tmux_session_exists_raw, s.tmux_session))
            task_meta.append(("session", i))

    for i, s in enumerate(statuses):
        recently_updated = s.updated_at and (now - s.updated_at).total_seconds() < HOOK_FRESHNESS_SECONDS
        if not recently_updated and s.activity_status in RECHECKABLE_STATUSES:
            tasks.append(asyncio.to_thread(_detect_pane_status, s.tmux_session))
            task_meta.append(("pane", i))

    for i, s in enumerate(statuses):
        if os.path.isdir(s.worktree_path):
            tasks.append(asyncio.to_thread(_get_diff_info, s.worktree_path, s.branch))
            task_meta.append(("diff", i))

    return tasks, task_meta


def _apply_results_and_build_cards(
    statuses: list[WorktreeAIStatus],
    pane_results: dict[int, tuple[AIActivityStatus, bool] | None],
    diff_results: dict[int, tuple[list[str], str]],
    tracker: StatusTracker,
    now: datetime,
) -> tuple[list[Card], dict[str, list[str]]]:
    """Apply pane detection and diff results, then build Card objects."""
    cards = []
    file_map: dict[str, list[str]] = {}

    for i, s in enumerate(statuses):
        detection = pane_results.get(i)
        if detection is not None:
            detected, high_confidence = detection
            if detected != s.activity_status:
                time_since_update = (now - s.updated_at).total_seconds() if s.updated_at else float("inf")
                hook_guarded = (
                    not high_confidence
                    and s.ai_tool in HOOK_CAPABLE_TOOLS
                    and s.activity_status == AIActivityStatus.WORKING
                    and detected == AIActivityStatus.WAITING
                    and time_since_update < HOOK_TRUST_MAX_SECONDS
                )
                if not hook_guarded:
                    s.activity_status = detected
                    s.updated_at = now
                    tracker.set_status(s)

        diff_stat = ""
        if i in diff_results:
            mod_files, diff_stat = diff_results[i]
            if mod_files != s.modified_files:
                s.modified_files = mod_files
                tracker.set_status(s)
            file_map[s.worktree_name] = mod_files
        else:
            file_map[s.worktree_name] = s.modified_files

        cards.append(
            Card(
                name=s.worktree_name,
                status=s.activity_status,
                branch=s.branch,
                ai_tool=s.ai_tool,
                task=s.current_task,
                elapsed=_format_elapsed(s),
                tmux_session=s.tmux_session,
                diff_stat=diff_stat,
            )
        )

    _compute_overlaps(cards, file_map)
    return cards, file_map


async def _build_cards_async(
    tracker: StatusTracker,
    wt_manager: WorktreeManager | None = None,
) -> tuple[list[Card], dict[str, list[str]]]:
    """Build card list from git worktrees enriched with status data.

    Uses git worktrees as the source of truth (same as ``owt list``),
    enriched with status DB data for activity tracking. Runs tmux pane
    captures and git diff calls concurrently via asyncio.to_thread.

    Returns (cards, file_map) where file_map maps worktree names to modified files.
    """
    statuses = _gather_statuses(tracker, wt_manager)
    now = datetime.now()

    # Run parallel I/O
    tasks, task_meta = _schedule_io_tasks(statuses, now)
    results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    # Categorize results
    pane_results: dict[int, tuple[AIActivityStatus, bool] | None] = {}
    diff_results: dict[int, tuple[list[str], str]] = {}
    stale_sessions: set[int] = set()
    for (kind, idx), result in zip(task_meta, results):
        if isinstance(result, BaseException):
            continue
        if kind == "session":
            if not result:
                stale_sessions.add(idx)
        elif kind == "pane":
            pane_results[idx] = result  # type: ignore[assignment]
        else:
            diff_results[idx] = result  # type: ignore[assignment]

    # Clear stale tmux sessions
    for idx in stale_sessions:
        s = statuses[idx]
        s.tmux_session = None
        if s.activity_status in (AIActivityStatus.WORKING, AIActivityStatus.BLOCKED):
            s.activity_status = AIActivityStatus.WAITING
        s.updated_at = now
        tracker.set_status(s)
        pane_results.pop(idx, None)

    return _apply_results_and_build_cards(statuses, pane_results, diff_results, tracker, now)


def _build_cards(tracker: StatusTracker) -> tuple[list[Card], dict[str, list[str]]]:
    """Sync wrapper for _build_cards_async (used by tests)."""
    return asyncio.run(_build_cards_async(tracker))


# ---------------------------------------------------------------------------
# Card rendering
# ---------------------------------------------------------------------------


def _render_card(card: Card, tick: int) -> str:
    """Render a card as Rich markup text.

    Compact 3-line layout:
      [light] name              elapsed
      branch | tool | stats
      task description     [!N]
    """
    w = CARD_WIDTH - 2  # inner width (minus panel border)
    status_key = card.status.value
    light_char, color = STATUS_LIGHTS.get(status_key, ("?", "white"))
    if card.status == AIActivityStatus.WORKING:
        light_char = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
        color = SPINNER_COLORS[tick // 2 % len(SPINNER_COLORS)]

    # Line 1: status light + name + elapsed (right-aligned)
    name_trunc = card.name[: w - 8]
    elapsed_str = card.elapsed or ""
    line1_left = f"[{color}]{light_char}[/{color}] [bold]{name_trunc}[/bold]"
    visible_left = 2 + len(name_trunc)
    pad1 = w - visible_left - len(elapsed_str)
    line1 = line1_left + " " * max(1, pad1) + f"[dim]{elapsed_str}[/dim]"

    # Line 2: branch | tool | diff stats (dim)
    branch_short = card.branch.split("/")[-1] if "/" in card.branch else card.branch
    meta_parts: list[str] = [branch_short[:12]]
    if card.ai_tool:
        meta_parts.append(card.ai_tool[:8])
    if card.diff_stat:
        diff_display = card.diff_stat.replace("+", "\u2191").replace("-", "\u2193")
        meta_parts.append(diff_display)
    meta_str = " | ".join(meta_parts)
    line2 = f"[dim]{meta_str[:w]}[/dim]"

    # Line 3: task + overlap badge
    if card.overlap_count > 0:
        overlap_badge = f" [yellow bold][!{card.overlap_count}][/yellow bold]"
        badge_visible_len = len(f" [!{card.overlap_count}]")
        task_str = card.task or "\u2014"
        task_trunc = task_str[: w - badge_visible_len]
        line3 = f"{task_trunc}{overlap_badge}"
    else:
        task_str = card.task or "\u2014"
        line3 = f"{task_str[:w]}"

    return "\n".join([line1, line2, line3])

"""Hook installer for AI tool status reporting.

Installs lifecycle hooks into AI tools (Claude Code, Droid) so they push
status updates to OWT's status file in real-time, instead of relying on
fragile tmux pane scraping.

Hooks are installed per-worktree (in the worktree's .claude/settings.local.json)
so they only fire for OWT-managed sessions and don't pollute global config.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
from pathlib import Path
from typing import Any

from open_orchestrator.config import AITool

logger = logging.getLogger(__name__)


def install_hooks(worktree_path: Path, worktree_name: str, ai_tool: AITool) -> bool:
    """Install status-reporting hooks for the given AI tool.

    Args:
        worktree_path: Path to the worktree directory.
        worktree_name: Name of the worktree (used in hook commands).
        ai_tool: Which AI tool to configure hooks for.

    Returns:
        True if hooks were installed, False if tool is unsupported.
    """
    if ai_tool == AITool.CLAUDE:
        return _install_claude_hooks(worktree_path, worktree_name)
    if ai_tool == AITool.DROID:
        return _install_droid_hooks(worktree_path, worktree_name)
    # OpenCode: no hook system, falls back to pane scraping
    return False


def _owt_path() -> str:
    """Get the full path to the owt executable."""
    path = shutil.which("owt")
    return path or "owt"


def _install_claude_hooks(worktree_path: Path, worktree_name: str) -> bool:
    """Install Claude Code hooks into .claude/settings.local.json.

    Three hooks cover the full lifecycle:
    - UserPromptSubmit → WORKING (user sent a prompt, agent starts)
    - Stop → WAITING (agent finished, waiting for input)
    - Notification(permission_prompt) → BLOCKED (needs user approval)
    """
    settings_dir = worktree_path / ".claude"
    settings_path = settings_dir / "settings.local.json"
    settings_dir.mkdir(parents=True, exist_ok=True)

    # Load existing local settings (merge, don't overwrite)
    existing: dict[str, Any] = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    owt = _owt_path()
    name_q = shlex.quote(worktree_name)
    hooks = existing.setdefault("hooks", {})

    # UserPromptSubmit → WORKING
    hooks["UserPromptSubmit"] = [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": f"{owt} hook --event working --worktree {name_q}",
                }
            ],
        }
    ]

    # Stop → WAITING
    hooks["Stop"] = [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": f"{owt} hook --event waiting --worktree {name_q}",
                }
            ],
        }
    ]

    # Notification(permission_prompt) → BLOCKED
    notification_hooks = hooks.get("Notification", [])
    # Remove any existing OWT notification hooks, keep user-defined ones
    notification_hooks = [h for h in notification_hooks if not (isinstance(h, dict) and "owt hook" in str(h.get("hooks", [])))]
    notification_hooks.append(
        {
            "matcher": "permission_prompt",
            "hooks": [
                {
                    "type": "command",
                    "command": f"{owt} hook --event blocked --worktree {name_q}",
                }
            ],
        }
    )
    hooks["Notification"] = notification_hooks

    existing["hooks"] = hooks
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    logger.info(f"Installed Claude Code hooks in {settings_path}")
    return True


def _install_droid_hooks(worktree_path: Path, worktree_name: str) -> bool:
    """Install Droid hooks into .factory/settings.json inside the worktree.

    Droid's hook system mirrors Claude Code's:
    - UserPromptSubmit → WORKING
    - Stop → WAITING
    - Notification → BLOCKED
    """
    settings_dir = worktree_path / ".factory"
    settings_path = settings_dir / "settings.json"
    settings_dir.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    owt = _owt_path()
    name_q = shlex.quote(worktree_name)
    hooks = existing.setdefault("hooks", {})

    hooks["UserPromptSubmit"] = [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": f"{owt} hook --event working --worktree {name_q}",
                }
            ],
        }
    ]

    hooks["Stop"] = [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": f"{owt} hook --event waiting --worktree {name_q}",
                }
            ],
        }
    ]

    hooks["Notification"] = [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": f"{owt} hook --event blocked --worktree {name_q}",
                }
            ],
        }
    ]

    existing["hooks"] = hooks
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    logger.info(f"Installed Droid hooks in {settings_path}")
    return True

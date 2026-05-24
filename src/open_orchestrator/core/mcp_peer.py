"""MCP peer communication server for OWT agents.

Each agent's Claude Code session spawns this as an MCP server via stdio.
Provides tools for discovering peers, sending/receiving messages, and
coordinating work across worktrees.

Usage (via Claude Code settings.local.json)::

    {"mcpServers": {"owt-peers": {
        "command": "python3",
        "args": ["-m", "open_orchestrator.core.mcp_peer"],
        "env": {"OWT_WORKTREE_NAME": "...", "OWT_DB_PATH": "..."}
    }}}

Requires: pip install open-orchestrator[mcp]
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

import click

from open_orchestrator.core._db import open_db
from open_orchestrator.core.status import PEER_MESSAGES_SCHEMA, default_status_path

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Hosts that are always loopback-only. No override flag exists by design:
# the MCP peer brokers privileged in-repo coordination between sibling agents
# and must never be reachable from off-host. See docs/security.md.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def _validate_loopback_bind(host: str) -> None:
    """Refuse to start the MCP peer if ``host`` is not loopback.

    Raises :class:`click.ClickException` so the failure surfaces as a clean
    startup error regardless of whether the server is launched via the
    Click-based CLI or directly via ``python -m``. There is intentionally
    NO override flag: a non-loopback MCP peer bind is treated as a
    configuration bug, not an opt-in.
    """
    normalized = (host or "").strip().lower()
    if normalized not in _LOOPBACK_HOSTS:
        raise click.ClickException(f"MCP peer must bind loopback-only (127.0.0.1 or ::1). Refusing to start (got host={host!r}).")


def _get_connection(db_path: str) -> sqlite3.Connection:
    """Open a WAL-mode connection matching StatusTracker settings."""
    conn = open_db(db_path)
    conn.executescript(PEER_MESSAGES_SCHEMA)
    conn.commit()
    return conn


def create_server() -> FastMCP:
    """Create and return the FastMCP server instance.

    The server reads its identity from environment variables:
    - ``OWT_WORKTREE_NAME``: this agent's worktree name
    - ``OWT_DB_PATH``: path to the shared status.db
    """
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("owt-peers")

    # Defensive: FastMCP defaults to 127.0.0.1, but settings.host may have been
    # overridden by env (FASTMCP_HOST), kwarg, or future config wiring. Validate
    # at construction so a non-loopback bind fails fast — before any network
    # transport is opened. No override flag by design.
    _validate_loopback_bind(server.settings.host)

    worktree_name = os.environ.get("OWT_WORKTREE_NAME", "unknown")
    db_path = os.environ.get(
        "OWT_DB_PATH",
        str(default_status_path()),
    )

    conn = _get_connection(db_path)

    @server.tool(description="List all peer worktrees and their current status.")
    def list_peers() -> list[dict[str, str | None]]:
        """Discover active agents. Returns name, branch, status, task, and summary for each peer."""
        rows = conn.execute(
            "SELECT worktree_name, branch, activity_status, current_task, notes FROM worktree_status WHERE worktree_name != ?",
            (worktree_name,),
        ).fetchall()
        return [
            {
                "name": r["worktree_name"],
                "branch": r["branch"],
                "status": r["activity_status"],
                "task": r["current_task"],
                "summary": r["notes"],
            }
            for r in rows
        ]

    @server.tool(description="Send a message to a peer agent. Use to_peer='*' to broadcast.")
    def send_message(to_peer: str, message: str) -> dict[str, bool | int]:
        """Send a message to another agent's inbox."""
        now = datetime.now().isoformat()
        if to_peer == "*":
            peers = conn.execute(
                "SELECT worktree_name FROM worktree_status WHERE worktree_name != ?",
                (worktree_name,),
            ).fetchall()
            for p in peers:
                conn.execute(
                    "INSERT INTO peer_messages (from_peer, to_peer, message, created_at) VALUES (?, ?, ?, ?)",
                    (worktree_name, p["worktree_name"], message, now),
                )
            conn.commit()
            return {"sent": True, "count": len(peers)}
        cursor = conn.execute(
            "INSERT INTO peer_messages (from_peer, to_peer, message, created_at) VALUES (?, ?, ?, ?)",
            (worktree_name, to_peer, message, now),
        )
        conn.commit()
        return {"sent": True, "id": cursor.lastrowid or 0}

    @server.tool(description="Check for unread messages from other agents.")
    def check_messages(mark_read: bool = True) -> list[dict[str, str | int]]:
        """Read this agent's inbox. Messages are marked read by default."""
        rows = conn.execute(
            "SELECT id, from_peer, message, created_at FROM peer_messages WHERE to_peer = ? AND read = 0 ORDER BY created_at",
            (worktree_name,),
        ).fetchall()
        messages = [{"id": r["id"], "from": r["from_peer"], "message": r["message"], "created_at": r["created_at"]} for r in rows]
        if mark_read and messages:
            ids = [m["id"] for m in messages]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE peer_messages SET read = 1 WHERE id IN ({placeholders})",  # noqa: S608
                ids,
            )
            conn.commit()
        return messages

    @server.tool(description="Set a brief summary of what this agent is working on, visible to peers.")
    def set_summary(summary: str) -> dict[str, bool]:
        """Update this agent's visible status for peer coordination."""
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE worktree_status SET notes = ?, updated_at = ? WHERE worktree_name = ?",
            (summary, now, worktree_name),
        )
        conn.commit()
        return {"updated": True}

    @server.tool(description="Get the list of files modified by a peer, to avoid conflicts.")
    def get_peer_files(peer_name: str) -> list[str]:
        """Check what files a peer agent is editing."""
        row = conn.execute(
            "SELECT modified_files FROM worktree_status WHERE worktree_name = ?",
            (peer_name,),
        ).fetchone()
        if not row:
            return []
        files: list[str] = json.loads(row["modified_files"] or "[]")
        return files

    return server


if __name__ == "__main__":
    server = create_server()
    # Re-validate immediately before opening any transport. The stdio default
    # never opens a port, but the second check guards against future edits
    # that flip the transport to sse/streamable-http without also auditing
    # the bind host.
    _validate_loopback_bind(server.settings.host)
    server.run(transport="stdio")

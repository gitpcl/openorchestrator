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
from pathlib import Path

logger = logging.getLogger(__name__)

_PEER_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS peer_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_peer TEXT NOT NULL,
    to_peer TEXT NOT NULL,
    message TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_peer_messages_to_peer_read
    ON peer_messages(to_peer, read);
"""


def _get_connection(db_path: str) -> sqlite3.Connection:
    """Open a WAL-mode connection matching StatusTracker settings."""
    conn = sqlite3.connect(db_path, isolation_level="DEFERRED")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_PEER_SCHEMA_SQL)
    conn.commit()
    return conn


def create_server():  # type: ignore[no-untyped-def]
    """Create and return the FastMCP server instance.

    The server reads its identity from environment variables:
    - ``OWT_WORKTREE_NAME``: this agent's worktree name
    - ``OWT_DB_PATH``: path to the shared status.db
    """
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]

    server = FastMCP("owt-peers")

    worktree_name = os.environ.get("OWT_WORKTREE_NAME", "unknown")
    db_path = os.environ.get(
        "OWT_DB_PATH",
        str(Path.home() / ".open-orchestrator" / "status.db"),
    )

    conn = _get_connection(db_path)

    @server.tool(description="List all peer worktrees and their current status.")
    def list_peers() -> list[dict[str, str | None]]:
        """Discover active agents. Returns name, branch, status, task, and summary for each peer."""
        rows = conn.execute(
            "SELECT worktree_name, branch, activity_status, current_task, notes "
            "FROM worktree_status WHERE worktree_name != ?",
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
                    "INSERT INTO peer_messages (from_peer, to_peer, message, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (worktree_name, p["worktree_name"], message, now),
                )
            conn.commit()
            return {"sent": True, "count": len(peers)}
        cursor = conn.execute(
            "INSERT INTO peer_messages (from_peer, to_peer, message, created_at) "
            "VALUES (?, ?, ?, ?)",
            (worktree_name, to_peer, message, now),
        )
        conn.commit()
        return {"sent": True, "id": cursor.lastrowid or 0}

    @server.tool(description="Check for unread messages from other agents.")
    def check_messages(mark_read: bool = True) -> list[dict[str, str | int]]:
        """Read this agent's inbox. Messages are marked read by default."""
        rows = conn.execute(
            "SELECT id, from_peer, message, created_at FROM peer_messages "
            "WHERE to_peer = ? AND read = 0 ORDER BY created_at",
            (worktree_name,),
        ).fetchall()
        messages = [
            {"id": r["id"], "from": r["from_peer"],
             "message": r["message"], "created_at": r["created_at"]}
            for r in rows
        ]
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
            "UPDATE worktree_status SET notes = ?, updated_at = ? "
            "WHERE worktree_name = ?",
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
        return json.loads(row["modified_files"] or "[]")

    return server


if __name__ == "__main__":
    server = create_server()
    server.run(transport="stdio")

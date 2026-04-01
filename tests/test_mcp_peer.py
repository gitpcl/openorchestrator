"""Tests for the MCP peer communication server."""

import sqlite3
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp", reason="MCP SDK not installed")


@pytest.fixture
def peer_db(tmp_path: Path) -> Path:
    """Create a temp SQLite DB with the status + peer schemas."""
    db_path = tmp_path / "status.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""\
        CREATE TABLE IF NOT EXISTS worktree_status (
            worktree_name TEXT PRIMARY KEY,
            worktree_path TEXT NOT NULL,
            branch TEXT NOT NULL,
            tmux_session TEXT,
            ai_tool TEXT DEFAULT 'claude',
            activity_status TEXT DEFAULT 'idle',
            current_task TEXT,
            last_task_update TEXT,
            notes TEXT,
            modified_files TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
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
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_worktree(db_path: Path, name: str, branch: str, **kwargs: str) -> None:
    """Insert a worktree row for testing."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO worktree_status "
        "(worktree_name, worktree_path, branch, activity_status, current_task, "
        "notes, modified_files, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (
            name,
            kwargs.get("path", f"/tmp/{name}"),
            branch,
            kwargs.get("status", "working"),
            kwargs.get("task", "building stuff"),
            kwargs.get("notes", None),
            kwargs.get("modified_files", "[]"),
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def server(peer_db: Path, monkeypatch: pytest.MonkeyPatch):
    """Create the MCP server with test identity."""
    monkeypatch.setenv("OWT_WORKTREE_NAME", "agent-self")
    monkeypatch.setenv("OWT_DB_PATH", str(peer_db))

    _insert_worktree(peer_db, "agent-self", "feat/self")
    _insert_worktree(peer_db, "agent-alpha", "feat/alpha", notes="building auth")
    _insert_worktree(peer_db, "agent-beta", "feat/beta", task="REST API",
                     modified_files='["api.py", "models.py"]')

    from open_orchestrator.core.mcp_peer import create_server
    return create_server()


def _call_tool(server, name: str, **kwargs):
    """Call a tool function by name from the server."""
    # FastMCP registers tools as callables; access via the _tool_manager
    tool_fn = None
    for tool in server._tool_manager._tools.values():
        if tool.name == name:
            tool_fn = tool.fn
            break
    assert tool_fn is not None, f"Tool '{name}' not found"
    return tool_fn(**kwargs)


class TestPeerSchema:
    def test_peer_messages_table_created(self, peer_db: Path) -> None:
        conn = sqlite3.connect(str(peer_db))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "peer_messages" in table_names

    def test_index_created(self, peer_db: Path) -> None:
        conn = sqlite3.connect(str(peer_db))
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = [i[0] for i in indexes]
        assert "idx_peer_messages_to_peer_read" in index_names

    def test_schema_idempotent(self, peer_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OWT_WORKTREE_NAME", "test")
        monkeypatch.setenv("OWT_DB_PATH", str(peer_db))
        from open_orchestrator.core.mcp_peer import _get_connection
        # Calling twice should not raise
        _get_connection(str(peer_db))
        _get_connection(str(peer_db))


class TestListPeers:
    def test_returns_other_worktrees(self, server) -> None:
        result = _call_tool(server, "list_peers")
        names = [p["name"] for p in result]
        assert "agent-alpha" in names
        assert "agent-beta" in names

    def test_excludes_self(self, server) -> None:
        result = _call_tool(server, "list_peers")
        names = [p["name"] for p in result]
        assert "agent-self" not in names

    def test_includes_status_fields(self, server) -> None:
        result = _call_tool(server, "list_peers")
        alpha = next(p for p in result if p["name"] == "agent-alpha")
        assert alpha["branch"] == "feat/alpha"
        assert alpha["summary"] == "building auth"

    def test_empty_when_no_peers(self, peer_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OWT_WORKTREE_NAME", "solo")
        monkeypatch.setenv("OWT_DB_PATH", str(peer_db))
        # Clear all worktrees
        conn = sqlite3.connect(str(peer_db))
        conn.execute("DELETE FROM worktree_status")
        conn.commit()
        conn.close()
        from open_orchestrator.core.mcp_peer import create_server
        s = create_server()
        assert _call_tool(s, "list_peers") == []


class TestSendMessage:
    def test_send_to_specific_peer(self, server, peer_db: Path) -> None:
        result = _call_tool(server, "send_message", to_peer="agent-alpha", message="hello")
        assert result["sent"] is True
        assert result["id"] > 0

        conn = sqlite3.connect(str(peer_db))
        row = conn.execute("SELECT * FROM peer_messages WHERE id = ?", (result["id"],)).fetchone()
        assert row is not None

    def test_broadcast(self, server) -> None:
        result = _call_tool(server, "send_message", to_peer="*", message="heads up")
        assert result["sent"] is True
        assert result["count"] == 2  # alpha + beta, not self

    def test_broadcast_excludes_self(self, server, peer_db: Path) -> None:
        _call_tool(server, "send_message", to_peer="*", message="broadcast")
        conn = sqlite3.connect(str(peer_db))
        rows = conn.execute(
            "SELECT to_peer FROM peer_messages WHERE message = 'broadcast'"
        ).fetchall()
        recipients = [r[0] for r in rows]
        assert "agent-self" not in recipients


class TestCheckMessages:
    def test_returns_unread_messages(self, server, peer_db: Path) -> None:
        conn = sqlite3.connect(str(peer_db))
        conn.execute(
            "INSERT INTO peer_messages (from_peer, to_peer, message, created_at) "
            "VALUES ('agent-alpha', 'agent-self', 'ping', datetime('now'))"
        )
        conn.commit()
        conn.close()

        msgs = _call_tool(server, "check_messages")
        assert len(msgs) == 1
        assert msgs[0]["from"] == "agent-alpha"
        assert msgs[0]["message"] == "ping"

    def test_marks_messages_read(self, server, peer_db: Path) -> None:
        conn = sqlite3.connect(str(peer_db))
        conn.execute(
            "INSERT INTO peer_messages (from_peer, to_peer, message, created_at) "
            "VALUES ('agent-beta', 'agent-self', 'hey', datetime('now'))"
        )
        conn.commit()
        conn.close()

        _call_tool(server, "check_messages", mark_read=True)
        # Second call should return empty
        assert _call_tool(server, "check_messages") == []

    def test_mark_read_false_preserves(self, server, peer_db: Path) -> None:
        conn = sqlite3.connect(str(peer_db))
        conn.execute(
            "INSERT INTO peer_messages (from_peer, to_peer, message, created_at) "
            "VALUES ('agent-alpha', 'agent-self', 'stay', datetime('now'))"
        )
        conn.commit()
        conn.close()

        msgs1 = _call_tool(server, "check_messages", mark_read=False)
        msgs2 = _call_tool(server, "check_messages", mark_read=False)
        assert len(msgs1) == len(msgs2) == 1

    def test_empty_inbox(self, server) -> None:
        assert _call_tool(server, "check_messages") == []


class TestSetSummary:
    def test_updates_notes(self, server, peer_db: Path) -> None:
        result = _call_tool(server, "set_summary", summary="refactoring auth module")
        assert result["updated"] is True

        conn = sqlite3.connect(str(peer_db))
        row = conn.execute(
            "SELECT notes FROM worktree_status WHERE worktree_name = 'agent-self'"
        ).fetchone()
        assert row[0] == "refactoring auth module"


class TestGetPeerFiles:
    def test_returns_modified_files(self, server) -> None:
        files = _call_tool(server, "get_peer_files", peer_name="agent-beta")
        assert files == ["api.py", "models.py"]

    def test_unknown_peer_returns_empty(self, server) -> None:
        assert _call_tool(server, "get_peer_files", peer_name="nonexistent") == []

    def test_peer_with_no_files(self, server) -> None:
        files = _call_tool(server, "get_peer_files", peer_name="agent-alpha")
        assert files == []

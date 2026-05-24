"""Shared SQLite connection helper.

Centralizes the pragma set used by every long-lived SQLite connection in
``open_orchestrator``. Keeping the WAL + busy_timeout + synchronous tuning
in one place prevents drift between :mod:`status`, :mod:`memory_store`,
and :mod:`mcp_peer`, all of which write to disk under switchboard + hooks +
dream-daemon contention.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["open_db"]

_BUSY_TIMEOUT_MS = 5000


def open_db(path: Path | str, *, check_same_thread: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection with the project's standard pragmas.

    All callers writing to a long-lived ``open_orchestrator`` database must
    route through this helper. The pragmas applied — ``journal_mode=WAL``,
    ``busy_timeout=5000``, and ``synchronous=NORMAL`` — eliminate the
    ``database is locked`` window that appeared under concurrent writes
    from switchboard, hooks, and the dream daemon.

    Args:
        path: Filesystem path to the database file. Parent must exist.
        check_same_thread: Forwarded to :func:`sqlite3.connect`. Defaults to
            ``False`` because background daemons (dream, switchboard refresh)
            may invoke the same connection from a worker thread; SQLite's
            internal locking plus ``busy_timeout`` keep writes safe.

    Returns:
        Configured :class:`sqlite3.Connection` with ``row_factory`` set to
        :class:`sqlite3.Row`.
    """

    conn = sqlite3.connect(
        str(path),
        isolation_level="DEFERRED",
        check_same_thread=check_same_thread,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

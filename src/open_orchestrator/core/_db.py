"""Shared SQLite connection helper.

Centralizes the pragma set used by every long-lived SQLite connection in
``open_orchestrator``. Keeping the WAL + busy_timeout + synchronous tuning
in one place prevents drift between :mod:`status`, :mod:`memory_store`,
and :mod:`mcp_peer`, all of which write to disk under switchboard + hooks +
dream-daemon contention.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
from pathlib import Path

__all__ = ["open_db", "secure_db_perms"]

_BUSY_TIMEOUT_MS = 5000
_DB_FILE_MODE = 0o600


def secure_db_perms(path: Path | str) -> None:
    """Apply ``0o600`` (owner read/write only) to a SQLite DB and siblings.

    SQLite in WAL mode creates ``<db>-wal`` and ``<db>-shm`` companion files
    alongside the main database. All three may contain in-flight rows that
    haven't yet been checkpointed back, so they need the same restrictive
    permissions as the primary file.

    Idempotent — re-applying ``0o600`` to a file already at ``0o600`` is a
    no-op. Missing sibling files are skipped silently because WAL/SHM only
    appear after the first write.

    No-op on Windows where POSIX permission bits are not meaningful and
    :func:`os.chmod` does not enforce owner-only semantics.
    """

    if sys.platform == "win32":
        return

    db_path = Path(path)
    for candidate in (db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
        if candidate.exists():
            # Best-effort: refusing perms (e.g. read-only mount, NFS) must
            # not crash long-lived daemons like the switchboard.
            with contextlib.suppress(OSError):
                os.chmod(candidate, _DB_FILE_MODE)


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
    secure_db_perms(path)
    return conn

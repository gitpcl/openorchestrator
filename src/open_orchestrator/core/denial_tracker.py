"""Denial tracking for agent safety degradation.

Tracks consecutive and total denials per session. When thresholds
are exceeded, the agent degrades to user-confirmation mode to
prevent runaway autonomous actions.

Thresholds:
- 3 consecutive denials → confirmation mode
- 20 total denials → confirmation mode

State persists in SQLite, resets on session start.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CONSECUTIVE_THRESHOLD = 3
TOTAL_THRESHOLD = 20

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS denials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    consecutive_denials INTEGER DEFAULT 0,
    total_denials INTEGER DEFAULT 0,
    confirmation_mode INTEGER DEFAULT 0
);
"""


@dataclass(frozen=True)
class DenialState:
    """Current denial tracking state for a session."""

    session_id: str
    consecutive_denials: int
    total_denials: int
    confirmation_mode: bool

    @property
    def should_confirm(self) -> bool:
        """Whether the agent should ask for user confirmation."""
        return self.confirmation_mode


class DenialTracker:
    """SQLite-backed denial counter with threshold-based degradation."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or Path.home() / ".open-orchestrator" / "denials.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._connect()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        self._conn.executescript(_SCHEMA)

    # ── Session Management ──────────────────────────────────────────

    def start_session(self, session_id: str) -> DenialState:
        """Start or reset a session. Clears consecutive count, keeps total for reference."""
        now = datetime.now().isoformat()
        self._conn.execute(
            """INSERT INTO sessions (session_id, started_at, consecutive_denials, total_denials, confirmation_mode)
               VALUES (?, ?, 0, 0, 0)
               ON CONFLICT(session_id) DO UPDATE SET
                   started_at = excluded.started_at,
                   consecutive_denials = 0,
                   total_denials = 0,
                   confirmation_mode = 0""",
            (session_id, now),
        )
        self._conn.commit()
        return self.get_state(session_id)

    def get_state(self, session_id: str) -> DenialState:
        """Get current denial state for a session."""
        row = self._conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            return DenialState(
                session_id=session_id,
                consecutive_denials=0,
                total_denials=0,
                confirmation_mode=False,
            )
        return DenialState(
            session_id=row["session_id"],
            consecutive_denials=row["consecutive_denials"],
            total_denials=row["total_denials"],
            confirmation_mode=bool(row["confirmation_mode"]),
        )

    # ── Recording ───────────────────────────────────────────────────

    def record_denial(self, session_id: str, action: str, reason: str = "") -> DenialState:
        """Record a denial and update thresholds.

        Returns the updated state (may have triggered confirmation mode).
        """
        now = datetime.now().isoformat()

        # Log the denial
        self._conn.execute(
            "INSERT INTO denials (session_id, action, reason, created_at) VALUES (?, ?, ?, ?)",
            (session_id, action, reason, now),
        )

        # Ensure session exists
        self._conn.execute(
            """INSERT INTO sessions (session_id, started_at, consecutive_denials, total_denials, confirmation_mode)
               VALUES (?, ?, 0, 0, 0)
               ON CONFLICT(session_id) DO NOTHING""",
            (session_id, now),
        )

        # Increment counters
        self._conn.execute(
            """UPDATE sessions SET
                consecutive_denials = consecutive_denials + 1,
                total_denials = total_denials + 1
               WHERE session_id = ?""",
            (session_id,),
        )

        # Check thresholds
        row = self._conn.execute(
            "SELECT consecutive_denials, total_denials FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if row:
            consec = row["consecutive_denials"]
            total = row["total_denials"]
            if consec >= CONSECUTIVE_THRESHOLD or total >= TOTAL_THRESHOLD:
                self._conn.execute(
                    "UPDATE sessions SET confirmation_mode = 1 WHERE session_id = ?",
                    (session_id,),
                )
                logger.warning(
                    "Session '%s' entered confirmation mode (consecutive=%d, total=%d)",
                    session_id,
                    consec,
                    total,
                )

        self._conn.commit()
        return self.get_state(session_id)

    def record_approval(self, session_id: str) -> DenialState:
        """Record an approval, resetting the consecutive denial counter."""
        self._conn.execute(
            """UPDATE sessions SET consecutive_denials = 0 WHERE session_id = ?""",
            (session_id,),
        )
        self._conn.commit()
        return self.get_state(session_id)

    def reset_session(self, session_id: str) -> DenialState:
        """Fully reset a session's denial state."""
        return self.start_session(session_id)

    # ── Query ───────────────────────────────────────────────────────

    def get_denial_history(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        """Get recent denials for a session."""
        rows = self._conn.execute(
            "SELECT action, reason, created_at FROM denials WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [{"action": r["action"], "reason": r["reason"], "created_at": r["created_at"]} for r in rows]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

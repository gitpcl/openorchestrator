"""SQLite-backed recall memory store with FTS5 full-text search.

Stores structured facts in a 4-layer token-budgeted stack (L0-L3) and a
temporal knowledge graph for tracking fact relationships over time.

Pure SQLite — FTS5 ships with Python's stdlib sqlite3 module, no new deps.
Follows the WAL pattern from status.py for concurrent access from
switchboard, dream daemon, and CLI.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from open_orchestrator.models.memory import (
    LAYER_BUDGETS,
    ContradictionGroup,
    Fact,
    FactSearchHit,
    MemoryLayer,
    MemoryType,
    Triple,
)

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_FILENAME = "recall.db"
MEMORY_DB_ENV_VAR = "OWT_RECALL_DB_PATH"

# 4 chars per token heuristic — matches Sprint 016 compaction
_CHARS_PER_TOKEN = 4


_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worktree TEXT NOT NULL,
    category TEXT NOT NULL,
    kind TEXT NOT NULL,
    layer TEXT NOT NULL,
    content TEXT NOT NULL,
    aaak TEXT,
    token_estimate INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_facts_layer ON facts(layer);
CREATE INDEX IF NOT EXISTS idx_facts_worktree ON facts(worktree);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    content,
    aaak,
    content=facts,
    content_rowid=id,
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, aaak) VALUES (new.id, new.content, new.aaak);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, aaak)
    VALUES('delete', old.id, old.content, old.aaak);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, aaak)
    VALUES('delete', old.id, old.content, old.aaak);
    INSERT INTO facts_fts(rowid, content, aaak)
    VALUES(new.id, new.content, new.aaak);
END;

CREATE TABLE IF NOT EXISTS kg_triples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    source_fact_id INTEGER REFERENCES facts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_kg_subject_predicate ON kg_triples(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_kg_valid_to ON kg_triples(valid_to);
"""


def estimate_tokens(text: str) -> int:
    """Heuristic token count using 4 chars per token (matches Sprint 016)."""
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def default_memory_path() -> Path:
    """Resolve the default recall DB path.

    ``OWT_RECALL_DB_PATH`` takes precedence to support tests and explicit overrides.
    """
    env_path = os.environ.get(MEMORY_DB_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".open-orchestrator" / DEFAULT_MEMORY_FILENAME


@dataclass
class MemoryStoreConfig:
    """Configuration for the recall memory store."""

    storage_path: Path | None = None


def _dt_to_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _str_to_dt(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        worktree=row["worktree"],
        category=row["category"],
        kind=MemoryType(row["kind"]),
        layer=MemoryLayer(row["layer"]),
        content=row["content"],
        aaak=row["aaak"],
        token_estimate=row["token_estimate"] or 0,
        source=row["source"],
        created_at=_str_to_dt(row["created_at"]) or datetime.now(),
        updated_at=_str_to_dt(row["updated_at"]) or datetime.now(),
    )


def _row_to_triple(row: sqlite3.Row) -> Triple:
    return Triple(
        id=row["id"],
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        valid_from=_str_to_dt(row["valid_from"]) or datetime.now(),
        valid_to=_str_to_dt(row["valid_to"]),
        source_fact_id=row["source_fact_id"],
    )


class MemoryStore:
    """SQLite + FTS5 backed store for the recall memory system."""

    def __init__(self, config: MemoryStoreConfig | None = None) -> None:
        self.config = config or MemoryStoreConfig()
        self.storage_path = self.config.storage_path or default_memory_path()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.storage_path), isolation_level="DEFERRED")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()
        try:
            os.chmod(self.storage_path, 0o600)
        except (PermissionError, OSError):
            pass

    def _ensure_schema(self) -> None:
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            logger.debug("Error closing recall DB", exc_info=True)

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Facts CRUD
    # ------------------------------------------------------------------

    def add_fact(
        self,
        content: str,
        kind: MemoryType,
        category: str,
        worktree: str = "global",
        layer: MemoryLayer = MemoryLayer.L2_TOPIC,
        aaak: str | None = None,
        source: str | None = None,
    ) -> Fact:
        """Insert a new fact and return it with the assigned ID.

        Token budgets for L0/L1 are enforced softly: facts that overflow
        their layer's budget are demoted to L2 with a warning logged.
        """
        token_estimate = estimate_tokens(content)
        target_layer = self._enforce_budget(layer, token_estimate, worktree)
        now = datetime.now()
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO facts
                    (worktree, category, kind, layer, content, aaak,
                     token_estimate, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worktree,
                    category,
                    kind.value,
                    target_layer.value,
                    content,
                    aaak,
                    token_estimate,
                    source,
                    _dt_to_str(now),
                    _dt_to_str(now),
                ),
            )
            fact_id = cursor.lastrowid
        return Fact(
            id=fact_id,
            worktree=worktree,
            category=category,
            kind=kind,
            layer=target_layer,
            content=content,
            aaak=aaak,
            token_estimate=token_estimate,
            source=source,
            created_at=now,
            updated_at=now,
        )

    def get_fact(self, fact_id: int) -> Fact | None:
        row = self.conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return _row_to_fact(row) if row else None

    def list_facts(
        self,
        worktree: str | None = None,
        layer: MemoryLayer | None = None,
        category: str | None = None,
    ) -> list[Fact]:
        sql = "SELECT * FROM facts WHERE 1=1"
        params: list[object] = []
        if worktree is not None:
            sql += " AND worktree = ?"
            params.append(worktree)
        if layer is not None:
            sql += " AND layer = ?"
            params.append(layer.value)
        if category is not None:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_fact(row) for row in rows]

    def update_fact(
        self,
        fact_id: int,
        content: str | None = None,
        aaak: str | None = None,
        layer: MemoryLayer | None = None,
    ) -> Fact | None:
        existing = self.get_fact(fact_id)
        if existing is None:
            return None
        new_content = content if content is not None else existing.content
        new_aaak = aaak if aaak is not None else existing.aaak
        new_layer = layer if layer is not None else existing.layer
        new_tokens = estimate_tokens(new_content)
        now = datetime.now()
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE facts
                SET content = ?, aaak = ?, layer = ?, token_estimate = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_content,
                    new_aaak,
                    new_layer.value,
                    new_tokens,
                    _dt_to_str(now),
                    fact_id,
                ),
            )
        return self.get_fact(fact_id)

    def delete_fact(self, fact_id: int) -> bool:
        with self._transaction() as conn:
            cursor = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # FTS5 Search
    # ------------------------------------------------------------------

    def search_facts(
        self,
        query: str,
        limit: int = 20,
        worktree: str | None = None,
    ) -> list[FactSearchHit]:
        """BM25-ranked full-text search across content and aaak columns."""
        if not query.strip():
            return []
        sql = """
            SELECT facts.*, bm25(facts_fts) AS rank,
                   snippet(facts_fts, 0, '[', ']', '...', 16) AS snippet
            FROM facts_fts
            JOIN facts ON facts.id = facts_fts.rowid
            WHERE facts_fts MATCH ?
        """
        params: list[object] = [self._sanitize_fts_query(query)]
        if worktree is not None:
            sql += " AND facts.worktree = ?"
            params.append(worktree)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 query failed: %s", exc)
            return []
        return [
            FactSearchHit(
                fact=_row_to_fact(row),
                rank=float(row["rank"]),
                snippet=row["snippet"] or "",
            )
            for row in rows
        ]

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Quote each term so FTS5 treats them as literal strings, not operators."""
        terms = [t for t in query.replace('"', "").split() if t]
        if not terms:
            return query
        return " ".join(f'"{t}"' for t in terms)

    # ------------------------------------------------------------------
    # Layer budget enforcement
    # ------------------------------------------------------------------

    def _enforce_budget(self, layer: MemoryLayer, new_tokens: int, worktree: str) -> MemoryLayer:
        """Enforce token budgets for L0/L1; demote to L2 if overflowing."""
        budget = LAYER_BUDGETS.get(layer, 0)
        if budget <= 0:
            return layer
        used = self._tokens_used(layer, worktree)
        if used + new_tokens > budget:
            logger.warning(
                "Layer %s budget exceeded (%d + %d > %d) — demoting to L2",
                layer.value,
                used,
                new_tokens,
                budget,
            )
            return MemoryLayer.L2_TOPIC
        return layer

    def _tokens_used(self, layer: MemoryLayer, worktree: str) -> int:
        row = self.conn.execute(
            """
            SELECT COALESCE(SUM(token_estimate), 0) AS total
            FROM facts
            WHERE layer = ? AND (worktree = ? OR worktree = 'global')
            """,
            (layer.value, worktree),
        ).fetchone()
        return int(row["total"] or 0)

    def get_l0_l1_payload(self, worktree: str = "global") -> str:
        """Return combined L0+L1 facts under 250 tokens, suitable for CLAUDE.md injection."""
        l0_facts = self.list_facts(worktree=worktree, layer=MemoryLayer.L0_IDENTITY)
        l1_facts = self.list_facts(worktree=worktree, layer=MemoryLayer.L1_CRITICAL)
        if worktree != "global":
            l0_facts.extend(self.list_facts(worktree="global", layer=MemoryLayer.L0_IDENTITY))
            l1_facts.extend(self.list_facts(worktree="global", layer=MemoryLayer.L1_CRITICAL))
        lines: list[str] = []
        if l0_facts:
            lines.append("## Identity")
            for fact in l0_facts:
                lines.append(f"- {fact.content}")
        if l1_facts:
            lines.append("")
            lines.append("## Critical")
            for fact in l1_facts:
                lines.append(f"- {fact.aaak or fact.content}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Knowledge Graph: triples CRUD
    # ------------------------------------------------------------------

    def kg_add(
        self,
        subject: str,
        predicate: str,
        object_: str,
        source_fact_id: int | None = None,
        valid_from: datetime | None = None,
    ) -> Triple:
        """Append a new triple. Always sets valid_from (defaults to now)."""
        from_ts = valid_from or datetime.now()
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO kg_triples
                    (subject, predicate, object, valid_from, valid_to, source_fact_id)
                VALUES (?, ?, ?, ?, NULL, ?)
                """,
                (subject, predicate, object_, _dt_to_str(from_ts), source_fact_id),
            )
            triple_id = cursor.lastrowid
        return Triple(
            id=triple_id,
            subject=subject,
            predicate=predicate,
            object=object_,
            valid_from=from_ts,
            valid_to=None,
            source_fact_id=source_fact_id,
        )

    def kg_invalidate(
        self,
        subject: str,
        predicate: str,
        at: datetime | None = None,
    ) -> int:
        """Mark all currently-valid triples for (subject, predicate) as invalid."""
        invalidated_at = at or datetime.now()
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE kg_triples
                SET valid_to = ?
                WHERE subject = ? AND predicate = ? AND valid_to IS NULL
                """,
                (_dt_to_str(invalidated_at), subject, predicate),
            )
            return cursor.rowcount

    def kg_query(
        self,
        subject: str,
        predicate: str | None = None,
        at: datetime | None = None,
    ) -> list[Triple]:
        """Query triples for a subject. Returns valid triples by default.

        - ``at=None``: returns currently-valid triples (valid_to IS NULL)
        - ``at=ts``: returns triples valid at the given timestamp
        """
        if at is None:
            sql = "SELECT * FROM kg_triples WHERE subject = ? AND valid_to IS NULL"
            params: list[object] = [subject]
        else:
            sql = "SELECT * FROM kg_triples WHERE subject = ? AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            ts = _dt_to_str(at)
            params = [subject, ts, ts]
        if predicate is not None:
            sql += " AND predicate = ?"
            params.append(predicate)
        sql += " ORDER BY valid_from DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_triple(row) for row in rows]

    def kg_timeline(self, subject: str) -> list[Triple]:
        """Return all triples for a subject in chronological order."""
        rows = self.conn.execute(
            "SELECT * FROM kg_triples WHERE subject = ? ORDER BY valid_from ASC, id ASC",
            (subject,),
        ).fetchall()
        return [_row_to_triple(row) for row in rows]

    def kg_entities(self) -> list[str]:
        """Return distinct subjects across the entire graph."""
        rows = self.conn.execute("SELECT DISTINCT subject FROM kg_triples ORDER BY subject").fetchall()
        return [row["subject"] for row in rows]

    def detect_contradictions(self) -> list[ContradictionGroup]:
        """Find currently-valid triples sharing subject+predicate with different objects."""
        rows = self.conn.execute(
            """
            SELECT subject, predicate
            FROM kg_triples
            WHERE valid_to IS NULL
            GROUP BY subject, predicate
            HAVING COUNT(DISTINCT object) > 1
            """
        ).fetchall()
        groups: list[ContradictionGroup] = []
        for row in rows:
            triple_rows = self.conn.execute(
                """
                SELECT * FROM kg_triples
                WHERE subject = ? AND predicate = ? AND valid_to IS NULL
                ORDER BY valid_from ASC
                """,
                (row["subject"], row["predicate"]),
            ).fetchall()
            groups.append(
                ContradictionGroup(
                    subject=row["subject"],
                    predicate=row["predicate"],
                    conflicting_triples=[_row_to_triple(r) for r in triple_rows],
                )
            )
        return groups

    def resolve_contradiction(
        self,
        group: ContradictionGroup,
        keep_id: int,
        at: datetime | None = None,
    ) -> int:
        """Invalidate losing triples in a contradiction group, keeping ``keep_id``.

        Returns the number of invalidated triples.
        """
        invalidated_at = at or datetime.now()
        losers = [t.id for t in group.conflicting_triples if t.id is not None and t.id != keep_id]
        if not losers:
            return 0
        placeholders = ",".join("?" for _ in losers)
        with self._transaction() as conn:
            cursor = conn.execute(
                f"UPDATE kg_triples SET valid_to = ? WHERE id IN ({placeholders})",  # noqa: S608 - placeholders are fixed '?' markers
                [_dt_to_str(invalidated_at), *losers],
            )
            return cursor.rowcount

"""Concurrency tests for the shared SQLite ``open_db`` helper.

Asserts that ``open_db`` survives N concurrent writers without raising
``sqlite3.OperationalError: database is locked``. The previous bespoke
connection blocks in :mod:`core.status`, :mod:`core.memory_store`, and
:mod:`core.mcp_peer` were correct individually but easy to drift; the
shared helper is the single chokepoint, so this test guards the whole
project's SQLite contention behavior.
"""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from open_orchestrator.core._db import open_db


def _writer(db_path: Path, worker_id: int, rows: int) -> tuple[int, int]:
    """Insert ``rows`` rows from a worker thread, return (worker_id, count)."""
    conn = open_db(db_path)
    try:
        for i in range(rows):
            conn.execute(
                "INSERT INTO writes (worker_id, seq, payload) VALUES (?, ?, ?)",
                (worker_id, i, f"w{worker_id}-r{i}"),
            )
            conn.commit()
        cursor = conn.execute("SELECT COUNT(*) FROM writes WHERE worker_id = ?", (worker_id,))
        (count,) = cursor.fetchone()
        return worker_id, count
    finally:
        conn.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A fresh SQLite file with the test schema applied."""
    path = tmp_path / "concurrent.db"
    conn = open_db(path)
    conn.executescript(
        """
        CREATE TABLE writes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            payload TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    return path


def test_open_db_applies_required_pragmas(db_path: Path) -> None:
    """The helper must set WAL, busy_timeout, and synchronous on every call."""
    conn = open_db(db_path)
    try:
        (journal_mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        (busy_timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
        (synchronous,) = conn.execute("PRAGMA synchronous").fetchone()
    finally:
        conn.close()

    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000
    # synchronous=NORMAL maps to integer 1; FULL=2, OFF=0, EXTRA=3
    assert synchronous == 1


def test_open_db_returns_row_factory(db_path: Path) -> None:
    """Rows must be ``sqlite3.Row`` so callers can column-access by name."""
    conn = open_db(db_path)
    try:
        conn.execute(
            "INSERT INTO writes (worker_id, seq, payload) VALUES (?, ?, ?)",
            (1, 0, "smoke"),
        )
        conn.commit()
        row = conn.execute("SELECT worker_id, payload FROM writes").fetchone()
    finally:
        conn.close()

    assert isinstance(row, sqlite3.Row)
    assert row["worker_id"] == 1
    assert row["payload"] == "smoke"


@pytest.mark.parametrize("n_workers,rows_per_worker", [(20, 25)])
def test_concurrent_writers_no_locked_errors(db_path: Path, n_workers: int, rows_per_worker: int) -> None:
    """N=20 threads each writing 25 rows must complete without locked errors.

    Without ``busy_timeout``, this scenario reliably triggers
    ``sqlite3.OperationalError: database is locked`` on the second or third
    worker. With the helper's pragmas, all writes serialize cleanly.
    """
    barrier = threading.Barrier(n_workers)

    def gated_worker(worker_id: int) -> tuple[int, int]:
        barrier.wait()
        return _writer(db_path, worker_id, rows_per_worker)

    errors: list[BaseException] = []
    results: list[tuple[int, int]] = []
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(gated_worker, w) for w in range(n_workers)]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except BaseException as exc:  # noqa: BLE001 — surface every failure
                errors.append(exc)

    assert not errors, f"concurrent writers raised: {errors!r}"
    assert len(results) == n_workers
    # Each worker should have inserted exactly rows_per_worker rows
    for _worker_id, count in results:
        assert count == rows_per_worker

    # Cross-check the aggregate total
    conn = open_db(db_path)
    try:
        (total,) = conn.execute("SELECT COUNT(*) FROM writes").fetchone()
    finally:
        conn.close()
    assert total == n_workers * rows_per_worker

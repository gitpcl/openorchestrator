"""Small IO helpers for safe persistence.

Provides atomic_write_text() which writes to a temp file in the same
filesystem and atomically replaces the destination, then sets restrictive
permissions.

Also provides cross-platform file locking via shared_file_lock().
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

logger = logging.getLogger(__name__)


@contextmanager
def shared_file_lock(file_handle: TextIO) -> Iterator[None]:
    """Cross-platform shared (read) file lock context manager.

    On Unix, uses fcntl.flock with LOCK_SH.
    On Windows, uses msvcrt.locking (non-blocking, best-effort).
    If locking is unavailable or fails, continues without locking.

    Usage:
        with open(path) as f:
            with shared_file_lock(f):
                data = f.read()
    """
    locked = False

    try:
        if sys.platform == "win32":
            try:
                import msvcrt

                # Windows: lock first byte (advisory lock)
                msvcrt.locking(file_handle.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError:
                # Locking failed or unavailable, continue without lock
                pass
        else:
            try:
                import fcntl

                fcntl.flock(file_handle, fcntl.LOCK_SH)
                locked = True
            except OSError:
                # Locking failed or unavailable, continue without lock
                pass

        yield

    finally:
        if locked:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(file_handle, fcntl.LOCK_UN)
            except OSError:
                pass


def atomic_write_text(path: str | Path, data: str, perms: int = 0o600) -> None:
    """Atomically write text content to path with restrictive permissions.

    Steps:
    - Ensure parent directory exists
    - Write to a NamedTemporaryFile in the same directory
    - fsync the temp file
    - os.replace() to move into place atomically
    - chmod the target path to perms

    If os.replace() fails, the temp file is cleaned up before re-raising.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    tmp_name: str | None = None
    try:
        # Write to a temp file in the same directory for atomic replace
        with tempfile.NamedTemporaryFile(
            "w",
            dir=str(dest.parent),
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name

        os.replace(tmp_name, dest)
        tmp_name = None  # Successfully replaced, no cleanup needed

        try:
            os.chmod(dest, perms)
        except PermissionError:
            logger.warning(
                f"Could not set permissions {oct(perms)} on {dest}. "
                f"File was written but permissions may be insecure."
            )
    finally:
        # Clean up temp file if replace failed
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

"""PATH-lookup hardening for external binaries.

When ``open-orchestrator`` invokes ``gh`` / ``git`` / ``tmux`` / AI CLIs from
inside a worktree directory, a malicious worktree could plant a poisoned
``gh`` binary in that worktree (or in any directory it convinces the user to
prepend to ``PATH``) and intercept the call. The defense is to resolve
binaries against a fixed, allowlisted PATH that does **not** include the
current working directory or any active worktree path.

The helper is intentionally tiny: a single ``resolve_binary(name)`` entry
point, a system-default allowlist plus optional user-configured directories,
a per-process cache, and a fail-loud ``BinaryNotFoundError`` when nothing
on the allowlist matches.

Call sites that are exposed to a foreign ``cwd`` (environment install,
tmux session creation, agent launch) should resolve binaries through this
module instead of trusting the inherited PATH.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


# Conservative system defaults. Order matters: Homebrew before /usr/local
# so a brew install of a tool wins on Apple silicon, but only directories
# that ship from the OS or a package manager — never $HOME, never cwd.
_SYSTEM_DEFAULT_DIRS: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/bin",
    "/sbin",
    "/Applications/Claude.app/Contents/MacOS",
)


class BinaryNotFoundError(RuntimeError):
    """Raised when a required external binary cannot be resolved on the safe PATH.

    The error message includes the binary name and the inspected directories
    so the operator has a clear remediation path (install the binary, or add
    its directory to ``[security].extra_path`` in the config).
    """

    def __init__(self, name: str, inspected: tuple[str, ...]) -> None:
        self.name = name
        self.inspected = inspected
        joined = ":".join(inspected) if inspected else "<empty>"
        super().__init__(
            f"Required binary {name!r} not found on the safe PATH. "
            f"Inspected: {joined}. "
            f"Install it via your package manager or add its directory to the "
            f"open-orchestrator config under [security].extra_path."
        )


# Module-level cache: binary name -> resolved absolute path.
# Per-process lifetime is sufficient because the safe PATH is built from
# static directories; rebuilds across processes pick up config changes.
_resolution_cache: dict[str, str] = {}

# Extra directories the user has explicitly allowlisted. Mutated only by
# ``configure_extra_path`` so the cache stays coherent.
_extra_dirs: tuple[str, ...] = ()

# Directories to exclude (e.g. active worktree paths, cwd of the running
# orchestrator). Stored as resolved absolute paths.
_excluded_dirs: frozenset[str] = frozenset()


def configure_extra_path(dirs: list[str] | tuple[str, ...] | None) -> None:
    """Register user-configured extra directories on the safe PATH.

    Idempotent: clears the resolution cache so a reconfigured PATH is honored
    on the next call. Empty / non-existent directories are silently dropped.
    """
    global _extra_dirs
    if not dirs:
        _extra_dirs = ()
    else:
        _extra_dirs = tuple(d for d in dirs if d and Path(d).is_dir())
    _resolution_cache.clear()


def configure_excluded_dirs(dirs: list[str] | tuple[str, ...] | None) -> None:
    """Register worktree paths (and cwd) to forbid as binary sources.

    Useful when the switchboard knows the active set of worktrees; any
    directory listed here is filtered out of the safe PATH even if it would
    otherwise appear via a system default symlink. Resets the cache.
    """
    global _excluded_dirs
    if not dirs:
        _excluded_dirs = frozenset()
    else:
        resolved: set[str] = set()
        for d in dirs:
            if not d:
                continue
            try:
                resolved.add(str(Path(d).resolve()))
            except OSError:
                continue
        _excluded_dirs = frozenset(resolved)
    _resolution_cache.clear()


def _safe_path() -> str:
    """Build the colon-joined PATH used for binary resolution.

    Starts from the operator's inherited ``PATH`` so the real toolchain —
    nvm / fnm / volta / pyenv / cargo, etc., which all live under ``$HOME`` —
    stays discoverable, then drops only the entries that make resolution
    dangerous from inside a foreign worktree:

    - empty entries (an empty ``PATH`` element means "cwd"),
    - relative entries (``.``, ``bin`` — resolved against cwd),
    - the current working directory itself,
    - any directory at or under an excluded worktree path.

    User-configured extras and the conservative system defaults are appended
    last so resolution still works in a sparse ``PATH`` (cron, CI). Order is
    preserved so an earlier real entry wins over a later default. Non-existent
    directories are dropped so ``shutil.which`` never wastes a syscall.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    cwd_resolved: str | None
    try:
        cwd_resolved = str(Path.cwd().resolve())
    except OSError:
        cwd_resolved = None

    inherited = os.environ.get("PATH", "").split(os.pathsep)

    for raw in (*inherited, *_extra_dirs, *_SYSTEM_DEFAULT_DIRS):
        if not raw:
            # An empty PATH element resolves to cwd — never trust it.
            continue
        expanded = os.path.expanduser(raw)
        if not os.path.isabs(expanded):
            # Relative entries ('.', 'bin', ...) resolve against cwd. Drop.
            continue
        try:
            resolved = str(Path(expanded).resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        if cwd_resolved is not None and resolved == cwd_resolved:
            continue
        if resolved in _excluded_dirs:
            continue
        if any(resolved == ex or resolved.startswith(ex + os.sep) for ex in _excluded_dirs):
            continue
        if not Path(resolved).is_dir():
            continue
        seen.add(resolved)
        candidates.append(resolved)

    return os.pathsep.join(candidates)


def resolve_binary(name: str) -> str:
    """Resolve ``name`` to an absolute path using the allowlisted PATH.

    Cached per process. Raises :class:`BinaryNotFoundError` if no directory
    on the safe PATH contains an executable matching ``name`` — callers must
    *not* fall back to bare ``shutil.which(name)`` because that would defeat
    the hardening by re-trusting the inherited PATH.
    """
    if not name or os.sep in name:
        # Absolute / relative path supplied — pass through unchanged. The
        # caller has opted out of allowlist resolution, which is fine for
        # config-driven paths (e.g. user pinned a tool to /opt/foo/bin/x).
        return name

    cached = _resolution_cache.get(name)
    if cached is not None:
        return cached

    safe_path = _safe_path()
    resolved = shutil.which(name, path=safe_path)
    if resolved is None:
        inspected = tuple(safe_path.split(os.pathsep)) if safe_path else ()
        raise BinaryNotFoundError(name, inspected)

    _resolution_cache[name] = resolved
    return resolved


def try_resolve_binary(name: str) -> str | None:
    """Like :func:`resolve_binary` but returns ``None`` instead of raising.

    Provided for probe-style call sites (e.g. ``_command_exists``) that need
    to ask "is this tool installed?" without forcing the caller to wrap the
    lookup in a try/except.
    """
    try:
        return resolve_binary(name)
    except BinaryNotFoundError:
        return None


def clear_cache() -> None:
    """Drop the per-process resolution cache. Exposed for tests."""
    _resolution_cache.clear()

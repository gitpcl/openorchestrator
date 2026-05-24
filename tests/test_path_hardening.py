"""Tests for the PATH-hardening helper in ``core/_path.py``.

The threat model: a worktree directory contains a malicious ``gh`` binary,
and the operator inadvertently runs open-orchestrator with ``PATH`` set to
that worktree (or with the worktree as cwd). The resolver must never hand
back the poisoned binary.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from open_orchestrator.core import _path
from open_orchestrator.core._path import (
    BinaryNotFoundError,
    clear_cache,
    configure_excluded_dirs,
    configure_extra_path,
    resolve_binary,
    try_resolve_binary,
)


@pytest.fixture(autouse=True)
def _reset_path_state():
    """Reset the helper's module-level state between tests."""
    clear_cache()
    configure_extra_path(None)
    configure_excluded_dirs(None)
    yield
    clear_cache()
    configure_extra_path(None)
    configure_excluded_dirs(None)


def _plant_fake_binary(directory: Path, name: str, marker: str = "FAKE") -> Path:
    """Write an executable script that prints ``marker`` and return its path."""
    binary = directory / name
    binary.write_text(f"#!/bin/sh\necho '{marker}'\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


def test_resolve_binary_ignores_poisoned_cwd(tmp_path, monkeypatch):
    """A fake gh planted in cwd and on PATH must NOT be returned."""
    fake = _plant_fake_binary(tmp_path, "gh")
    # Prepend the poisoned dir to PATH and switch cwd into it — the worst
    # case a hostile worktree can engineer for a careless operator.
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.chdir(tmp_path)

    # The resolver must not return the poisoned binary. Two outcomes are
    # acceptable: a real ``gh`` exists on the system safe PATH (in which
    # case we got it instead of the fake), or no ``gh`` is installed (in
    # which case we raise BinaryNotFoundError). The poisoned path must
    # never be the answer.
    try:
        resolved = resolve_binary("gh")
    except BinaryNotFoundError as e:
        # Confirm the inspected list never included the poisoned tmpdir.
        assert str(tmp_path) not in e.inspected, f"Poisoned tmpdir {tmp_path} leaked into safe PATH: {e.inspected}"
        return

    assert resolved != str(fake), "Resolver returned the poisoned binary"
    assert not resolved.startswith(str(tmp_path)), f"Resolver returned a path under the poisoned tmpdir: {resolved}"


def test_resolve_binary_ignores_excluded_dirs(tmp_path, monkeypatch):
    """A binary inside an explicitly excluded worktree is not resolvable.

    Simulates the switchboard registering the active worktree paths via
    :func:`configure_excluded_dirs` — those directories must be filtered
    out even if added through ``configure_extra_path``.
    """
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _plant_fake_binary(worktree, "owt-test-binary-xyz")

    # Allowlist the worktree (simulating misconfiguration) and then mark
    # it excluded. Exclusion must win.
    configure_extra_path([str(worktree)])
    configure_excluded_dirs([str(worktree)])

    assert try_resolve_binary("owt-test-binary-xyz") is None


def test_resolve_binary_uses_extra_path_when_safe(tmp_path):
    """A user-configured extra dir that isn't excluded is honored."""
    safe_dir = tmp_path / "extra"
    safe_dir.mkdir()
    fake = _plant_fake_binary(safe_dir, "owt-test-binary-abc")

    configure_extra_path([str(safe_dir)])

    resolved = resolve_binary("owt-test-binary-abc")
    assert resolved == str(fake)


def test_resolve_binary_raises_with_helpful_message():
    """Missing binaries surface a clear remediation message."""
    with pytest.raises(BinaryNotFoundError) as excinfo:
        resolve_binary("definitely-not-a-real-binary-xyz")

    msg = str(excinfo.value)
    assert "definitely-not-a-real-binary-xyz" in msg
    assert "open-orchestrator" in msg or "PATH" in msg


def test_resolve_binary_passes_through_absolute_paths(tmp_path):
    """Absolute / relative paths bypass the allowlist (caller opted out)."""
    fake = _plant_fake_binary(tmp_path, "owt-absolute-test")
    # Absolute path with os.sep must be returned unchanged.
    assert resolve_binary(str(fake)) == str(fake)


def test_resolve_binary_caches_per_process(tmp_path, monkeypatch):
    """A successful resolution is cached and survives PATH mutation."""
    safe_dir = tmp_path / "extra"
    safe_dir.mkdir()
    fake = _plant_fake_binary(safe_dir, "owt-cache-test")
    configure_extra_path([str(safe_dir)])

    first = resolve_binary("owt-cache-test")
    assert first == str(fake)

    # Mutating the underlying directory shouldn't change the cached answer.
    fake.unlink()
    second = resolve_binary("owt-cache-test")
    assert second == first


def test_configure_extra_path_clears_cache(tmp_path):
    """Reconfiguring extra PATH dirs invalidates stale cache entries."""
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    fake_a = _plant_fake_binary(dir_a, "owt-reconfig-test")
    configure_extra_path([str(dir_a)])
    assert resolve_binary("owt-reconfig-test") == str(fake_a)

    dir_b = tmp_path / "b"
    dir_b.mkdir()
    fake_b = _plant_fake_binary(dir_b, "owt-reconfig-test")
    configure_extra_path([str(dir_b)])
    # Cache must have been invalidated.
    assert resolve_binary("owt-reconfig-test") == str(fake_b)


def test_safe_path_excludes_cwd(tmp_path, monkeypatch):
    """``_safe_path`` must not include the current working directory."""
    monkeypatch.chdir(tmp_path)
    # Even if the user accidentally adds cwd as extra_path, it should drop.
    configure_extra_path([str(tmp_path)])
    safe = _path._safe_path()
    # Resolve cwd the same way the helper does.
    cwd_resolved = str(Path.cwd().resolve())
    assert cwd_resolved not in safe.split(os.pathsep)

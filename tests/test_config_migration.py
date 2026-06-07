"""Removed-key migration UX (Sprint 028 / 0.5.0).

The cockpit reposition deleted several config sections (the Agno
intelligence layer, critic, recall/memory, dream, swarm, the legacy
switchboard UI). ``Config`` uses ``extra="forbid"``, so a user upgrading
from 0.4.0 with valid-yesterday config would otherwise get a "check
spelling" hint for a key they didn't misspell. These tests assert the
friendly "removed in 0.5.0, safe to delete" path instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from open_orchestrator.config import ConfigError, load_config

# (toml snippet, the top-level key the user will see named back)
REMOVED_KEY_CASES = [
    ("[agno]\nenabled = true\n", "agno"),
    ('[intelligence]\nmodel = "x"\n', "intelligence"),
    ("[critic]\nenabled = true\n", "critic"),
    ("critic_enabled = true\n", "critic_enabled"),
    ("[recall]\nenabled = true\n", "recall"),
    ("recall_enabled = true\n", "recall_enabled"),
    ("[memory]\nmax = 10\n", "memory"),
    ("[dream]\nenabled = true\n", "dream"),
    ("dream_enabled = true\n", "dream_enabled"),
    ("dream_interval = 30\n", "dream_interval"),
    ("[swarm]\nsize = 3\n", "swarm"),
    ('[switchboard]\nbackground_color = "#1a1b2e"\n', "switchboard"),
]


@pytest.mark.parametrize("snippet,key", REMOVED_KEY_CASES)
def test_removed_key_yields_migration_message(tmp_path: Path, snippet: str, key: str) -> None:
    cfg = tmp_path / ".worktreerc"
    cfg.write_text(snippet)

    with pytest.raises(ConfigError) as exc:
        load_config(str(cfg))

    message = str(exc.value)
    assert "removed in 0.5.0" in message
    assert "safe to delete" in message.lower()
    assert key in message
    # It must NOT be misclassified as a typo.
    assert "check spelling" not in message


def test_genuine_typo_still_gets_spelling_hint(tmp_path: Path) -> None:
    """A real unknown key (not a deleted feature) keeps the spelling hint —
    the migration path must not swallow typo detection."""
    cfg = tmp_path / ".worktreerc"
    cfg.write_text('[worktree]\nbase_braanch = "main"\n')  # typo: base_braanch

    with pytest.raises(ConfigError) as exc:
        load_config(str(cfg))

    message = str(exc.value)
    assert "check spelling" in message
    assert "removed in 0.5.0" not in message


def test_switchboard_section_is_removed_not_a_noop(tmp_path: Path) -> None:
    """[switchboard] was a real 0.4.0 section; after the cut it must be
    reported as removed (not silently accepted as a no-op)."""
    cfg = tmp_path / ".worktreerc"
    cfg.write_text('[switchboard]\nbackground_color = "#000000"\n')

    with pytest.raises(ConfigError) as exc:
        load_config(str(cfg))

    assert "switchboard" in str(exc.value)
    assert "control plane" in str(exc.value)

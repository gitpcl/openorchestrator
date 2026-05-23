"""Backend (multiplexer) data models.

A *backend* is owt's abstraction over the terminal multiplexer that
hosts agent panes. Sprint 025 introduces this so herdr can ride
alongside tmux without leaking herdr-specific code into call sites.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BackendKind(str, Enum):
    """Which multiplexer backend hosts the session."""

    TMUX = "tmux"
    HERDR = "herdr"


class BackendSession(BaseModel):
    """Opaque handle to a session running inside a backend.

    For tmux: ``id`` is the tmux session name.
    For herdr: ``id`` is the pane id; ``meta`` carries the workspace id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: BackendKind = Field(description="Which backend owns this session")
    id: str = Field(description="Multiplexer-native identifier (tmux session, herdr pane id)")
    worktree_name: str = Field(description="Worktree name the session belongs to")
    meta: dict[str, str] = Field(default_factory=dict, description="Backend-specific metadata")


class BackendConfig(BaseModel):
    """``[backend]`` section of ``open-orchestrator.toml``."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["tmux", "herdr", "auto"] = Field(
        default="tmux",
        description="'tmux' is the default; 'herdr' opts in; 'auto' picks herdr when available.",
    )
    herdr_session: str = Field(
        default="default",
        description="Named herdr session to target (selects which socket to talk to).",
    )
    herdr_socket: str | None = Field(
        default=None,
        description="Override the herdr socket path (default: $XDG_CONFIG_HOME/herdr/herdr.sock).",
    )

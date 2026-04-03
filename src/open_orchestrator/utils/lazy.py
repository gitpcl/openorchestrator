"""Lazy module proxy for deferred imports of optional dependencies."""

from __future__ import annotations

import importlib
from types import ModuleType


class LazyModule:
    """Proxy that defers module import until first attribute access.

    Usage:
        agno = LazyModule("agno", install_hint="pip install open-orchestrator[agno]")
        # Module is not imported until you access an attribute:
        agent = agno.Agent  # triggers import here
    """

    def __init__(self, module_name: str, install_hint: str | None = None) -> None:
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_install_hint", install_hint)
        object.__setattr__(self, "_module", None)

    def _load(self) -> ModuleType:
        mod: ModuleType | None = object.__getattribute__(self, "_module")
        if mod is not None:
            return mod
        name = object.__getattribute__(self, "_module_name")
        hint = object.__getattribute__(self, "_install_hint")
        try:
            mod = importlib.import_module(name)
        except ImportError as e:
            msg = f"Optional dependency '{name}' is required for this feature."
            if hint:
                msg += f" Install with: {hint}"
            raise ImportError(msg) from e
        object.__setattr__(self, "_module", mod)
        return mod

    def __getattr__(self, name: str) -> object:
        return getattr(self._load(), name)

    def __repr__(self) -> str:
        mod = object.__getattribute__(self, "_module")
        name = object.__getattribute__(self, "_module_name")
        if mod is not None:
            return repr(mod)
        return f"<LazyModule '{name}' (not yet loaded)>"

"""Tests for LazyModule deferred import proxy."""

from __future__ import annotations

import pytest

from open_orchestrator.utils.lazy import LazyModule


class TestLazyModule:
    """Test LazyModule deferred import behavior."""

    def test_does_not_import_on_creation(self) -> None:
        """Module should not be imported at construction time."""
        lazy = LazyModule("json")
        # Access internal state directly to verify no import happened
        assert object.__getattribute__(lazy, "_module") is None

    def test_imports_on_first_attribute_access(self) -> None:
        """Accessing an attribute triggers the import."""
        lazy = LazyModule("json")
        # This should trigger the import
        loads = lazy.loads  # noqa: F841
        assert object.__getattribute__(lazy, "_module") is not None

    def test_returns_correct_attribute(self) -> None:
        """Attribute access should return the real module's attribute."""
        import json

        lazy = LazyModule("json")
        assert lazy.dumps is json.dumps

    def test_caches_module_after_first_load(self) -> None:
        """Second access should not re-import."""
        lazy = LazyModule("json")
        _ = lazy.dumps
        mod1 = object.__getattribute__(lazy, "_module")
        _ = lazy.loads
        mod2 = object.__getattribute__(lazy, "_module")
        assert mod1 is mod2

    def test_missing_module_raises_import_error(self) -> None:
        """Accessing attribute on missing module raises ImportError."""
        lazy = LazyModule("nonexistent_module_abc123")
        with pytest.raises(ImportError, match="Optional dependency"):
            _ = lazy.some_attr

    def test_install_hint_included_in_error(self) -> None:
        """ImportError message should include the install hint."""
        lazy = LazyModule("nonexistent_xyz", install_hint="pip install nonexistent_xyz")
        with pytest.raises(ImportError, match="pip install nonexistent_xyz"):
            _ = lazy.attr

    def test_repr_before_load(self) -> None:
        """repr should show 'not yet loaded' before import."""
        lazy = LazyModule("json")
        assert "not yet loaded" in repr(lazy)
        assert "json" in repr(lazy)

    def test_repr_after_load(self) -> None:
        """repr should show the real module repr after import."""
        lazy = LazyModule("json")
        _ = lazy.dumps  # trigger load
        assert "json" in repr(lazy)
        assert "not yet loaded" not in repr(lazy)

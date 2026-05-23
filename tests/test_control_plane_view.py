"""Sprint 024: tests for the Textual control plane view (Pilot-driven)."""

from __future__ import annotations

import pytest

from open_orchestrator.core.control_plane_view import ControlPlaneApp, SectionWidget
from open_orchestrator.models.control_plane import ControlPlaneRow, RowAction, SectionKind


@pytest.mark.asyncio
async def test_view_mounts_one_widget_per_section(tmp_path) -> None:  # noqa: ANN001
    app = ControlPlaneApp(repo_root=tmp_path, refresh_seconds=999)
    async with app.run_test() as pilot:
        del pilot
        widgets = list(app.query(SectionWidget))
        assert {w.kind for w in widgets} == set(SectionKind)


@pytest.mark.asyncio
async def test_empty_section_gets_empty_class(tmp_path) -> None:  # noqa: ANN001
    app = ControlPlaneApp(repo_root=tmp_path, refresh_seconds=999)
    async with app.run_test() as pilot:
        await pilot.pause()
        widgets = {w.kind: w for w in app.query(SectionWidget)}
        # With no worktrees, all sections are empty
        for w in widgets.values():
            assert "empty" in w.classes or len(w.rows) == 0


@pytest.mark.asyncio
async def test_render_focus_marker(tmp_path) -> None:  # noqa: ANN001
    app = ControlPlaneApp(repo_root=tmp_path, refresh_seconds=999)
    async with app.run_test() as pilot:
        widget = SectionWidget(SectionKind.READY_TO_SHIP)
        await pilot.pause()
        row = ControlPlaneRow(
            id="x",
            section=SectionKind.READY_TO_SHIP,
            name="wt",
            summary="+2 commits",
            actions=(RowAction.SHIP,),
        )
        widget.update_rows([row], focused_row=0)
        rendered = widget.render()
        text = rendered.plain
        assert "▶" in text
        assert "[s]" in text


@pytest.mark.asyncio
async def test_navigation_keys(tmp_path) -> None:  # noqa: ANN001
    """Up / Down should not error when there are no rows."""
    app = ControlPlaneApp(repo_root=tmp_path, refresh_seconds=999)
    async with app.run_test() as pilot:
        await pilot.press("down")
        await pilot.press("up")
        # No assertion needed — absence of exception is the test

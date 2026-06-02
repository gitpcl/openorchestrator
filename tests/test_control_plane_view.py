"""Sprint 024: tests for the Textual control plane view (Pilot-driven)."""

from __future__ import annotations

import pytest

from open_orchestrator.core.control_plane_view import (
    SECTION_ORDER,
    ControlPlaneApp,
    SectionWidget,
)
from open_orchestrator.models.control_plane import ControlPlaneRow, RowAction, SectionKind


@pytest.fixture(autouse=True)
def _isolated_status_db(tmp_path, monkeypatch):  # noqa: ANN001
    """Point StatusTracker at a per-test SQLite file via OWT_DB_PATH.

    Without this, every test in the module reads from the user's shared
    ``~/.open-orchestrator/status.db`` and inherits whatever leftover
    worktree rows happen to be there (the empty-section invariant breaks
    the moment a real ``owt new`` has ever run on the developer machine).
    """
    monkeypatch.setenv("OWT_DB_PATH", str(tmp_path / "status.db"))


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


@pytest.mark.asyncio
async def test_footer_shows_nav_new_quit_when_no_rows(tmp_path) -> None:  # noqa: ANN001
    """With nothing focused, the footer always offers nav / new / quit."""
    app = ControlPlaneApp(repo_root=tmp_path, refresh_seconds=999)
    async with app.run_test() as pilot:
        await pilot.pause()
        footer = app._build_footer()
        assert "nav" in footer
        assert "new" in footer
        assert "quit" in footer
        # No row focused → no row-action verbs.
        assert "ship" not in footer


@pytest.mark.asyncio
async def test_footer_reflects_focused_row_actions(tmp_path) -> None:  # noqa: ANN001
    """The footer lists exactly the focused row's actions, and nothing else."""
    app = ControlPlaneApp(repo_root=tmp_path, refresh_seconds=999)
    async with app.run_test() as pilot:
        await pilot.pause()
        row = ControlPlaneRow(
            id="x",
            section=SectionKind.READY_TO_SHIP,
            name="wt",
            summary="+2 commits",
            actions=(RowAction.SHIP, RowAction.MERGE),
        )
        app._sections[SectionKind.READY_TO_SHIP] = [row]
        app._focus.section = SECTION_ORDER.index(SectionKind.READY_TO_SHIP)
        app._focus.row = 0
        footer = app._build_footer()
        assert "ship" in footer
        assert "merge" in footer
        # Actions not on the row must not appear.
        assert "fix" not in footer
        assert "dismiss" not in footer

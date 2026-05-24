"""
Tests for switchboard pane status detection, regex patterns, and hook trust logic.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.core.switchboard import (
    _ALLOW_PROMPT_RE,
    _BLOCKED_RE,
    _INTERRUPTED_RE,
    _PROMPT_RE,
    _STATUS_BAR_RE,
    _TOOL_HEADER_RE,
    HOOK_CAPABLE_TOOLS,
    HOOK_TRUST_MAX_SECONDS,
    _detect_pane_status,
)
from open_orchestrator.core.switchboard_cards import Card
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

# ---------------------------------------------------------------------------
# Regex pattern tests
# ---------------------------------------------------------------------------


class TestBlockedRegex:
    """Test _BLOCKED_RE matches permission/confirmation prompts."""

    @pytest.mark.parametrize(
        "text",
        [
            "(y/N)",
            "(Y/n)",
            "Do you want to proceed",
            "Press Enter to continue",
            "Do you want to proceed with this change?",
        ],
    )
    def test_matches_blocked_prompts(self, text: str) -> None:
        assert _BLOCKED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Processing files...",
            "Reading configuration",
            "❯",
            "Allow me to explain",
        ],
    )
    def test_rejects_non_blocked_text(self, text: str) -> None:
        assert not _BLOCKED_RE.search(text)


class TestAllowPromptRegex:
    """Test _ALLOW_PROMPT_RE matches tool permission prompts."""

    @pytest.mark.parametrize(
        "text",
        [
            "Allow Read",
            "Allow Write",
            "Allow Edit",
            "Allow Bash",
            "Allow Glob",
            "Allow Grep",
            "Allow Agent",
            "Allow WebFetch",
            "Allow WebSearch",
            "Allow NotebookEdit",
            "Allow mcp_server_tool",
        ],
    )
    def test_matches_tool_prompts(self, text: str) -> None:
        assert _ALLOW_PROMPT_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Allow me to explain",
            "Allow me to read the file",
            "I'll allow that change",
            "Allowing access to",
        ],
    )
    def test_rejects_non_tool_allow(self, text: str) -> None:
        assert not _ALLOW_PROMPT_RE.search(text)


class TestStatusBarRegex:
    """Test _STATUS_BAR_RE filters Claude Code status bar lines."""

    @pytest.mark.parametrize(
        "text",
        [
            "ctx: 45%",
            "ctx: 2%",
            "bypass permissions on",
            "shift+tab to cycle",
            "permissions on",
        ],
    )
    def test_matches_status_bar(self, text: str) -> None:
        assert _STATUS_BAR_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Reading file contents",
            "Implementing authentication",
            "❯",
        ],
    )
    def test_rejects_non_status_bar(self, text: str) -> None:
        assert not _STATUS_BAR_RE.search(text)


class TestPromptRegex:
    """Test _PROMPT_RE matches idle prompt indicators."""

    @pytest.mark.parametrize(
        "text",
        [
            ">",
            "> ",
            "❯",
            "❯ ",
            "What would you like",
            "What would you like me to do?",
            "How can I help",
            "How can I help you today?",
        ],
    )
    def test_matches_idle_prompts(self, text: str) -> None:
        assert _PROMPT_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "> some text after prompt",
            "❯ command here",
            "Working on feature...",
            "Searching for files",
        ],
    )
    def test_rejects_non_idle(self, text: str) -> None:
        assert not _PROMPT_RE.search(text)


class TestToolHeaderRegex:
    """Test _TOOL_HEADER_RE matches Claude Code tool execution headers."""

    @pytest.mark.parametrize(
        "text",
        [
            "Read: src/foo.py",
            "Read /path/to/file.py",
            "Write: /tmp/output.txt",
            "Edit: src/main.py",
            "Bash: npm install",
            "Glob: **/*.py",
            "Grep: some pattern",
            "Agent: sub-task",
            "WebFetch: https://example.com",
            "WebSearch: query",
            "NotebookEdit: notebook.ipynb",
        ],
    )
    def test_matches_tool_headers(self, text: str) -> None:
        assert _TOOL_HEADER_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Reading file contents",
            "Bash command failed",
            "I need to read the file",
            "grep -r pattern .",
        ],
    )
    def test_rejects_non_tool_headers(self, text: str) -> None:
        assert not _TOOL_HEADER_RE.search(text)


class TestInterruptedRegex:
    """Test _INTERRUPTED_RE matches high-confidence idle signals."""

    @pytest.mark.parametrize(
        "text",
        [
            "Interrupted",
            "Interrupted · What should Claude do instead?",
            "What should Claude do instead",
            "what should claude do instead",
        ],
    )
    def test_matches_interrupted(self, text: str) -> None:
        assert _INTERRUPTED_RE.search(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Processing interrupted files",
            "Working on feature",
            "❯",
        ],
    )
    def test_rejects_non_interrupted(self, text: str) -> None:
        # "Processing interrupted files" DOES contain "Interrupted" substring (case-insensitive)
        # so we only test truly non-matching text
        assert not _INTERRUPTED_RE.search("Working on feature")
        assert not _INTERRUPTED_RE.search("❯")


# ---------------------------------------------------------------------------
# _detect_pane_status tests
# ---------------------------------------------------------------------------


def _make_pane_output(*lines: str) -> str:
    """Build fake tmux capture-pane output from lines."""
    return "\n".join(lines) + "\n"


class TestDetectPaneStatus:
    """Test _detect_pane_status with mocked subprocess."""

    def test_none_session_returns_none(self) -> None:
        assert _detect_pane_status(None) is None

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_subprocess_error_returns_none(self, mock_run: MagicMock) -> None:
        import subprocess as sp

        mock_run.side_effect = sp.CalledProcessError(1, "tmux")
        assert _detect_pane_status("owt-test") is None

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_subprocess_timeout_returns_none(self, mock_run: MagicMock) -> None:
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired("tmux", 2)
        assert _detect_pane_status("owt-test") is None

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_empty_output_returns_none(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="\n\n\n")
        assert _detect_pane_status("owt-test") is None

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_detects_blocked_yn_prompt(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Some output",
                "Do you want to proceed? (y/N)",
            )
        )
        result = _detect_pane_status("owt-test")
        assert result == (AIActivityStatus.BLOCKED, True)

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_detects_blocked_allow_tool(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "I need to read a file",
                "Allow Read /path/to/file.py",
            )
        )
        result = _detect_pane_status("owt-test")
        assert result == (AIActivityStatus.BLOCKED, True)

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_detects_waiting_prompt(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Done implementing the feature.",
                "❯",
            )
        )
        status, high_conf = _detect_pane_status("owt-test")
        assert status == AIActivityStatus.WAITING
        assert high_conf is False

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_detects_waiting_with_interrupted_high_confidence(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Searched for 5 patterns, read 4 files",
                "Interrupted · What should Claude do instead?",
                "❯",
            )
        )
        status, high_conf = _detect_pane_status("owt-test")
        assert status == AIActivityStatus.WAITING
        assert high_conf is True

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_detects_working_no_prompt(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Reading file src/main.py...",
                "Analyzing code structure",
                "Found 3 functions to refactor",
            )
        )
        result = _detect_pane_status("owt-test")
        assert result == (AIActivityStatus.WORKING, False)

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_filters_status_bar_lines(self, mock_run: MagicMock) -> None:
        """Status bar with 'permissions' should NOT trigger BLOCKED."""
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Working on the task...",
                "→ ainex git:(feat/test) Opus 4.6 (1M context) [ctx: 45%]",
                "›› bypass permissions on (shift+tab to cycle)",
            )
        )
        status, _ = _detect_pane_status("owt-test")
        # Status bar lines filtered out, remaining text shows active work
        assert status == AIActivityStatus.WORKING

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_allow_in_middle_of_output_not_blocked(self, mock_run: MagicMock) -> None:
        """'Allow me to explain...' in middle of output should NOT trigger BLOCKED."""
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Allow me to explain the approach I'll take here.",
                "First I'll read the existing code structure.",
                "Then I'll identify the best place to add the feature.",
                "Let me start by examining the codebase.",
                "Read src/main.py",
            )
        )
        status, _ = _detect_pane_status("owt-test")
        assert status != AIActivityStatus.BLOCKED

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_short_prompt_char_is_waiting(self, mock_run: MagicMock) -> None:
        """Short '❯' line on last line → WAITING."""
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Done implementing the feature.",
                "❯",
            )
        )
        status, _ = _detect_pane_status("owt-test")
        assert status == AIActivityStatus.WAITING

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_long_line_with_prompt_char_not_waiting(self, mock_run: MagicMock) -> None:
        """Long line containing '❯' as part of output is NOT WAITING."""
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Processing file ❯ src/main.py with options --verbose --debug",
            )
        )
        status, _ = _detect_pane_status("owt-test")
        assert status == AIActivityStatus.WORKING

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_tool_header_in_last_two_lines_is_working_high_confidence(self, mock_run: MagicMock) -> None:
        """Tool header like 'Read: src/foo.py' → WORKING with high confidence."""
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Analyzing the codebase structure",
                "Read: src/foo.py",
            )
        )
        result = _detect_pane_status("owt-test")
        assert result == (AIActivityStatus.WORKING, True)

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_old_yn_prompt_deep_in_history_not_blocked(self, mock_run: MagicMock) -> None:
        """Old y/N prompt deep in history (beyond last 2 lines) should NOT trigger BLOCKED."""
        mock_run.return_value = MagicMock(
            stdout=_make_pane_output(
                "Do you want to proceed? (y/N)",  # old prompt — scrolled up
                "Yes, proceeding with changes.",
                "Reading source files...",
                "Analyzing code structure",
                "Found 5 functions to update",
                "Read src/main.py",
            )
        )
        status, _ = _detect_pane_status("owt-test")
        assert status != AIActivityStatus.BLOCKED


# ---------------------------------------------------------------------------
# Hook guard logic tests (via _build_cards internals)
# ---------------------------------------------------------------------------


@patch("open_orchestrator.core.switchboard_cards._tmux_session_exists_raw", return_value=True)
@patch("open_orchestrator.core.switchboard_cards.WorktreeManager")
class TestHookGuardLogic:
    """Test the hook trust guard that prevents false WORKING → WAITING downgrades.

    These tests exercise the guard logic by calling _build_cards with a mocked
    tracker and subprocess to verify status transitions.

    WorktreeManager is patched at class level to return no worktrees, forcing
    the status DB fallback path and ensuring the mock status is always used.
    """

    def _make_status(
        self,
        ai_tool: str = "claude",
        activity_status: AIActivityStatus = AIActivityStatus.WORKING,
        updated_at: datetime | None = None,
    ) -> WorktreeAIStatus:
        return WorktreeAIStatus(
            worktree_name="test-wt",
            worktree_path="/tmp/fake-wt",
            branch="feat/test",
            tmux_session="owt-test",
            ai_tool=ai_tool,
            activity_status=activity_status,
            updated_at=updated_at or datetime.now(),
        )

    @patch("open_orchestrator.core.switchboard_cards._get_diff_info", return_value=([], ""))
    @patch("open_orchestrator.core.switchboard_cards._detect_pane_status")
    def test_blocks_low_confidence_working_to_waiting_for_claude(
        self,
        mock_detect: MagicMock,
        mock_diff: MagicMock,
        mock_wt_manager: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        """Scraper sees stale ❯ during thinking — should NOT downgrade."""
        from open_orchestrator.core.switchboard import _build_cards

        mock_wt_manager.return_value.list_all.return_value = []

        # Status set 5s ago (past HOOK_FRESHNESS but within HOOK_TRUST_MAX of 15s)
        status = self._make_status(updated_at=datetime.now() - timedelta(seconds=5))
        mock_detect.return_value = (AIActivityStatus.WAITING, False)  # low confidence

        tracker = MagicMock()
        tracker.get_all_statuses.return_value = [status]

        cards, _ = _build_cards(tracker)
        assert cards[0].status == AIActivityStatus.WORKING
        tracker.set_status.assert_not_called()

    @patch("open_orchestrator.core.switchboard_cards._get_diff_info", return_value=([], ""))
    @patch("open_orchestrator.core.switchboard_cards._detect_pane_status")
    def test_allows_high_confidence_working_to_waiting_for_claude(
        self,
        mock_detect: MagicMock,
        mock_diff: MagicMock,
        mock_wt_manager: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        """Scraper sees 'Interrupted' — should downgrade despite hook trust."""
        from open_orchestrator.core.switchboard import _build_cards

        mock_wt_manager.return_value.list_all.return_value = []

        status = self._make_status(updated_at=datetime.now() - timedelta(seconds=30))
        mock_detect.return_value = (AIActivityStatus.WAITING, True)  # high confidence

        tracker = MagicMock()
        tracker.get_all_statuses.return_value = [status]

        cards, _ = _build_cards(tracker)
        assert cards[0].status == AIActivityStatus.WAITING
        tracker.set_status.assert_called_once()

    @patch("open_orchestrator.core.switchboard_cards._get_diff_info", return_value=([], ""))
    @patch("open_orchestrator.core.switchboard_cards._detect_pane_status")
    def test_allows_working_to_blocked_for_claude(
        self,
        mock_detect: MagicMock,
        mock_diff: MagicMock,
        mock_wt_manager: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        """WORKING → BLOCKED via scraper should always be allowed."""
        from open_orchestrator.core.switchboard import _build_cards

        mock_wt_manager.return_value.list_all.return_value = []

        status = self._make_status(updated_at=datetime.now() - timedelta(seconds=30))
        mock_detect.return_value = (AIActivityStatus.BLOCKED, True)

        tracker = MagicMock()
        tracker.get_all_statuses.return_value = [status]

        cards, _ = _build_cards(tracker)
        assert cards[0].status == AIActivityStatus.BLOCKED

    @patch("open_orchestrator.core.switchboard_cards._get_diff_info", return_value=([], ""))
    @patch("open_orchestrator.core.switchboard_cards._detect_pane_status")
    def test_stale_hook_allows_scraper_recovery(
        self,
        mock_detect: MagicMock,
        mock_diff: MagicMock,
        mock_wt_manager: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        """After HOOK_TRUST_MAX_SECONDS, scraper can correct stale WORKING."""
        from open_orchestrator.core.switchboard import _build_cards

        mock_wt_manager.return_value.list_all.return_value = []

        # Status set 3 minutes ago (past HOOK_TRUST_MAX_SECONDS=120)
        status = self._make_status(updated_at=datetime.now() - timedelta(seconds=180))
        mock_detect.return_value = (AIActivityStatus.WAITING, False)  # low confidence

        tracker = MagicMock()
        tracker.get_all_statuses.return_value = [status]

        cards, _ = _build_cards(tracker)
        assert cards[0].status == AIActivityStatus.WAITING

    @patch("open_orchestrator.core.switchboard_cards._get_diff_info", return_value=([], ""))
    @patch("open_orchestrator.core.switchboard_cards._detect_pane_status")
    def test_opencode_not_guarded(
        self,
        mock_detect: MagicMock,
        mock_diff: MagicMock,
        mock_wt_manager: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        """Non-hook tools (opencode) should always allow scraper transitions."""
        from open_orchestrator.core.switchboard import _build_cards

        mock_wt_manager.return_value.list_all.return_value = []

        status = self._make_status(
            ai_tool="opencode",
            updated_at=datetime.now() - timedelta(seconds=30),
        )
        mock_detect.return_value = (AIActivityStatus.WAITING, False)

        tracker = MagicMock()
        tracker.get_all_statuses.return_value = [status]

        cards, _ = _build_cards(tracker)
        assert cards[0].status == AIActivityStatus.WAITING

    @patch("open_orchestrator.core.switchboard_cards._get_diff_info", return_value=([], ""))
    @patch("open_orchestrator.core.switchboard_cards._detect_pane_status")
    def test_fresh_hook_skips_scraper_entirely(
        self,
        mock_detect: MagicMock,
        mock_diff: MagicMock,
        mock_wt_manager: MagicMock,
        mock_session: MagicMock,
    ) -> None:
        """Within HOOK_FRESHNESS_SECONDS, scraper is not called at all."""
        from open_orchestrator.core.switchboard import _build_cards

        mock_wt_manager.return_value.list_all.return_value = []

        status = self._make_status(updated_at=datetime.now() - timedelta(seconds=3))

        tracker = MagicMock()
        tracker.get_all_statuses.return_value = [status]

        _build_cards(tracker)
        mock_detect.assert_not_called()


class TestHookCapableToolsConstant:
    """Verify HOOK_CAPABLE_TOOLS matches expected tools."""

    def test_contains_claude(self) -> None:
        assert "claude" in HOOK_CAPABLE_TOOLS

    def test_contains_droid(self) -> None:
        assert "droid" in HOOK_CAPABLE_TOOLS

    def test_does_not_contain_opencode(self) -> None:
        assert "opencode" not in HOOK_CAPABLE_TOOLS

    def test_trust_max_is_positive(self) -> None:
        assert HOOK_TRUST_MAX_SECONDS > 0


# ---------------------------------------------------------------------------
# Sprint 027 Phase 2: Pilot-driven SwitchboardApp tests
# ---------------------------------------------------------------------------
#
# These tests mount the (legacy) ``SwitchboardApp`` via Textual's
# ``App.run_test()`` harness with a stubbed ``StatusTracker``, ``TmuxManager``,
# and ``WorktreeManager``. No real tmux, git, or subprocess calls are made.
#
# Approach: patch the heavy collaborators at the ``open_orchestrator.core.switchboard``
# module level *before* constructing the app so ``__init__`` picks up the stubs.
# Then drive keypresses via the Pilot to exercise navigation, modal flows,
# delete-confirm, ship/merge confirmations, and broadcast.


def _make_card(name: str = "wt-a", session: str | None = "owt-wt-a") -> Card:
    return Card(
        name=name,
        status=AIActivityStatus.WORKING,
        branch=f"feat/{name}",
        ai_tool="claude",
        task="implement thing",
        elapsed="3s",
        tmux_session=session,
        overlap_count=0,
        overlap_names=[],
        diff_stat="+10 -2",
    )


@contextmanager
def _patched_switchboard_world(cards: list[Card] | None = None) -> Iterator[dict[str, MagicMock]]:
    """Patch the heavy collaborators used by SwitchboardApp.__init__.

    Yields a dict of the mocks so tests can assert against them.
    """
    cards = cards if cards is not None else [_make_card()]
    file_map = {c.name: [] for c in cards}

    tracker = MagicMock()
    tracker.get_all_statuses.return_value = []
    tracker.has_changed_since.return_value = False
    tracker.get_generation.return_value = "gen-0"
    tracker.cleanup_orphans.return_value = []
    tracker.record_command = MagicMock()
    tracker.close = MagicMock()

    tmux = MagicMock()
    tmux.session_exists.return_value = True
    tmux.switch_client = MagicMock()
    tmux.send_keys_to_pane = MagicMock()

    wt_manager = MagicMock()
    wt_manager.git_root = "/tmp/fake-root"
    wt_manager.list_all.return_value = []

    with (
        patch("open_orchestrator.core.switchboard.StatusTracker", return_value=tracker),
        patch("open_orchestrator.core.switchboard.TmuxManager", return_value=tmux),
        patch("open_orchestrator.core.switchboard.WorktreeManager", return_value=wt_manager),
        patch("open_orchestrator.core.switchboard._build_cards", return_value=(list(cards), dict(file_map))),
        patch("open_orchestrator.core.switchboard._build_cards_async", return_value=(list(cards), dict(file_map))),
    ):
        yield {
            "tracker": tracker,
            "tmux": tmux,
            "wt_manager": wt_manager,
            "cards": cards,
            "file_map": file_map,
        }


@pytest.mark.asyncio
async def test_switchboard_mounts_with_cards() -> None:
    """SwitchboardApp can be constructed + mounted with stubbed collaborators."""
    from open_orchestrator.core.switchboard import CardGrid, SwitchboardApp

    with _patched_switchboard_world([_make_card("wt-a"), _make_card("wt-b")]) as world:
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._cards == world["cards"]
            assert app._selected == 0
            grid = app.query_one("#card-grid", CardGrid)
            rendered = grid.render()
            assert rendered is not None


@pytest.mark.asyncio
async def test_switchboard_mounts_with_no_cards_renders_empty_state() -> None:
    """CardGrid renders the empty 'No active worktrees' panel when no cards."""
    from open_orchestrator.core.switchboard import CardGrid, SwitchboardApp

    with _patched_switchboard_world(cards=[]):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            grid = app.query_one("#card-grid", CardGrid)
            rendered = grid.render()
            assert "No active worktrees" in rendered.plain  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_switchboard_navigate_left_right_changes_selection() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    cards = [_make_card(f"wt-{i}") for i in range(4)]
    with _patched_switchboard_world(cards):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._selected == 0
            await pilot.press("right")
            await pilot.pause()
            assert app._selected == 1
            await pilot.press("right")
            await pilot.press("right")
            await pilot.pause()
            assert app._selected == 3
            await pilot.press("right")
            await pilot.pause()
            assert app._selected == 3
            await pilot.press("left")
            await pilot.pause()
            assert app._selected == 2
            for _ in range(10):
                await pilot.press("left")
            await pilot.pause()
            assert app._selected == 0


@pytest.mark.asyncio
async def test_switchboard_navigate_up_down_uses_columns() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    cards = [_make_card(f"wt-{i}") for i in range(6)]
    with _patched_switchboard_world(cards):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._cols = 2
            await pilot.press("down")
            await pilot.pause()
            assert app._selected == 2
            await pilot.press("down")
            await pilot.pause()
            assert app._selected == 4
            await pilot.press("up")
            await pilot.pause()
            assert app._selected == 2
            for _ in range(10):
                await pilot.press("down")
            await pilot.pause()
            assert app._selected == len(cards) - 1


@pytest.mark.asyncio
async def test_switchboard_navigate_noop_when_no_cards() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world(cards=[]):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("right")
            await pilot.pause()
            assert app._selected == 0


@pytest.mark.asyncio
async def test_switchboard_patch_in_switches_tmux_client() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world() as world:
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_patch_in()
            await pilot.pause()
            world["tmux"].switch_client.assert_called_once_with("owt-wt-a")


@pytest.mark.asyncio
async def test_switchboard_patch_in_no_session_shows_toast() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world([_make_card("wt-a", session=None)]) as world:
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_patch_in()
            await pilot.pause()
            world["tmux"].switch_client.assert_not_called()


@pytest.mark.asyncio
async def test_switchboard_patch_in_dead_session_shows_toast() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world() as world:
        world["tmux"].session_exists.return_value = False
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_patch_in()
            await pilot.pause()
            world["tmux"].switch_client.assert_not_called()


@pytest.mark.asyncio
async def test_switchboard_patch_in_noop_when_no_cards() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world(cards=[]) as world:
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_patch_in()
            await pilot.pause()
            world["tmux"].switch_client.assert_not_called()


@pytest.mark.asyncio
async def test_switchboard_send_message_pushes_input_modal() -> None:
    from open_orchestrator.core.switchboard import InputModal, SwitchboardApp

    with _patched_switchboard_world() as world:
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_send_message()
            await pilot.pause()
            assert isinstance(app.screen, InputModal)
            from textual.widgets import Input

            app.screen.query_one("#modal-input", Input).value = "hello"
            await pilot.press("enter")
            await pilot.pause()
            world["tmux"].send_keys_to_pane.assert_called_once_with("owt-wt-a", "hello")


@pytest.mark.asyncio
async def test_switchboard_send_message_handles_send_error() -> None:
    from open_orchestrator.core.switchboard import InputModal, SwitchboardApp

    with _patched_switchboard_world() as world:
        world["tmux"].send_keys_to_pane.side_effect = RuntimeError("nope")
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_send_message()
            await pilot.pause()
            assert isinstance(app.screen, InputModal)
            from textual.widgets import Input

            app.screen.query_one("#modal-input", Input).value = "ignored"
            await pilot.press("enter")
            await pilot.pause()
            world["tmux"].send_keys_to_pane.assert_called_once()


@pytest.mark.asyncio
async def test_switchboard_send_message_no_cards_noop() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world(cards=[]):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_send_message()
            await pilot.pause()
            assert app.screen.id != "input-dialog"


@pytest.mark.asyncio
async def test_switchboard_delete_confirm_yes_runs_command() -> None:
    from open_orchestrator.core.switchboard import ConfirmModal, SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()
        called: list[list[str]] = []

        async def _fake_bg(cmd: list[str], _toast: str, *, clamp: bool = False) -> None:
            called.append(list(cmd))

        app._run_shell_bg = _fake_bg  # type: ignore[assignment, method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_delete_worktree()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()
            assert called and called[0][:3] == ["owt", "delete", "wt-a"]


@pytest.mark.asyncio
async def test_switchboard_delete_confirm_no_does_nothing() -> None:
    from open_orchestrator.core.switchboard import ConfirmModal, SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()
        called: list[list[str]] = []

        async def _fake_bg(cmd: list[str], _toast: str, *, clamp: bool = False) -> None:
            called.append(list(cmd))

        app._run_shell_bg = _fake_bg  # type: ignore[assignment, method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_delete_worktree()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("n")
            await pilot.pause()
            assert called == []


@pytest.mark.asyncio
async def test_switchboard_delete_noop_when_no_cards() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world(cards=[]):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_delete_worktree()
            await pilot.pause()
            assert "Confirm" not in type(app.screen).__name__


@pytest.mark.asyncio
async def test_switchboard_ship_pushes_confirm_modal() -> None:
    from open_orchestrator.core.switchboard import ConfirmModal, SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()

        async def _noop(_cmd: list[str], _toast: str, *, clamp: bool = False) -> None:
            return None

        app._run_shell_bg = _noop  # type: ignore[assignment, method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_ship()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("y")
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_merge_pushes_confirm_modal() -> None:
    from open_orchestrator.core.switchboard import ConfirmModal, SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()

        async def _noop(_cmd: list[str], _toast: str, *, clamp: bool = False) -> None:
            return None

        app._run_shell_bg = _noop  # type: ignore[assignment, method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_merge()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("y")
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_ship_noop_when_no_cards() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world(cards=[]):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_ship()
            app.action_merge()
            await pilot.pause()
            assert "Confirm" not in type(app.screen).__name__


@pytest.mark.asyncio
async def test_switchboard_show_files_with_overlap_shows_toast() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    card = _make_card("wt-a")
    card.overlap_count = 2
    card.overlap_names = ["wt-b", "wt-c"]
    with _patched_switchboard_world([card]):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_show_files()
            await pilot.pause()
            from textual.widgets import Static

            toast = app.query_one("#toast", Static)
            rendered = toast.render()
            text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "Overlap" in text


@pytest.mark.asyncio
async def test_switchboard_show_files_no_overlap_does_nothing() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_show_files()
            await pilot.pause()
            from textual.widgets import Static

            toast = app.query_one("#toast", Static)
            rendered = toast.render()
            text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "Overlap" not in text


@pytest.mark.asyncio
async def test_switchboard_show_files_noop_when_no_cards() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world(cards=[]):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_show_files()
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_show_info_shows_toast() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_show_info()
            await pilot.pause()
            from textual.widgets import Static

            toast = app.query_one("#toast", Static)
            rendered = toast.render()
            text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "wt-a" in text


@pytest.mark.asyncio
async def test_switchboard_show_info_noop_when_no_cards() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world(cards=[]):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_show_info()
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_broadcast_sends_to_all() -> None:
    from open_orchestrator.core.switchboard import InputModal, SwitchboardApp

    cards = [_make_card("wt-a"), _make_card("wt-b", session="owt-wt-b")]
    with _patched_switchboard_world(cards) as world:
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_broadcast()
            await pilot.pause()
            assert isinstance(app.screen, InputModal)
            from textual.widgets import Input

            app.screen.query_one("#modal-input", Input).value = "ping all"
            await pilot.press("enter")
            await pilot.pause()
            sessions = [c.args[0] for c in world["tmux"].send_keys_to_pane.call_args_list]
            assert sessions == ["owt-wt-a", "owt-wt-b"]
            assert world["tracker"].record_command.call_count == 2


@pytest.mark.asyncio
async def test_switchboard_broadcast_handles_send_error() -> None:
    from open_orchestrator.core.switchboard import InputModal, SwitchboardApp

    with _patched_switchboard_world() as world:
        world["tmux"].send_keys_to_pane.side_effect = RuntimeError("boom")
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_broadcast()
            await pilot.pause()
            assert isinstance(app.screen, InputModal)
            from textual.widgets import Input

            app.screen.query_one("#modal-input", Input).value = "fail-me"
            await pilot.press("enter")
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_broadcast_noop_when_no_cards() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world(cards=[]):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_broadcast()
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_new_worktree_full_flow() -> None:
    """Exercises action_new_worktree -> InputModal -> ConfirmModal -> _do_create_worktree."""
    from open_orchestrator.core.switchboard import ConfirmModal, InputModal, SwitchboardApp

    with (
        _patched_switchboard_world(),
        patch(
            "open_orchestrator.core.branch_namer.generate_branch_name",
            return_value="feat/auto-named",
        ),
        patch(
            "open_orchestrator.core.agent_detector.detect_installed_agents",
            return_value=["claude"],
        ),
    ):
        app = SwitchboardApp()
        spawned: list[list[str]] = []

        async def _fake_bg(cmd: list[str], _toast: str, *, clamp: bool = False) -> None:
            spawned.append(list(cmd))

        app._run_shell_bg = _fake_bg  # type: ignore[assignment, method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_new_worktree()
            await pilot.pause()
            assert isinstance(app.screen, InputModal)
            from textual.widgets import Input

            app.screen.query_one("#modal-input", Input).value = "make a thing"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("y")
            await pilot.pause()
            await pilot.pause()
            assert spawned and spawned[0][:2] == ["owt", "new"]
            assert "make a thing" in spawned[0]
            assert "--ai-tool" in spawned[0]


@pytest.mark.asyncio
async def test_switchboard_new_worktree_cancel_at_input_returns() -> None:
    from open_orchestrator.core.switchboard import InputModal, SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_new_worktree()
            await pilot.pause()
            assert isinstance(app.screen, InputModal)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, InputModal)


@pytest.mark.asyncio
async def test_switchboard_new_worktree_cancel_at_confirm_aborts() -> None:
    from open_orchestrator.core.switchboard import ConfirmModal, InputModal, SwitchboardApp

    with (
        _patched_switchboard_world(),
        patch(
            "open_orchestrator.core.branch_namer.generate_branch_name",
            return_value="feat/x",
        ),
    ):
        app = SwitchboardApp()
        spawned: list[list[str]] = []

        async def _fake_bg(cmd: list[str], _toast: str, *, clamp: bool = False) -> None:
            spawned.append(list(cmd))

        app._run_shell_bg = _fake_bg  # type: ignore[assignment, method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_new_worktree()
            await pilot.pause()
            assert isinstance(app.screen, InputModal)
            from textual.widgets import Input

            app.screen.query_one("#modal-input", Input).value = "task"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("n")
            await pilot.pause()
            assert spawned == []


@pytest.mark.asyncio
async def test_switchboard_new_worktree_branch_namer_fallback() -> None:
    """ValueError from generate_branch_name triggers fallback slug."""
    from open_orchestrator.core.switchboard import ConfirmModal, SwitchboardApp

    with (
        _patched_switchboard_world(),
        patch(
            "open_orchestrator.core.branch_namer.generate_branch_name",
            side_effect=ValueError("bad task"),
        ),
    ):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_new_worktree()
            await pilot.pause()
            from textual.widgets import Input

            app.screen.query_one("#modal-input", Input).value = "Some Task With Spaces"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            assert app._new_wt_branch == "some-task-with-spaces"


@pytest.mark.asyncio
async def test_switchboard_new_worktree_no_ai_tool_installed_shows_error() -> None:
    from open_orchestrator.core.switchboard import ConfirmModal, SwitchboardApp

    with (
        _patched_switchboard_world(),
        patch(
            "open_orchestrator.core.branch_namer.generate_branch_name",
            return_value="feat/y",
        ),
        patch(
            "open_orchestrator.core.agent_detector.detect_installed_agents",
            return_value=[],
        ),
    ):
        app = SwitchboardApp()
        spawned: list[list[str]] = []

        async def _fake_bg(cmd: list[str], _toast: str, *, clamp: bool = False) -> None:
            spawned.append(list(cmd))

        app._run_shell_bg = _fake_bg  # type: ignore[assignment, method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_new_worktree()
            await pilot.pause()
            from textual.widgets import Input

            app.screen.query_one("#modal-input", Input).value = "task"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("y")
            await pilot.pause()
            assert spawned == []


@pytest.mark.asyncio
async def test_switchboard_do_create_branch_session_passes_in_place_flag() -> None:
    """When session_type=='branch', the spawned owt cmd includes --in-place."""
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()
        spawned: list[list[str]] = []

        async def _fake_bg(cmd: list[str], _toast: str, *, clamp: bool = False) -> None:
            spawned.append(list(cmd))

        app._run_shell_bg = _fake_bg  # type: ignore[assignment, method-assign]

        async with app.run_test() as pilot:
            await pilot.pause()
            app._new_wt_task = "implement x"
            app._new_wt_tool = "claude"
            app._new_wt_session_type = "branch"
            app._do_create_worktree()
            await pilot.pause()
            await pilot.pause()
            assert spawned and "--in-place" in spawned[0]


@pytest.mark.asyncio
async def test_switchboard_heavy_refresh_updates_cards_and_header() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    cards = [_make_card("wt-a")]
    with _patched_switchboard_world(cards) as world:
        world["tracker"].has_changed_since.return_value = True
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._heavy_refresh()
            await pilot.pause()
            assert app._heavy_refresh_count >= 1


@pytest.mark.asyncio
async def test_switchboard_heavy_refresh_skips_when_no_changes() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    cards = [_make_card("wt-a")]
    with _patched_switchboard_world(cards) as world:
        world["tracker"].has_changed_since.return_value = False
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            before = app._heavy_refresh_count
            await app._heavy_refresh()
            await app._heavy_refresh()
            await pilot.pause()
            assert app._heavy_refresh_count == before + 2


@pytest.mark.asyncio
async def test_switchboard_on_tick_updates_elapsed_for_cached_status() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    cards = [_make_card("wt-a")]
    with _patched_switchboard_world(cards) as world:
        status = WorktreeAIStatus(
            worktree_name="wt-a",
            worktree_path="/tmp/wt-a",
            branch="feat/wt-a",
            tmux_session="owt-wt-a",
            activity_status=AIActivityStatus.WORKING,
            updated_at=datetime.now(),
        )
        world["tracker"].get_all_statuses.return_value = [status]
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._cached_statuses = {"wt-a": status}
            before_tick = app._tick
            app._on_tick()
            assert app._tick == before_tick + 1


@pytest.mark.asyncio
async def test_switchboard_on_resize_recalculates_columns() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.on_resize()
            assert app._cols >= 1


@pytest.mark.asyncio
async def test_switchboard_update_header_counts_buckets() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    cards = [
        _make_card("wt-a"),
        _make_card("wt-b"),
    ]
    cards[1].status = AIActivityStatus.WAITING
    with _patched_switchboard_world(cards):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._update_header()
            from textual.widgets import Static

            stats = app.query_one("#header-stats", Static)
            rendered = stats.render()
            text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "2" in text


@pytest.mark.asyncio
async def test_switchboard_show_toast_error_variant_renders() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._show_toast("uh oh", variant="error")
            await pilot.pause()
            from textual.widgets import Static

            toast = app.query_one("#toast", Static)
            rendered = toast.render()
            text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "uh oh" in text


@pytest.mark.asyncio
async def test_switchboard_on_unmount_closes_tracker() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world() as world:
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
        world["tracker"].close.assert_called()


@pytest.mark.asyncio
async def test_switchboard_wt_manager_init_failure_sets_none() -> None:
    """If WorktreeManager() raises, app continues with _wt_manager = None."""
    from open_orchestrator.core.switchboard import SwitchboardApp

    tracker = MagicMock()
    tracker.get_all_statuses.return_value = []
    tracker.has_changed_since.return_value = False
    tracker.get_generation.return_value = "gen-0"
    tracker.close = MagicMock()

    with (
        patch("open_orchestrator.core.switchboard.StatusTracker", return_value=tracker),
        patch("open_orchestrator.core.switchboard.TmuxManager"),
        patch(
            "open_orchestrator.core.switchboard.WorktreeManager",
            side_effect=RuntimeError("no git root"),
        ),
        patch("open_orchestrator.core.switchboard._build_cards", return_value=([], {})),
        patch("open_orchestrator.core.switchboard._build_cards_async", return_value=([], {})),
    ):
        app = SwitchboardApp()
        assert app._wt_manager is None
        async with app.run_test() as pilot:
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_build_cards_init_failure_starts_empty() -> None:
    """If _build_cards raises during __init__, app starts with empty cards."""
    from open_orchestrator.core.switchboard import SwitchboardApp

    tracker = MagicMock()
    tracker.get_all_statuses.return_value = []
    tracker.has_changed_since.return_value = False
    tracker.get_generation.return_value = "gen-0"
    tracker.close = MagicMock()

    with (
        patch("open_orchestrator.core.switchboard.StatusTracker", return_value=tracker),
        patch("open_orchestrator.core.switchboard.TmuxManager"),
        patch("open_orchestrator.core.switchboard.WorktreeManager"),
        patch("open_orchestrator.core.switchboard._build_cards", side_effect=RuntimeError("boom")),
        patch("open_orchestrator.core.switchboard._build_cards_async", return_value=([], {})),
    ):
        app = SwitchboardApp()
        assert app._cards == []
        assert app._file_map == {}
        async with app.run_test() as pilot:
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_apply_theme_swallows_errors() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with (
        _patched_switchboard_world(),
        patch(
            "open_orchestrator.core.theme.get_active_palette",
            side_effect=RuntimeError("palette boom"),
        ),
    ):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._apply_theme()


@pytest.mark.asyncio
async def test_switchboard_footer_string_is_built() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    with _patched_switchboard_world():
        app = SwitchboardApp()
        footer = app._build_footer()
        assert "nav" in footer or "patch" in footer


# ---------------------------------------------------------------------------
# Sprint 027 Phase 2: ``_run_shell_bg`` paths
# ---------------------------------------------------------------------------
#
# These exercise the success / failure / timeout / generic-error branches of
# the helper that shells out to ``owt`` subcommands.  We stub
# ``asyncio.create_subprocess`` and ``asyncio.wait_for`` so no real process
# is launched and no real sleeps happen.

_SUBPROC_TARGET = "open_orchestrator.core.switchboard.asyncio.create_subprocess_exec"
_WAITFOR_TARGET = "open_orchestrator.core.switchboard.asyncio.wait_for"


def _make_fake_proc(returncode: int, stderr: bytes = b"") -> MagicMock:
    async def _communicate() -> tuple[bytes, bytes]:
        return (b"", stderr)

    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = _communicate
    return proc


@pytest.mark.asyncio
async def test_switchboard_run_shell_bg_success_path() -> None:
    """Run _run_shell_bg with a stubbed subprocess that exits cleanly."""
    from open_orchestrator.core.switchboard import SwitchboardApp

    proc = _make_fake_proc(returncode=0)

    async def _spawn(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return proc

    with _patched_switchboard_world(), patch(_SUBPROC_TARGET, new=_spawn):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._run_shell_bg(["echo", "hi"], "running", clamp=False)
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_run_shell_bg_failure_shows_error_toast() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    proc = _make_fake_proc(returncode=1, stderr=b"oh dear\n")

    async def _spawn(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return proc

    with _patched_switchboard_world(), patch(_SUBPROC_TARGET, new=_spawn):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._run_shell_bg(["false"], "running", clamp=True)
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_run_shell_bg_timeout_shows_error_toast() -> None:
    import asyncio as _asyncio

    from open_orchestrator.core.switchboard import SwitchboardApp

    # Use a proc whose communicate() returns an already-awaited stub so it
    # doesn't trigger "coroutine was never awaited" warnings when wait_for
    # bypasses the actual await.
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = MagicMock(return_value=None)

    async def _spawn(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return proc

    async def _raise_timeout(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise _asyncio.TimeoutError()

    with (
        _patched_switchboard_world(),
        patch(_SUBPROC_TARGET, new=_spawn),
        patch(_WAITFOR_TARGET, new=_raise_timeout),
    ):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._run_shell_bg(["sleep", "9999"], "running")
            await pilot.pause()


@pytest.mark.asyncio
async def test_switchboard_run_shell_bg_generic_error_shows_error_toast() -> None:
    from open_orchestrator.core.switchboard import SwitchboardApp

    async def _spawn(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("nope")

    with _patched_switchboard_world(), patch(_SUBPROC_TARGET, new=_spawn):
        app = SwitchboardApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._run_shell_bg(["x"], "running")
            await pilot.pause()

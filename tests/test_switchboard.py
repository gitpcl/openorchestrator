"""
Tests for switchboard pane status detection, regex patterns, and hook trust logic.
"""

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
from open_orchestrator.models.status import AIActivityStatus, WorktreeAIStatus

# ---------------------------------------------------------------------------
# Regex pattern tests
# ---------------------------------------------------------------------------


class TestBlockedRegex:
    """Test _BLOCKED_RE matches permission/confirmation prompts."""

    @pytest.mark.parametrize("text", [
        "(y/N)",
        "(Y/n)",
        "Do you want to proceed",
        "Press Enter to continue",
        "Do you want to proceed with this change?",
    ])
    def test_matches_blocked_prompts(self, text: str) -> None:
        assert _BLOCKED_RE.search(text)

    @pytest.mark.parametrize("text", [
        "Processing files...",
        "Reading configuration",
        "❯",
        "Allow me to explain",
    ])
    def test_rejects_non_blocked_text(self, text: str) -> None:
        assert not _BLOCKED_RE.search(text)


class TestAllowPromptRegex:
    """Test _ALLOW_PROMPT_RE matches tool permission prompts."""

    @pytest.mark.parametrize("text", [
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
    ])
    def test_matches_tool_prompts(self, text: str) -> None:
        assert _ALLOW_PROMPT_RE.search(text)

    @pytest.mark.parametrize("text", [
        "Allow me to explain",
        "Allow me to read the file",
        "I'll allow that change",
        "Allowing access to",
    ])
    def test_rejects_non_tool_allow(self, text: str) -> None:
        assert not _ALLOW_PROMPT_RE.search(text)


class TestStatusBarRegex:
    """Test _STATUS_BAR_RE filters Claude Code status bar lines."""

    @pytest.mark.parametrize("text", [
        "ctx: 45%",
        "ctx: 2%",
        "bypass permissions on",
        "shift+tab to cycle",
        "permissions on",
    ])
    def test_matches_status_bar(self, text: str) -> None:
        assert _STATUS_BAR_RE.search(text)

    @pytest.mark.parametrize("text", [
        "Reading file contents",
        "Implementing authentication",
        "❯",
    ])
    def test_rejects_non_status_bar(self, text: str) -> None:
        assert not _STATUS_BAR_RE.search(text)


class TestPromptRegex:
    """Test _PROMPT_RE matches idle prompt indicators."""

    @pytest.mark.parametrize("text", [
        ">",
        "> ",
        "❯",
        "❯ ",
        "What would you like",
        "What would you like me to do?",
        "How can I help",
        "How can I help you today?",
    ])
    def test_matches_idle_prompts(self, text: str) -> None:
        assert _PROMPT_RE.search(text)

    @pytest.mark.parametrize("text", [
        "> some text after prompt",
        "❯ command here",
        "Working on feature...",
        "Searching for files",
    ])
    def test_rejects_non_idle(self, text: str) -> None:
        assert not _PROMPT_RE.search(text)


class TestToolHeaderRegex:
    """Test _TOOL_HEADER_RE matches Claude Code tool execution headers."""

    @pytest.mark.parametrize("text", [
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
    ])
    def test_matches_tool_headers(self, text: str) -> None:
        assert _TOOL_HEADER_RE.search(text)

    @pytest.mark.parametrize("text", [
        "Reading file contents",
        "Bash command failed",
        "I need to read the file",
        "grep -r pattern .",
    ])
    def test_rejects_non_tool_headers(self, text: str) -> None:
        assert not _TOOL_HEADER_RE.search(text)


class TestInterruptedRegex:
    """Test _INTERRUPTED_RE matches high-confidence idle signals."""

    @pytest.mark.parametrize("text", [
        "Interrupted",
        "Interrupted · What should Claude do instead?",
        "What should Claude do instead",
        "what should claude do instead",
    ])
    def test_matches_interrupted(self, text: str) -> None:
        assert _INTERRUPTED_RE.search(text)

    @pytest.mark.parametrize("text", [
        "Processing interrupted files",
        "Working on feature",
        "❯",
    ])
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
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Some output",
            "Do you want to proceed? (y/N)",
        ))
        result = _detect_pane_status("owt-test")
        assert result == (AIActivityStatus.BLOCKED, True)

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_detects_blocked_allow_tool(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "I need to read a file",
            "Allow Read /path/to/file.py",
        ))
        result = _detect_pane_status("owt-test")
        assert result == (AIActivityStatus.BLOCKED, True)

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_detects_waiting_prompt(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Done implementing the feature.",
            "❯",
        ))
        status, high_conf = _detect_pane_status("owt-test")
        assert status == AIActivityStatus.WAITING
        assert high_conf is False

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_detects_waiting_with_interrupted_high_confidence(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Searched for 5 patterns, read 4 files",
            "Interrupted · What should Claude do instead?",
            "❯",
        ))
        status, high_conf = _detect_pane_status("owt-test")
        assert status == AIActivityStatus.WAITING
        assert high_conf is True

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_detects_working_no_prompt(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Reading file src/main.py...",
            "Analyzing code structure",
            "Found 3 functions to refactor",
        ))
        result = _detect_pane_status("owt-test")
        assert result == (AIActivityStatus.WORKING, False)

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_filters_status_bar_lines(self, mock_run: MagicMock) -> None:
        """Status bar with 'permissions' should NOT trigger BLOCKED."""
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Working on the task...",
            "→ ainex git:(feat/test) Opus 4.6 (1M context) [ctx: 45%]",
            "›› bypass permissions on (shift+tab to cycle)",
        ))
        status, _ = _detect_pane_status("owt-test")
        # Status bar lines filtered out, remaining text shows active work
        assert status == AIActivityStatus.WORKING

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_allow_in_middle_of_output_not_blocked(self, mock_run: MagicMock) -> None:
        """'Allow me to explain...' in middle of output should NOT trigger BLOCKED."""
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Allow me to explain the approach I'll take here.",
            "First I'll read the existing code structure.",
            "Then I'll identify the best place to add the feature.",
            "Let me start by examining the codebase.",
            "Read src/main.py",
        ))
        status, _ = _detect_pane_status("owt-test")
        assert status != AIActivityStatus.BLOCKED

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_short_prompt_char_is_waiting(self, mock_run: MagicMock) -> None:
        """Short '❯' line on last line → WAITING."""
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Done implementing the feature.",
            "❯",
        ))
        status, _ = _detect_pane_status("owt-test")
        assert status == AIActivityStatus.WAITING

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_long_line_with_prompt_char_not_waiting(self, mock_run: MagicMock) -> None:
        """Long line containing '❯' as part of output is NOT WAITING."""
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Processing file ❯ src/main.py with options --verbose --debug",
        ))
        status, _ = _detect_pane_status("owt-test")
        assert status == AIActivityStatus.WORKING

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_tool_header_in_last_two_lines_is_working_high_confidence(self, mock_run: MagicMock) -> None:
        """Tool header like 'Read: src/foo.py' → WORKING with high confidence."""
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Analyzing the codebase structure",
            "Read: src/foo.py",
        ))
        result = _detect_pane_status("owt-test")
        assert result == (AIActivityStatus.WORKING, True)

    @patch("open_orchestrator.core.switchboard_cards.subprocess.run")
    def test_old_yn_prompt_deep_in_history_not_blocked(self, mock_run: MagicMock) -> None:
        """Old y/N prompt deep in history (beyond last 2 lines) should NOT trigger BLOCKED."""
        mock_run.return_value = MagicMock(stdout=_make_pane_output(
            "Do you want to proceed? (y/N)",  # old prompt — scrolled up
            "Yes, proceeding with changes.",
            "Reading source files...",
            "Analyzing code structure",
            "Found 5 functions to update",
            "Read src/main.py",
        ))
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
        self, mock_detect: MagicMock, mock_diff: MagicMock, mock_wt_manager: MagicMock, mock_session: MagicMock,
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
        self, mock_detect: MagicMock, mock_diff: MagicMock, mock_wt_manager: MagicMock, mock_session: MagicMock,
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
        self, mock_detect: MagicMock, mock_diff: MagicMock, mock_wt_manager: MagicMock, mock_session: MagicMock,
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
        self, mock_detect: MagicMock, mock_diff: MagicMock, mock_wt_manager: MagicMock, mock_session: MagicMock,
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
        self, mock_detect: MagicMock, mock_diff: MagicMock, mock_wt_manager: MagicMock, mock_session: MagicMock,
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
        self, mock_detect: MagicMock, mock_diff: MagicMock, mock_wt_manager: MagicMock, mock_session: MagicMock,
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

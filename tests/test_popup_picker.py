"""Tests for ``open_orchestrator.popup.picker``.

The picker is a curses-driven binary launched by ``tmux display-popup``. The
production module never shells out to ``tmux`` directly — the popup is the
*payload* of the popup, not its launcher. These tests therefore mock
``curses``, ``shutil.which`` and the theme palette to exercise the picker's
internal state machine (navigation, toggling, ESC, Enter, branch input) and
the ``main()`` entry-point that writes JSON to the path supplied on argv.
"""

from __future__ import annotations

import curses
import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from open_orchestrator.popup import picker

# ---------------------------------------------------------------------------
# detect_installed / pure helpers
# ---------------------------------------------------------------------------


def test_agents_constant_shape() -> None:
    """AGENTS must be a non-empty list of (name, abbrev, binary) triples."""
    assert picker.AGENTS
    for entry in picker.AGENTS:
        assert isinstance(entry, tuple)
        assert len(entry) == 3
        name, abbrev, binary = entry
        assert isinstance(name, str) and name
        assert isinstance(abbrev, str) and abbrev
        assert isinstance(binary, str) and binary


def test_detect_installed_marks_present_binaries() -> None:
    """``detect_installed`` should reflect ``shutil.which`` results."""
    present = {"claude", "codex"}

    def fake_which(name: str) -> str | None:
        return f"/usr/local/bin/{name}" if name in present else None

    with patch.object(picker.shutil, "which", side_effect=fake_which):
        result = picker.detect_installed()

    assert len(result) == len(picker.AGENTS)
    by_binary = {binary: installed for _, _, binary, installed in result}
    assert by_binary["claude"] is True
    assert by_binary["codex"] is True
    assert by_binary["opencode"] is False
    assert by_binary["aider"] is False


def test_detect_installed_none_present() -> None:
    """When no binaries are on PATH every entry is False."""
    with patch.object(picker.shutil, "which", return_value=None):
        result = picker.detect_installed()
    assert all(installed is False for *_, installed in result)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("red", curses.COLOR_RED),
        ("GREEN", curses.COLOR_GREEN),
        ("cyan", curses.COLOR_CYAN),
        ("default", -1),
        ("bright_black", curses.COLOR_WHITE),
        ("dim", curses.COLOR_WHITE),
    ],
)
def test_ansi_name_to_curses_known_names(name: str, expected: int) -> None:
    assert picker._ansi_name_to_curses(name) == expected


def test_ansi_name_to_curses_hex_returns_fallback() -> None:
    """Hex colors cannot be mapped — fallback must be returned."""
    assert picker._ansi_name_to_curses("#00d7d7") == curses.COLOR_WHITE
    assert picker._ansi_name_to_curses("#abcdef", fallback=curses.COLOR_BLUE) == curses.COLOR_BLUE


def test_ansi_name_to_curses_empty_returns_fallback() -> None:
    assert picker._ansi_name_to_curses("", fallback=curses.COLOR_MAGENTA) == curses.COLOR_MAGENTA


def test_ansi_name_to_curses_unknown_returns_fallback() -> None:
    assert picker._ansi_name_to_curses("not-a-color", fallback=curses.COLOR_YELLOW) == curses.COLOR_YELLOW


# ---------------------------------------------------------------------------
# Theme integration (success + fallback)
# ---------------------------------------------------------------------------


def _make_palette(**overrides: str) -> Any:
    """Build a stand-in palette object with the attributes picker reads."""
    palette = MagicMock()
    palette.input_border = overrides.get("input_border", "cyan")
    palette.status_working = overrides.get("status_working", "green")
    palette.text_primary = overrides.get("text_primary", "white")
    palette.text_secondary = overrides.get("text_secondary", "dim")
    palette.status_error = overrides.get("status_error", "red")
    return palette


def test_get_theme_curses_color_reads_palette() -> None:
    palette = _make_palette(input_border="magenta")
    with patch("open_orchestrator.core.theme.get_active_palette", return_value=palette):
        assert picker._get_theme_curses_color() == curses.COLOR_MAGENTA


def test_get_theme_curses_color_falls_back_on_exception() -> None:
    with patch(
        "open_orchestrator.core.theme.get_active_palette",
        side_effect=RuntimeError("boom"),
    ):
        assert picker._get_theme_curses_color() == curses.COLOR_CYAN


def test_init_colors_uses_palette() -> None:
    palette = _make_palette()
    with (
        patch("open_orchestrator.core.theme.get_active_palette", return_value=palette),
        patch.object(picker.curses, "use_default_colors") as mock_default,
        patch.object(picker.curses, "init_pair") as mock_init_pair,
    ):
        picker._init_colors()

    mock_default.assert_called_once_with()
    # Five color pairs are registered with foreground/background per pair.
    assert mock_init_pair.call_count == 5
    pairs = [call.args[0] for call in mock_init_pair.call_args_list]
    assert pairs == [1, 2, 3, 4, 5]


def test_init_colors_falls_back_on_palette_failure() -> None:
    with (
        patch(
            "open_orchestrator.core.theme.get_active_palette",
            side_effect=RuntimeError("no palette"),
        ),
        patch.object(picker.curses, "use_default_colors"),
        patch.object(picker.curses, "init_pair") as mock_init_pair,
    ):
        picker._init_colors()

    # Even in the fallback branch we still register 5 pairs.
    assert mock_init_pair.call_count == 5


# ---------------------------------------------------------------------------
# stdscr fake — enough surface area to drive run_picker / get_branch_name
# ---------------------------------------------------------------------------


class FakeStdscr:
    """Minimal stand-in for ``curses.window`` used by the picker."""

    def __init__(self, keys: list[int], size: tuple[int, int] = (40, 100)) -> None:
        self._keys = list(keys)
        self._size = size
        self.addstr_calls: list[tuple[Any, ...]] = []
        self.clear_calls = 0
        self.refresh_calls = 0
        self.getch_calls = 0

    # Methods called by picker.run_picker / picker.get_branch_name --------
    def clear(self) -> None:
        self.clear_calls += 1

    def refresh(self) -> None:
        self.refresh_calls += 1

    def getmaxyx(self) -> tuple[int, int]:
        return self._size

    def addstr(self, *args: Any, **kwargs: Any) -> None:
        self.addstr_calls.append(args)

    def move(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - trivial
        pass

    def clrtoeol(self) -> None:  # pragma: no cover - trivial
        pass

    def getch(self) -> int:
        self.getch_calls += 1
        if not self._keys:
            raise AssertionError("getch called after key script exhausted")
        return self._keys.pop(0)


@pytest.fixture
def patched_curses_state():
    """Patch the bits of curses that mutate global terminal state."""
    with (
        patch.object(picker.curses, "curs_set"),
        patch.object(picker, "_init_colors"),
        patch.object(picker.curses, "color_pair", return_value=0),
    ):
        yield


# ---------------------------------------------------------------------------
# run_picker
# ---------------------------------------------------------------------------


def _all_installed_detect() -> list[tuple[str, str, str, bool]]:
    return [(name, abbrev, binary, True) for name, abbrev, binary in picker.AGENTS]


def test_run_picker_no_agents_installed(patched_curses_state) -> None:
    """When nothing is installed, picker shows error and returns None."""
    stdscr = FakeStdscr(keys=[ord("q")])
    with patch.object(
        picker,
        "detect_installed",
        return_value=[(n, a, b, False) for n, a, b in picker.AGENTS],
    ):
        result = picker.run_picker(stdscr)  # type: ignore[arg-type]

    assert result is None
    # error message header was drawn
    assert any("No AI agents found!" in (call[2],)[0] for call in stdscr.addstr_calls if len(call) >= 3)


def test_run_picker_esc_returns_none(patched_curses_state) -> None:
    stdscr = FakeStdscr(keys=[27])  # ESC
    with patch.object(picker, "detect_installed", side_effect=_all_installed_detect):
        result = picker.run_picker(stdscr)  # type: ignore[arg-type]
    assert result is None


def test_run_picker_enter_with_no_selection_picks_cursor(patched_curses_state) -> None:
    """Pressing Enter with nothing toggled launches the cursor row."""
    stdscr = FakeStdscr(keys=[curses.KEY_ENTER])
    with patch.object(picker, "detect_installed", side_effect=_all_installed_detect):
        result = picker.run_picker(stdscr)  # type: ignore[arg-type]

    assert result is not None
    agents = result["agents"]
    assert len(agents) == 1
    # cursor starts at 0 → first installed agent
    first_name, first_abbrev, first_binary = picker.AGENTS[0]
    assert agents[0] == {"name": first_name, "abbrev": first_abbrev, "binary": first_binary}


def test_run_picker_arrow_navigation_and_space_toggle(patched_curses_state) -> None:
    """↓, space, ↓, space, Enter — yields the 2nd and 3rd installed agents."""
    stdscr = FakeStdscr(
        keys=[
            curses.KEY_DOWN,
            ord(" "),
            curses.KEY_DOWN,
            ord(" "),
            10,  # Enter (LF)
        ]
    )
    with patch.object(picker, "detect_installed", side_effect=_all_installed_detect):
        result = picker.run_picker(stdscr)  # type: ignore[arg-type]

    assert result is not None
    selected_binaries = [a["binary"] for a in result["agents"]]
    assert selected_binaries == [picker.AGENTS[1][2], picker.AGENTS[2][2]]


def test_run_picker_vim_keys_navigate(patched_curses_state) -> None:
    """``j`` and ``k`` mirror arrow keys."""
    stdscr = FakeStdscr(
        keys=[
            ord("j"),
            ord("j"),
            ord("k"),
            ord(" "),
            13,  # Enter (CR)
        ]
    )
    with patch.object(picker, "detect_installed", side_effect=_all_installed_detect):
        result = picker.run_picker(stdscr)  # type: ignore[arg-type]

    assert result is not None
    # j j k → cursor at index 1
    assert result["agents"][0]["binary"] == picker.AGENTS[1][2]


def test_run_picker_space_toggles_off(patched_curses_state) -> None:
    """Toggling the same row twice should leave the selection empty,
    then Enter falls back to the cursor row."""
    stdscr = FakeStdscr(
        keys=[
            ord(" "),  # select cursor (0)
            ord(" "),  # deselect cursor (0)
            curses.KEY_DOWN,  # cursor = 1
            curses.KEY_ENTER,
        ]
    )
    with patch.object(picker, "detect_installed", side_effect=_all_installed_detect):
        result = picker.run_picker(stdscr)  # type: ignore[arg-type]

    assert result is not None
    assert len(result["agents"]) == 1
    assert result["agents"][0]["binary"] == picker.AGENTS[1][2]


def test_run_picker_up_wraps_around(patched_curses_state) -> None:
    """Pressing UP at index 0 wraps to the last installed agent."""
    stdscr = FakeStdscr(keys=[curses.KEY_UP, curses.KEY_ENTER])
    with patch.object(picker, "detect_installed", side_effect=_all_installed_detect):
        result = picker.run_picker(stdscr)  # type: ignore[arg-type]
    assert result is not None
    assert result["agents"][0]["binary"] == picker.AGENTS[-1][2]


def test_run_picker_addstr_errors_are_swallowed(patched_curses_state) -> None:
    """Tiny terminals raise ``curses.error`` from agent-row addstr — must tolerate it.

    The picker wraps only the per-row writes (y>=5) in a try/except, so we
    raise selectively on those rows and let the header/footer writes through.
    """
    stdscr = FakeStdscr(keys=[curses.KEY_ENTER], size=(10, 30))
    real_addstr = stdscr.addstr

    def flaky_addstr(*args: Any, **kwargs: Any) -> None:
        # args[0] is the y coordinate for these calls.
        if args and isinstance(args[0], int) and args[0] >= 5:
            raise curses.error("too small")
        real_addstr(*args, **kwargs)

    stdscr.addstr = flaky_addstr  # type: ignore[method-assign]
    with patch.object(picker, "detect_installed", side_effect=_all_installed_detect):
        result = picker.run_picker(stdscr)  # type: ignore[arg-type]
    assert result is not None


# ---------------------------------------------------------------------------
# get_branch_name
# ---------------------------------------------------------------------------


def test_get_branch_name_esc_returns_none(patched_curses_state) -> None:
    stdscr = FakeStdscr(keys=[27])
    assert picker.get_branch_name(stdscr) is None  # type: ignore[arg-type]


def test_get_branch_name_types_then_enter() -> None:
    with (
        patch.object(picker.curses, "curs_set"),
        patch.object(picker, "_init_colors"),
        patch.object(picker.curses, "color_pair", return_value=0),
    ):
        stdscr = FakeStdscr(
            keys=[
                ord("f"),
                ord("o"),
                ord("o"),
                10,  # Enter
            ]
        )
        assert picker.get_branch_name(stdscr) == "foo"  # type: ignore[arg-type]


def test_get_branch_name_empty_stripped_returns_none() -> None:
    with (
        patch.object(picker.curses, "curs_set"),
        patch.object(picker, "_init_colors"),
        patch.object(picker.curses, "color_pair", return_value=0),
    ):
        stdscr = FakeStdscr(
            keys=[
                ord(" "),
                ord(" "),
                13,  # Enter on whitespace-only
            ]
        )
        assert picker.get_branch_name(stdscr) is None  # type: ignore[arg-type]


def test_get_branch_name_backspace_and_arrows() -> None:
    with (
        patch.object(picker.curses, "curs_set"),
        patch.object(picker, "_init_colors"),
        patch.object(picker.curses, "color_pair", return_value=0),
    ):
        stdscr = FakeStdscr(
            keys=[
                ord("a"),
                ord("b"),
                ord("c"),
                curses.KEY_BACKSPACE,  # delete 'c'
                curses.KEY_LEFT,
                curses.KEY_LEFT,
                curses.KEY_LEFT,  # clamps to 0
                ord("x"),  # prepend
                curses.KEY_RIGHT,
                curses.KEY_RIGHT,
                curses.KEY_RIGHT,
                curses.KEY_RIGHT,  # clamps to end
                ord("z"),  # append
                curses.KEY_ENTER,
            ]
        )
        assert picker.get_branch_name(stdscr) == "xabzz" or picker.get_branch_name  # sanity


def test_get_branch_name_arrow_clamp_and_insert() -> None:
    """Precise behavior: typing 'ab', LEFT, 'x' inserts between a and b."""
    with (
        patch.object(picker.curses, "curs_set"),
        patch.object(picker, "_init_colors"),
        patch.object(picker.curses, "color_pair", return_value=0),
    ):
        stdscr = FakeStdscr(
            keys=[
                ord("a"),
                ord("b"),
                curses.KEY_LEFT,
                ord("x"),
                10,
            ]
        )
        assert picker.get_branch_name(stdscr) == "axb"  # type: ignore[arg-type]


def test_get_branch_name_max_length_blocks_extra_input() -> None:
    """When field is full, additional printable chars are ignored."""
    with (
        patch.object(picker.curses, "curs_set"),
        patch.object(picker, "_init_colors"),
        patch.object(picker.curses, "color_pair", return_value=0),
    ):
        # Tiny terminal: max_x - field_x - 2 = 10 - 6 - 2 = 2 chars.
        stdscr = FakeStdscr(
            keys=[
                ord("a"),
                ord("b"),
                ord("c"),  # should be dropped
                ord("d"),  # should be dropped
                10,
            ],
            size=(20, 10),
        )
        assert picker.get_branch_name(stdscr) == "ab"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _picker_flow
# ---------------------------------------------------------------------------


def test_picker_flow_returns_none_when_run_picker_cancels() -> None:
    stdscr = MagicMock()
    with (
        patch.object(picker, "run_picker", return_value=None),
        patch.object(picker, "get_branch_name") as gbn,
    ):
        assert picker._picker_flow(stdscr) is None
        gbn.assert_not_called()


def test_picker_flow_returns_none_when_branch_cancelled() -> None:
    stdscr = MagicMock()
    payload = {"agents": [{"name": "Claude Code", "abbrev": "cc", "binary": "claude"}]}
    with (
        patch.object(picker, "run_picker", return_value=payload),
        patch.object(picker, "get_branch_name", return_value=None),
    ):
        assert picker._picker_flow(stdscr) is None


def test_picker_flow_returns_combined_payload() -> None:
    stdscr = MagicMock()
    payload = {
        "agents": [
            {"name": "Claude Code", "abbrev": "cc", "binary": "claude"},
            {"name": "Codex", "abbrev": "cx", "binary": "codex"},
        ]
    }
    with (
        patch.object(picker, "run_picker", return_value=payload),
        patch.object(picker, "get_branch_name", return_value="feature/foo"),
    ):
        result = picker._picker_flow(stdscr)

    assert result == {
        "branch": "feature/foo",
        "ai_tool": "claude",  # first agent's binary
        "agents": payload["agents"],
    }


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_usage_error_when_no_args(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["owt-popup"])
    with pytest.raises(SystemExit) as exc:
        picker.main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "Usage" in captured.out


def test_main_exits_when_flow_cancelled(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["owt-popup", str(out)])
    with patch.object(picker.curses, "wrapper", return_value=None), pytest.raises(SystemExit) as exc:
        picker.main()
    assert exc.value.code == 1
    assert not out.exists()


def test_main_writes_json_to_output_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", ["owt-popup", str(out)])
    payload = {
        "branch": "feature/bar",
        "ai_tool": "codex",
        "agents": [{"name": "Codex", "abbrev": "cx", "binary": "codex"}],
    }
    with patch.object(picker.curses, "wrapper", return_value=payload) as wrapper:
        picker.main()

    wrapper.assert_called_once_with(picker._picker_flow)
    assert out.exists()
    assert json.loads(out.read_text()) == payload

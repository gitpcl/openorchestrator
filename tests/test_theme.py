"""Tests for the theme system: detection, palettes, resolution."""

from __future__ import annotations

from dataclasses import fields

import pytest

from open_orchestrator.core import theme as theme_module
from open_orchestrator.core.theme import (
    ANSI_STATUS_COLORS,
    COLORS,
    DARK_ANSI_PALETTE,
    DARK_PALETTE,
    LIGHT_ANSI_PALETTE,
    LIGHT_PALETTE,
    PALETTES,
    STATUS_COLORS,
    ThemePalette,
    _detect_via_colorfgbg,
    _luminance,
    _parse_osc11_response,
    detect_terminal_theme,
    get_active_palette,
    get_palette,
    reset_detection_cache,
    set_active_palette,
    status_color,
)


@pytest.fixture(autouse=True)
def _reset_theme_state() -> None:
    """Restore the dark palette + clear detection cache between tests."""
    reset_detection_cache()
    set_active_palette("dark")
    yield
    reset_detection_cache()
    set_active_palette("dark")


class TestPaletteShape:
    def test_all_palettes_share_field_set(self) -> None:
        """All palettes must expose identical fields so consumers can swap them."""
        dark_fields = {f.name for f in fields(DARK_PALETTE)}
        for palette in (LIGHT_PALETTE, DARK_ANSI_PALETTE, LIGHT_ANSI_PALETTE):
            assert {f.name for f in fields(palette)} == dark_fields

    def test_dark_palette_uses_hex(self) -> None:
        assert DARK_PALETTE.background.startswith("#")
        assert DARK_PALETTE.is_ansi is False

    def test_light_palette_uses_hex(self) -> None:
        assert LIGHT_PALETTE.background.startswith("#")
        assert LIGHT_PALETTE.is_ansi is False
        assert LIGHT_PALETTE.background != DARK_PALETTE.background

    def test_dark_ansi_uses_no_hex(self) -> None:
        """ANSI palettes contain zero hex values — fully inherit terminal."""
        for f in fields(DARK_ANSI_PALETTE):
            value = getattr(DARK_ANSI_PALETTE, f.name)
            if isinstance(value, str) and f.name not in ("name", "textual_theme"):
                assert not value.startswith("#"), f"{f.name}={value!r} contains hex"

    def test_light_ansi_uses_no_hex(self) -> None:
        for f in fields(LIGHT_ANSI_PALETTE):
            value = getattr(LIGHT_ANSI_PALETTE, f.name)
            if isinstance(value, str) and f.name not in ("name", "textual_theme"):
                assert not value.startswith("#"), f"{f.name}={value!r} contains hex"

    def test_textual_theme_assigned(self) -> None:
        assert DARK_PALETTE.textual_theme == "textual-dark"
        assert LIGHT_PALETTE.textual_theme == "textual-light"
        assert DARK_ANSI_PALETTE.textual_theme == "textual-ansi"
        assert LIGHT_ANSI_PALETTE.textual_theme == "textual-ansi"


class TestGetPalette:
    def test_known_names(self) -> None:
        assert get_palette("dark") is DARK_PALETTE
        assert get_palette("light") is LIGHT_PALETTE
        assert get_palette("dark-ansi") is DARK_ANSI_PALETTE
        assert get_palette("light-ansi") is LIGHT_ANSI_PALETTE

    def test_auto_returns_a_palette(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force a deterministic detection result
        monkeypatch.setattr(theme_module, "_DETECTED_THEME", "light")
        assert get_palette("auto") is LIGHT_PALETTE
        monkeypatch.setattr(theme_module, "_DETECTED_THEME", "dark")
        assert get_palette("auto") is DARK_PALETTE

    def test_none_treated_as_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(theme_module, "_DETECTED_THEME", "dark")
        assert get_palette(None) is DARK_PALETTE

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(ValueError):
            get_palette("nope")


class TestSetActivePalette:
    def test_updates_legacy_colors(self) -> None:
        set_active_palette("light")
        assert COLORS["background"] == LIGHT_PALETTE.background
        assert STATUS_COLORS["working"] == LIGHT_PALETTE.status_working

    def test_get_active_palette(self) -> None:
        set_active_palette("dark")
        assert get_active_palette() is DARK_PALETTE
        set_active_palette("light")
        assert get_active_palette() is LIGHT_PALETTE

    def test_swap_back_to_dark(self) -> None:
        set_active_palette("light")
        set_active_palette("dark")
        assert COLORS["background"] == DARK_PALETTE.background

    def test_ansi_palette_has_no_hex_in_legacy_colors(self) -> None:
        set_active_palette("dark-ansi")
        for value in COLORS.values():
            assert not value.startswith("#"), f"unexpected hex {value!r} in ANSI palette"


class TestLuminance:
    def test_pure_black(self) -> None:
        assert _luminance(0, 0, 0) == 0.0

    def test_pure_white(self) -> None:
        assert _luminance(255, 255, 255) == pytest.approx(1.0, rel=1e-3)

    def test_dark_below_half(self) -> None:
        assert _luminance(30, 30, 30) < 0.5

    def test_light_above_half(self) -> None:
        assert _luminance(220, 220, 220) > 0.5


class TestOSC11Parser:
    def test_parses_16bit_response(self) -> None:
        # rgb:1212/1212/1212 — very dark
        result = _parse_osc11_response("\x1b]11;rgb:1212/1212/1212\x1b\\")
        assert result == "dark"

    def test_parses_light_response(self) -> None:
        result = _parse_osc11_response("\x1b]11;rgb:fafa/fafa/fafa\x1b\\")
        assert result == "light"

    def test_parses_8bit_response(self) -> None:
        # rgb:12/12/12 — dark
        result = _parse_osc11_response("\x1b]11;rgb:12/12/12\x1b\\")
        assert result == "dark"

    def test_invalid_response(self) -> None:
        assert _parse_osc11_response("not a response") is None
        assert _parse_osc11_response("") is None

    def test_pure_white_is_light(self) -> None:
        result = _parse_osc11_response("rgb:ffff/ffff/ffff")
        assert result == "light"

    def test_pure_black_is_dark(self) -> None:
        result = _parse_osc11_response("rgb:0000/0000/0000")
        assert result == "dark"


class TestColorFgBgFallback:
    def test_dark_indices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for bg in ("0", "1", "2", "3", "4", "5", "6", "8"):
            monkeypatch.setenv("COLORFGBG", f"15;{bg}")
            assert _detect_via_colorfgbg() == "dark", f"index {bg} should be dark"

    def test_light_indices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for bg in ("7", "9", "10", "11", "15"):
            monkeypatch.setenv("COLORFGBG", f"0;{bg}")
            assert _detect_via_colorfgbg() == "light", f"index {bg} should be light"

    def test_missing_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("COLORFGBG", raising=False)
        assert _detect_via_colorfgbg() is None

    def test_malformed_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLORFGBG", "garbage")
        assert _detect_via_colorfgbg() is None


class TestDetectTerminalTheme:
    def test_caches_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reset_detection_cache()
        monkeypatch.setattr(theme_module, "_detect_via_osc11", lambda timeout_ms=200: "light")
        first = detect_terminal_theme()
        # Patch underlying function to a different value — cache should hold
        monkeypatch.setattr(theme_module, "_detect_via_osc11", lambda timeout_ms=200: "dark")
        second = detect_terminal_theme()
        assert first == second == "light"

    def test_falls_back_to_dark(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reset_detection_cache()
        monkeypatch.setattr(theme_module, "_detect_via_osc11", lambda timeout_ms=200: None)
        monkeypatch.setattr(theme_module, "_detect_via_colorfgbg", lambda: None)
        assert detect_terminal_theme() == "dark"

    def test_uses_colorfgbg_when_osc11_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reset_detection_cache()
        monkeypatch.setattr(theme_module, "_detect_via_osc11", lambda timeout_ms=200: None)
        monkeypatch.setattr(theme_module, "_detect_via_colorfgbg", lambda: "light")
        assert detect_terminal_theme() == "light"


class TestAnsiStatusColors:
    def test_ansi_status_colors_are_named(self) -> None:
        for name, value in ANSI_STATUS_COLORS.items():
            assert not value.startswith("#"), f"{name}={value!r} should be ANSI"

    def test_status_color_helper(self) -> None:
        assert status_color("working") == "green"
        assert status_color("error") == "red"
        assert status_color("unknown_status") == "white"


class TestThemeCLIIntegration:
    """Integration: ensure --theme option resolves through cli.py."""

    def test_cli_theme_flag_dark(self) -> None:
        from click.testing import CliRunner

        from open_orchestrator.cli import main

        runner = CliRunner()
        # `owt --theme light version` should run without errors
        result = runner.invoke(main, ["--theme", "light", "version"])
        assert result.exit_code == 0, result.output

    def test_cli_theme_flag_invalid(self) -> None:
        from click.testing import CliRunner

        from open_orchestrator.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--theme", "neon", "version"])
        assert result.exit_code != 0


class TestPaletteRegistry:
    def test_palettes_dict_contains_all_four(self) -> None:
        assert set(PALETTES.keys()) == {"dark", "light", "dark-ansi", "light-ansi"}

    def test_palettes_dict_values_are_palettes(self) -> None:
        for value in PALETTES.values():
            assert isinstance(value, ThemePalette)


class TestCursesPickerTheme:
    """Picker uses theme-aware curses constants."""

    def test_ansi_name_to_curses_returns_int(self) -> None:
        from open_orchestrator.popup.picker import _ansi_name_to_curses

        # Each name should map to a curses constant (int)
        for name in ("red", "green", "yellow", "blue", "cyan", "white", "default"):
            assert isinstance(_ansi_name_to_curses(name), int)

    def test_ansi_name_to_curses_unknown_falls_back(self) -> None:
        import curses

        from open_orchestrator.popup.picker import _ansi_name_to_curses

        assert _ansi_name_to_curses("unknown_color") == curses.COLOR_WHITE
        assert _ansi_name_to_curses("#ff00ff") == curses.COLOR_WHITE  # hex falls back

    def test_get_theme_curses_color_uses_active_palette(self) -> None:
        from open_orchestrator.popup.picker import _get_theme_curses_color

        # Active palette is reset to dark via the autouse fixture
        result = _get_theme_curses_color()
        assert isinstance(result, int)

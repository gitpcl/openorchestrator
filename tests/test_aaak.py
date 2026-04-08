"""Tests for AAAK compression (encoder, decoder, round-trip)."""

from __future__ import annotations

from open_orchestrator.core.aaak import (
    AAAKPair,
    compression_ratio,
    decode,
    decode_pairs,
    encode,
    encode_batch,
)


class TestEncode:
    def test_empty_returns_empty(self) -> None:
        assert encode("") == ""
        assert encode("   ") == ""

    def test_no_matches_returns_empty(self) -> None:
        assert encode("Hello world, how are you today?") == ""

    def test_encodes_project_name(self) -> None:
        result = encode("The project name is owt")
        assert "PRJ:owt" in result

    def test_encodes_language(self) -> None:
        result = encode("This is written in python3.10+")
        assert "LANG:python3.10+" in result

    def test_encodes_test_framework(self) -> None:
        result = encode("We test with pytest")
        assert "TST:pytest" in result

    def test_encodes_standalone_pytest(self) -> None:
        result = encode("The suite runs pytest for everything")
        assert "TST:pytest" in result

    def test_encodes_dependency_count(self) -> None:
        result = encode("Has 7 dependencies total")
        assert "DEPS:7" in result

    def test_encodes_database(self) -> None:
        result = encode("Database is sqlite in WAL mode")
        assert "DB:sqlite" in result

    def test_encodes_version(self) -> None:
        result = encode("Version is 0.3.0 as of today")
        assert "VER:0.3.0" in result

    def test_encodes_owner(self) -> None:
        result = encode("Owner is @pclopes")
        assert "OWNER:@pclopes" in result

    def test_encodes_deadline_iso(self) -> None:
        result = encode("Deadline is 2026-05-01 firm")
        assert "DEAD:2026-05-01" in result

    def test_encodes_blocker(self) -> None:
        result = encode("Blocked by upstream API bug")
        assert "BLOCK:" in result
        decoded = decode(encode("Blocked by upstream API bug"))
        assert "upstream-API-bug" in decoded["BLOCK"]

    def test_multiple_predicates(self) -> None:
        text = "The project name is owt and we test with pytest and the database is sqlite"
        result = encode(text)
        decoded = decode(result)
        assert decoded["PRJ"] == "owt"
        assert decoded["TST"] == "pytest"
        assert decoded["DB"] == "sqlite"

    def test_first_match_wins_per_key(self) -> None:
        """If two patterns match the same key, first encountered wins."""
        text = "The project name is alpha but the project name is beta"
        result = encode(text)
        decoded = decode(result)
        assert decoded["PRJ"] == "alpha"

    def test_format_uses_pipe_separator(self) -> None:
        text = "project name is owt and language is python"
        result = encode(text)
        assert "|" in result
        assert result.count("|") == len(result.split("|")) - 1


class TestDecode:
    def test_empty_returns_empty_dict(self) -> None:
        assert decode("") == {}
        assert decode("   ") == {}

    def test_single_pair(self) -> None:
        assert decode("PRJ:owt") == {"PRJ": "owt"}

    def test_multiple_pairs(self) -> None:
        result = decode("PRJ:owt|LANG:py3.10+|TST:pytest")
        assert result == {
            "PRJ": "owt",
            "LANG": "py3.10+",
            "TST": "pytest",
        }

    def test_preserves_unknown_predicates(self) -> None:
        """Unknown keys are preserved so the format stays extensible."""
        result = decode("XYZ:custom|ABC:value")
        assert result == {"XYZ": "custom", "ABC": "value"}

    def test_skips_invalid_pairs(self) -> None:
        result = decode("PRJ:owt|invalid|LANG:py")
        assert result == {"PRJ": "owt", "LANG": "py"}

    def test_skips_empty_key_or_value(self) -> None:
        result = decode(":value|KEY:|PRJ:owt")
        assert result == {"PRJ": "owt"}


class TestDecodePairs:
    def test_returns_ordered_pairs(self) -> None:
        pairs = decode_pairs("PRJ:owt|LANG:py|TST:pytest")
        assert len(pairs) == 3
        assert pairs[0] == AAAKPair(key="PRJ", value="owt")
        assert pairs[1] == AAAKPair(key="LANG", value="py")
        assert pairs[2] == AAAKPair(key="TST", value="pytest")

    def test_str_representation(self) -> None:
        pair = AAAKPair(key="PRJ", value="owt")
        assert str(pair) == "PRJ:owt"


class TestRoundTrip:
    def test_round_trip_preserves_key_info(self) -> None:
        """encode → decode round-trips preserve all extracted key fields."""
        original = "The project name is owt, written in python3.10+, we test with pytest"
        encoded = encode(original)
        decoded = decode(encoded)
        assert "PRJ" in decoded
        assert "LANG" in decoded
        assert "TST" in decoded
        assert decoded["PRJ"] == "owt"
        assert "pytest" in decoded["TST"]

    def test_encoded_is_readable_without_decoder(self) -> None:
        """AAAK output must be readable by humans/LLMs without external grammar."""
        text = "project name is owt and language is python3.10 and test with pytest"
        encoded = encode(text)
        # Must contain canonical keys that any reader can interpret
        assert "PRJ:" in encoded
        assert "LANG:" in encoded
        assert "TST:" in encoded
        # Must use | as separator (documented format)
        assert "|" in encoded


class TestCompressionRatio:
    def test_empty_encoded_is_zero(self) -> None:
        assert compression_ratio("anything", "") == 0.0

    def test_typical_compression(self) -> None:
        """Compression ratio on a verbose multi-fact sentence should be >1.5x."""
        original = (
            "The project name is open-orchestrator, written in python3.10+, "
            "we test with pytest, the database is sqlite, and architecture is cli+textual"
        )
        encoded = encode(original)
        ratio = compression_ratio(original, encoded)
        assert ratio >= 1.5, f"Ratio {ratio:.1f} too low for {encoded!r}"

    def test_verbose_filler_gets_high_compression(self) -> None:
        """Sentences with a lot of filler around a single fact compress 5-10x."""
        original = (
            "After much debate and consideration of alternatives, and after reviewing "
            "several competing options with the team, we finally concluded that the "
            "project name is owt"
        )
        encoded = encode(original)
        ratio = compression_ratio(original, encoded)
        assert ratio >= 5.0, f"Ratio {ratio:.1f} too low for {encoded!r}"

    def test_short_fact_still_compresses(self) -> None:
        original = "The project name is owt and language is python"
        encoded = encode(original)
        ratio = compression_ratio(original, encoded)
        assert ratio > 1.5


class TestEncodeBatch:
    def test_empty_batch_returns_empty(self) -> None:
        assert encode_batch([]) == ""

    def test_merges_multiple_facts(self) -> None:
        facts = [
            "project name is owt",
            "language is python3.10",
            "test with pytest",
        ]
        result = encode_batch(facts)
        decoded = decode(result)
        assert decoded["PRJ"] == "owt"
        assert decoded["LANG"] == "python3.10"
        assert decoded["TST"] == "pytest"

    def test_dedupes_by_key_first_wins(self) -> None:
        facts = [
            "project name is alpha",
            "project name is beta",
        ]
        result = encode_batch(facts)
        decoded = decode(result)
        assert decoded["PRJ"] == "alpha"

    def test_skips_facts_with_no_match(self) -> None:
        facts = [
            "hello world",
            "project name is owt",
            "nothing to see here",
        ]
        result = encode_batch(facts)
        decoded = decode(result)
        assert decoded == {"PRJ": "owt"}

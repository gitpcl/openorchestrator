"""AAAK compression — readable shorthand for L1 critical facts.

AAAK is a deterministic, LLM-readable key-value format used to compress
critical facts so the L0+L1 payload stays under 250 tokens while remaining
human- and LLM-readable without a decoder.

Grammar::

    AAAK := PAIR (| PAIR)*
    PAIR := PREDICATE : VALUE

Example::

    PRJ:owt|LANG:py3.10+|TST:pytest|DEPS:7+opt|ARCH:cli+textual|MEM:sqlite-fts5

The encoder is a pure heuristic — no LLM calls. It uses keyword matching
against a predicate dictionary to extract structured key-value tuples from
natural language. The decoder is optional (the format is self-describing);
it exists for round-trip testing and programmatic recovery of key fields.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Predicate dictionary (extensible)
# --------------------------------------------------------------------------
# Maps canonical keys to (regex pattern, value extractor).
# Order matters — earlier entries win when a string matches multiple.

PREDICATES: dict[str, str] = {
    "PRJ": "project",
    "LANG": "language",
    "TST": "test framework",
    "DEPS": "dependencies",
    "ARCH": "architecture",
    "MEM": "memory backend",
    "STAT": "status",
    "OWNER": "owner",
    "DEAD": "deadline",
    "BLOCK": "blocker",
    "DEC": "decision",
    "WHY": "rationale",
    "DB": "database",
    "VER": "version",
    "ENV": "environment",
}


# Keyword → predicate mapping used by the heuristic encoder.
# Each entry: compiled regex → (predicate key, value extractor group name or None).
# If the regex has a named group "val", that group is used as the value.
# Otherwise the matched phrase after the keyword is captured.

_ENCODE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("PRJ", re.compile(r"\bproject(?:\s+name)?\s*(?:is|=|:)?\s*(?P<val>[A-Za-z0-9\-_.+]+)", re.I)),
    ("LANG", re.compile(r"\blanguage\s*(?:is|=|:)?\s*(?P<val>[A-Za-z0-9+.\-]+)", re.I)),
    (
        "LANG",
        re.compile(
            r"\b(?:uses|written in|based on)\s+(?P<val>python3?(?:\.\d+)?\+?"
            r"|typescript|javascript|go|rust|java|kotlin|php|swift)",
            re.I,
        ),
    ),
    (
        "TST",
        re.compile(
            r"\b(?:test(?:s|ing|ed)?\s+(?:with|using|framework|via)|test framework:?)\s+(?P<val>[A-Za-z0-9\-_.]+)",
            re.I,
        ),
    ),
    (
        "TST",
        re.compile(r"\b(?P<val>pytest|jest|mocha|vitest|junit|rspec|go test|cargo test)\b", re.I),
    ),
    (
        "DEPS",
        re.compile(
            r"\b(?P<val>\d+(?:\+[A-Za-z]+)?)\s+dependencies?\b",
            re.I,
        ),
    ),
    (
        "ARCH",
        re.compile(
            r"\barchitecture\s*(?:is|=|:)?\s*(?P<val>[A-Za-z0-9+\-_.]+(?:\+[A-Za-z0-9+\-_.]+)*)",
            re.I,
        ),
    ),
    (
        "MEM",
        re.compile(
            r"\b(?:memory|storage)\s+(?:backend|uses|is)\s*(?:=|:)?\s*(?P<val>[A-Za-z0-9\-_.+]+)",
            re.I,
        ),
    ),
    (
        "DB",
        re.compile(
            r"\b(?:database|db)\s*(?:is|=|:|uses)?\s*(?P<val>sqlite|postgres|postgresql|mysql|mariadb|mongodb|redis|dynamodb)",
            re.I,
        ),
    ),
    ("VER", re.compile(r"\bversion\s*(?:is|=|:)?\s*(?P<val>\d+\.\d+(?:\.\d+)?[A-Za-z0-9\-]*)", re.I)),
    (
        "ENV",
        re.compile(
            r"\benvironment\s*(?:is|=|:)?\s*(?P<val>dev|development|prod|production|staging|test)",
            re.I,
        ),
    ),
    ("STAT", re.compile(r"\bstatus\s*(?:is|=|:)?\s*(?P<val>[A-Za-z0-9\-_]+)", re.I)),
    ("OWNER", re.compile(r"\bowner\s*(?:is|=|:)?\s*(?P<val>[@A-Za-z0-9\-_.]+)", re.I)),
    (
        "DEAD",
        re.compile(
            r"\bdeadline\s*(?:is|=|:)?\s*(?P<val>\d{4}-\d{2}-\d{2}|[A-Za-z]+\s+\d{1,2})",
            re.I,
        ),
    ),
    (
        "BLOCK",
        re.compile(r"\bblock(?:ed|er)\s*(?:by|on|:)\s*(?P<val>[^.,;\n]+)", re.I),
    ),
    (
        "DEC",
        re.compile(r"\bdecided\s+(?:to\s+)?(?P<val>[^.,;\n]+)", re.I),
    ),
    (
        "WHY",
        re.compile(r"\bbecause\s+(?P<val>[^.,;\n]+)", re.I),
    ),
]


# --------------------------------------------------------------------------
# Encoder
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AAAKPair:
    """A single AAAK key-value pair."""

    key: str
    value: str

    def __str__(self) -> str:
        return f"{self.key}:{self.value}"


def _clean_value(raw: str) -> str:
    """Normalize a captured value: strip whitespace, collapse spaces to dashes."""
    v = raw.strip().strip(".,;:'\"")
    v = re.sub(r"\s+", "-", v)
    return v


def encode(text: str) -> str:
    """Compress a natural-language fact into AAAK format.

    Returns a string like ``PRJ:owt|LANG:py3.10+|TST:pytest``.
    If no predicates match, returns an empty string.
    """
    if not text or not text.strip():
        return ""

    seen: dict[str, str] = {}  # key → value (first match wins per key)
    for predicate, pattern in _ENCODE_RULES:
        if predicate in seen:
            continue
        match = pattern.search(text)
        if match is None:
            continue
        value = _clean_value(match.group("val"))
        if not value:
            continue
        seen[predicate] = value

    if not seen:
        return ""

    return "|".join(f"{k}:{v}" for k, v in seen.items())


def encode_batch(facts: Iterable[str]) -> str:
    """Compress multiple facts into a single AAAK block.

    Duplicates are deduplicated by key (first fact wins per key).
    """
    merged: dict[str, str] = {}
    for fact in facts:
        encoded = encode(fact)
        if not encoded:
            continue
        for pair in encoded.split("|"):
            if ":" not in pair:
                continue
            key, _, value = pair.partition(":")
            if key not in merged:
                merged[key] = value
    return "|".join(f"{k}:{v}" for k, v in merged.items())


# --------------------------------------------------------------------------
# Decoder
# --------------------------------------------------------------------------


def decode(aaak: str) -> dict[str, str]:
    """Parse an AAAK string into a key-value dict.

    Invalid pairs (missing colon, empty key/value) are skipped.
    Unknown predicates are preserved as-is so the format is extensible.
    """
    if not aaak:
        return {}
    result: dict[str, str] = {}
    for pair in aaak.split("|"):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, _, value = pair.partition(":")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        result[key] = value
    return result


def decode_pairs(aaak: str) -> list[AAAKPair]:
    """Parse an AAAK string into ordered pairs (preserves duplicates)."""
    if not aaak:
        return []
    pairs: list[AAAKPair] = []
    for chunk in aaak.split("|"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        key, _, value = chunk.partition(":")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        pairs.append(AAAKPair(key=key, value=value))
    return pairs


def compression_ratio(original: str, encoded: str) -> float:
    """Return the compression ratio (original length / encoded length).

    Ratios of 5-10x are typical for sentences matching 2-4 predicates.
    """
    if not encoded:
        return 0.0
    return len(original) / len(encoded)

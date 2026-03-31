"""Branch name generation from task descriptions.

Generates clean, consistent branch names from natural language task descriptions.
Uses simple text processing — no LLM needed.
"""

import re

# Common filler words to strip from branch names
_FILLER_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "must",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "my",
        "our",
        "your",
        "their",
        "we",
        "i",
        "me",
        "us",
        "them",
        "so",
        "if",
        "then",
        "than",
        "when",
        "where",
        "how",
        "what",
        "which",
        "who",
        "whom",
    }
)

# Prefix keywords that map to conventional branch prefixes
_PREFIX_KEYWORDS = {
    "fix": "fix",
    "bugfix": "fix",
    "bug": "fix",
    "hotfix": "hotfix",
    "patch": "fix",
    "repair": "fix",
    "resolve": "fix",
    "add": "feat",
    "feature": "feat",
    "implement": "feat",
    "create": "feat",
    "build": "feat",
    "introduce": "feat",
    "refactor": "refactor",
    "restructure": "refactor",
    "reorganize": "refactor",
    "clean": "refactor",
    "cleanup": "refactor",
    "docs": "docs",
    "document": "docs",
    "documentation": "docs",
    "test": "test",
    "testing": "test",
    "experiment": "experiment",
    "explore": "experiment",
    "research": "experiment",
    "spike": "experiment",
    "chore": "chore",
    "update": "feat",
    "upgrade": "chore",
    "migrate": "chore",
    "remove": "feat",
    "delete": "feat",
    "improve": "feat",
    "enhance": "feat",
    "optimize": "feat",
    "perf": "perf",
    "performance": "perf",
    "security": "security",
    "secure": "security",
}

# Maximum slug length (excluding prefix)
_MAX_SLUG_LENGTH = 50
_MAX_WORDS = 6


def generate_branch_name(
    description: str,
    prefix: str | None = None,
    max_length: int = _MAX_SLUG_LENGTH,
) -> str:
    """Generate a branch name from a task description.

    Args:
        description: Natural language task description (e.g., "Add user authentication with JWT").
        prefix: Override the auto-detected prefix (e.g., "feat", "fix").
        max_length: Maximum length for the slug portion (default: 50).

    Returns:
        A clean branch name like "feat/add-user-auth-jwt".
    """
    if not description or not description.strip():
        raise ValueError("Task description cannot be empty")

    # Normalize: lowercase, strip punctuation except hyphens
    text = description.strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)

    # Split into words
    words = text.split()
    if not words:
        raise ValueError("Task description produced no valid words")

    # Detect prefix from first word
    detected_prefix = None
    if words[0] in _PREFIX_KEYWORDS:
        detected_prefix = _PREFIX_KEYWORDS[words[0]]
        # Keep the action word in the slug if it's meaningful
        if words[0] in {"add", "fix", "remove", "delete", "update", "improve", "enhance", "optimize", "create", "build"}:
            pass  # Keep it
        else:
            words = words[1:]  # Strip prefix keyword from slug

    branch_prefix = prefix or detected_prefix or "feat"

    # Remove filler words
    filtered = [word for word in words if word not in _FILLER_WORDS]
    if not filtered:
        # All words were filler, keep the original
        filtered = words

    # Limit word count
    filtered = filtered[:_MAX_WORDS]

    # Join with hyphens
    slug = "-".join(filtered)

    # Truncate to max length
    if len(slug) > max_length:
        # Try to break at a word boundary
        truncated = slug[:max_length]
        last_hyphen = truncated.rfind("-")
        if last_hyphen > 10:  # Only break at word boundary if reasonable
            slug = truncated[:last_hyphen]
        else:
            slug = truncated

    # Clean up any double hyphens or trailing hyphens
    slug = re.sub(r"-+", "-", slug).strip("-")

    if not slug:
        slug = "task"

    return f"{branch_prefix}/{slug}"

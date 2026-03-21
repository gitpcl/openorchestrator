"""Tests for branch name generation from task descriptions."""

from __future__ import annotations

import pytest

from open_orchestrator.core.branch_namer import generate_branch_name


class TestGenerateBranchName:
    def test_basic_feature(self):
        result = generate_branch_name("Add user authentication")
        assert result == "feat/add-user-authentication"

    def test_fix_prefix_detected(self):
        result = generate_branch_name("Fix login redirect bug")
        assert result.startswith("fix/")
        assert "login" in result

    def test_refactor_prefix_detected(self):
        result = generate_branch_name("Refactor database queries")
        assert result.startswith("refactor/")

    def test_docs_prefix_detected(self):
        result = generate_branch_name("Document API endpoints")
        assert result.startswith("docs/")

    def test_custom_prefix_override(self):
        result = generate_branch_name("Add user auth", prefix="hotfix")
        assert result.startswith("hotfix/")

    def test_filler_words_stripped(self):
        result = generate_branch_name("Add the user authentication with JWT")
        assert "the" not in result.split("/")[1].split("-")
        assert "with" not in result.split("/")[1].split("-")

    def test_max_word_limit(self):
        result = generate_branch_name("Add very long description with many extra words here today")
        slug = result.split("/")[1]
        assert len(slug.split("-")) <= 6

    def test_truncation_at_word_boundary(self):
        result = generate_branch_name(
            "Implement comprehensive authentication system with JWT refresh tokens",
            max_length=30,
        )
        slug = result.split("/")[1]
        assert len(slug) <= 30

    def test_empty_description_raises(self):
        with pytest.raises(ValueError, match="empty"):
            generate_branch_name("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty"):
            generate_branch_name("   ")

    def test_all_filler_words_kept(self):
        # When all words are filler, keep them rather than producing empty slug
        result = generate_branch_name("the and or")
        assert "/" in result
        slug = result.split("/")[1]
        assert len(slug) > 0

    def test_punctuation_stripped(self):
        result = generate_branch_name("Fix bug! (critical)")
        assert "!" not in result
        assert "(" not in result

    def test_no_double_hyphens(self):
        result = generate_branch_name("Fix  --  something  weird")
        assert "--" not in result

    def test_no_trailing_hyphens(self):
        result = generate_branch_name("Add feature")
        slug = result.split("/")[1]
        assert not slug.endswith("-")
        assert not slug.startswith("-")

    def test_action_words_kept_in_slug(self):
        result = generate_branch_name("Add user model")
        assert "add" in result

    def test_default_prefix_is_feat(self):
        result = generate_branch_name("something new")
        assert result.startswith("feat/")

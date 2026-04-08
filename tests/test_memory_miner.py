"""Tests for the fact miner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from open_orchestrator.core.memory_miner import FactMiner, MinedFact
from open_orchestrator.models.memory import MemoryType


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)


def _commit(root: Path, subject: str, file_name: str = "README.md") -> None:
    (root / file_name).write_text(f"{subject}\n")
    subprocess.run(["git", "add", file_name], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", subject, "--no-verify"], cwd=root, check=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    _init_git_repo(tmp_path)
    return tmp_path


class TestGitLogMining:
    def test_mine_conventional_commits(self, git_repo: Path) -> None:
        _commit(git_repo, "feat(auth): add JWT support")
        _commit(git_repo, "fix(db): handle connection timeout")
        _commit(git_repo, "refactor(core): split manager module")
        _commit(git_repo, "chore: update deps")  # Not conventional we care about

        miner = FactMiner(git_repo)
        facts = miner.mine_git_log()

        # chore is filtered out
        assert len(facts) == 3
        contents = [f.content for f in facts]
        assert any("feat: add JWT support" in c for c in contents)
        assert any("fix: handle connection timeout" in c for c in contents)
        assert any("refactor: split manager module" in c for c in contents)

    def test_source_attribution(self, git_repo: Path) -> None:
        _commit(git_repo, "feat: initial feature")
        miner = FactMiner(git_repo)
        facts = miner.mine_git_log()
        assert len(facts) == 1
        assert facts[0].source.startswith("commit:")
        # sha is 12 hex chars
        sha_part = facts[0].source.split(":", 1)[1]
        assert len(sha_part) == 12

    def test_commit_type_classification(self, git_repo: Path) -> None:
        _commit(git_repo, "feat: add feature")
        _commit(git_repo, "refactor: reshape module", file_name="a.md")
        _commit(git_repo, "docs: add README", file_name="b.md")
        miner = FactMiner(git_repo)
        facts = miner.mine_git_log()
        by_type = {f.category: f.kind for f in facts}
        assert by_type["feat"] == MemoryType.DECISION
        assert by_type["refactor"] == MemoryType.ARCHITECTURE
        assert by_type["docs"] == MemoryType.REFERENCE

    def test_mines_at_least_5_from_10_commits(self, git_repo: Path) -> None:
        for i in range(10):
            _commit(
                git_repo,
                f"feat(x): feature {i}",
                file_name=f"f{i}.md",
            )
        miner = FactMiner(git_repo)
        facts = miner.mine_git_log()
        assert len(facts) >= 5

    def test_empty_repo(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        miner = FactMiner(tmp_path)
        assert miner.mine_git_log() == []

    def test_non_git_dir(self, tmp_path: Path) -> None:
        miner = FactMiner(tmp_path)
        assert miner.mine_git_log() == []


class TestProgressFileMining:
    def test_mine_bullet_entries(self, tmp_path: Path) -> None:
        (tmp_path / ".harness").mkdir()
        (tmp_path / ".harness" / "progress_log.md").write_text(
            "# Progress\n- [auth] Chose JWT over sessions\n- [db] Added FTS5 indexing\n"
        )
        miner = FactMiner(tmp_path)
        facts = miner.mine_progress_files()
        assert len(facts) == 2
        categories = {f.category for f in facts}
        assert categories == {"auth", "db"}
        # All have file:line source attribution
        for fact in facts:
            assert ":" in fact.source
            assert "progress_log.md" in fact.source

    def test_mine_decided_phrases(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("## Notes\nWe decided to use SQLite because it's embedded.\n")
        miner = FactMiner(tmp_path)
        facts = miner.mine_progress_files()
        assert len(facts) == 1
        assert "decided" in facts[0].content.lower()
        assert facts[0].category == "progress"

    def test_missing_progress_file_safe(self, tmp_path: Path) -> None:
        miner = FactMiner(tmp_path)
        assert miner.mine_progress_files() == []


class TestCommentMining:
    def test_mine_python_todos(self, tmp_path: Path) -> None:
        src = tmp_path / "app.py"
        src.write_text("def foo():\n    # TODO: refactor this bit\n    pass\n# NOTE: this file is legacy\n")
        miner = FactMiner(tmp_path)
        facts = miner.mine_code_comments()
        assert len(facts) == 2
        tags = {f.category for f in facts}
        assert tags == {"todo", "note"}

    def test_source_attribution_has_file_line(self, tmp_path: Path) -> None:
        src = tmp_path / "a.py"
        src.write_text("line1\n# DECISION: use asyncio\nline3\n")
        miner = FactMiner(tmp_path)
        facts = miner.mine_code_comments()
        assert len(facts) == 1
        assert facts[0].source == "a.py:2"

    def test_ignores_venv_and_node_modules(self, tmp_path: Path) -> None:
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "x.py").write_text("# TODO: ignored\n")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "y.js").write_text("// TODO: ignored\n")
        # Real file that should be found
        (tmp_path / "real.py").write_text("# TODO: real fact\n")
        miner = FactMiner(tmp_path)
        facts = miner.mine_code_comments()
        assert len(facts) == 1
        assert facts[0].source == "real.py:1"

    def test_tag_classification(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("# DECISION: A\n# NOTE: B\n# TODO: C\n")
        miner = FactMiner(tmp_path)
        facts = miner.mine_code_comments()
        by_tag = {f.category: f.kind for f in facts}
        assert by_tag["decision"] == MemoryType.DECISION
        assert by_tag["note"] == MemoryType.REFERENCE
        assert by_tag["todo"] == MemoryType.CONVENTION


class TestMineAll:
    def test_combined_mining(self, git_repo: Path) -> None:
        _commit(git_repo, "feat: initial")
        (git_repo / ".harness").mkdir()
        (git_repo / ".harness" / "progress_log.md").write_text("- [setup] initial scaffolding\n")
        (git_repo / "app.py").write_text("# TODO: wire this up\n")
        miner = FactMiner(git_repo)
        facts = miner.mine_all()
        sources = {f.source.split(":", 1)[0] for f in facts}
        assert "commit" in sources
        assert any("progress_log.md" in f.source for f in facts)
        assert any("app.py" in f.source for f in facts)


class TestMinedFactModel:
    def test_frozen(self) -> None:
        fact = MinedFact(
            content="test",
            kind=MemoryType.DECISION,
            category="x",
            source="commit:abc",
        )
        with pytest.raises(Exception):
            fact.content = "changed"  # type: ignore[misc]

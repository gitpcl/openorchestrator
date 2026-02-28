"""
Tests for PyPI distribution and package metadata.

These tests verify that the package is correctly configured for PyPI distribution,
including metadata, entry points, and included files.
"""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

# Get the project root directory (parent of tests directory)
PROJECT_ROOT = Path(__file__).parent.parent


class TestPyprojectMetadata:
    """Tests for pyproject.toml metadata configuration."""

    @pytest.fixture
    def pyproject_content(self) -> str:
        """Read the pyproject.toml content."""
        pyproject_path = PROJECT_ROOT / "pyproject.toml"
        return pyproject_path.read_text()

    def test_project_name(self, pyproject_content: str) -> None:
        """Verify the package name is correctly set."""
        assert 'name = "open-orchestrator"' in pyproject_content

    def test_project_version(self, pyproject_content: str) -> None:
        """Verify the package version is set."""
        assert 'version = "0.1.0"' in pyproject_content

    def test_project_description(self, pyproject_content: str) -> None:
        """Verify the package has a description."""
        assert 'description = "Git Worktree + Claude Code orchestration tool' in pyproject_content

    def test_readme_configured(self, pyproject_content: str) -> None:
        """Verify README.md is configured as the long description source."""
        assert 'readme = "README.md"' in pyproject_content

    def test_python_requires(self, pyproject_content: str) -> None:
        """Verify Python version requirement is set."""
        assert 'requires-python = ">=3.10"' in pyproject_content

    def test_license_configured(self, pyproject_content: str) -> None:
        """Verify MIT license is configured."""
        assert 'license = {text = "MIT"}' in pyproject_content

    def test_authors_configured(self, pyproject_content: str) -> None:
        """Verify authors are configured."""
        assert "authors = [" in pyproject_content
        assert "Pedro Lopes" in pyproject_content

    def test_keywords_configured(self, pyproject_content: str) -> None:
        """Verify keywords are configured."""
        assert "keywords = [" in pyproject_content
        assert '"git"' in pyproject_content
        assert '"worktree"' in pyproject_content
        assert '"claude"' in pyproject_content

    def test_classifiers_configured(self, pyproject_content: str) -> None:
        """Verify PyPI classifiers are configured."""
        required_classifiers = [
            "Development Status :: 3 - Alpha",
            "Environment :: Console",
            "Intended Audience :: Developers",
            "License :: OSI Approved :: MIT License",
            "Programming Language :: Python :: 3",
            "Programming Language :: Python :: 3.10",
        ]
        for classifier in required_classifiers:
            assert classifier in pyproject_content, f"Missing classifier: {classifier}"

    def test_project_urls_configured(self, pyproject_content: str) -> None:
        """Verify project URLs are configured for PyPI."""
        assert "[project.urls]" in pyproject_content
        assert "Homepage = " in pyproject_content
        assert "Repository = " in pyproject_content
        assert "Documentation = " in pyproject_content
        assert "Changelog = " in pyproject_content
        assert '"Bug Tracker" = ' in pyproject_content

    def test_cli_entry_point_configured(self, pyproject_content: str) -> None:
        """Verify the owt CLI entry point is configured."""
        assert "[project.scripts]" in pyproject_content
        assert 'owt = "open_orchestrator.cli:main"' in pyproject_content

    def test_build_system_configured(self, pyproject_content: str) -> None:
        """Verify hatchling build backend is configured."""
        assert "[build-system]" in pyproject_content
        assert 'requires = ["hatchling"]' in pyproject_content
        assert 'build-backend = "hatchling.build"' in pyproject_content

    def test_wheel_artifacts_configured(self, pyproject_content: str) -> None:
        """Verify skills/*.md files are included as wheel artifacts."""
        assert "[tool.hatch.build.targets.wheel]" in pyproject_content
        assert 'artifacts = ["src/open_orchestrator/skills/**/*.md"]' in pyproject_content

    def test_sdist_includes_configured(self, pyproject_content: str) -> None:
        """Verify sdist includes the necessary files."""
        assert "[tool.hatch.build.targets.sdist]" in pyproject_content
        assert '"src/"' in pyproject_content
        assert '"tests/"' in pyproject_content
        assert '"README.md"' in pyproject_content
        assert '"LICENSE"' in pyproject_content


class TestRequiredFiles:
    """Tests for required distribution files."""

    def test_readme_exists(self) -> None:
        """Verify README.md exists in the project root."""
        readme_path = PROJECT_ROOT / "README.md"
        assert readme_path.exists(), "README.md is required for PyPI distribution"

    def test_readme_not_empty(self) -> None:
        """Verify README.md has substantial content."""
        readme_path = PROJECT_ROOT / "README.md"
        content = readme_path.read_text()
        # README should have substantial content (at least 1000 chars)
        assert len(content) > 1000, "README.md should have substantial content"

    def test_readme_has_installation_section(self) -> None:
        """Verify README.md has installation instructions."""
        readme_path = PROJECT_ROOT / "README.md"
        content = readme_path.read_text()
        assert "## Installation" in content
        assert "pipx install open-orchestrator" in content
        assert "pip install open-orchestrator" in content
        assert "uv pip install open-orchestrator" in content

    def test_license_exists(self) -> None:
        """Verify LICENSE file exists."""
        license_path = PROJECT_ROOT / "LICENSE"
        assert license_path.exists(), "LICENSE is required for PyPI distribution"

    def test_license_is_mit(self) -> None:
        """Verify LICENSE is MIT license."""
        license_path = PROJECT_ROOT / "LICENSE"
        content = license_path.read_text()
        assert "MIT License" in content or "MIT" in content

    def test_version_file_exists(self) -> None:
        """Verify __version__.py exists."""
        version_path = PROJECT_ROOT / "src" / "open_orchestrator" / "__version__.py"
        assert version_path.exists(), "__version__.py is required"

    def test_version_file_content(self) -> None:
        """Verify __version__.py contains version string."""
        version_path = PROJECT_ROOT / "src" / "open_orchestrator" / "__version__.py"
        content = version_path.read_text()
        assert "__version__" in content
        assert "0.1.0" in content

    def test_skill_file_exists(self) -> None:
        """Verify the Claude Code skill file exists."""
        skill_path = PROJECT_ROOT / "src" / "open_orchestrator" / "skills" / "open-orchestrator" / "SKILL.md"
        assert skill_path.exists(), "SKILL.md is required for distribution"


@pytest.mark.slow
class TestPackageBuild:
    """Tests for package build process."""

    @pytest.fixture
    def build_dist(self, tmp_path: Path) -> Path:
        """Build the package and return the dist directory."""
        # Copy project to temp location to avoid polluting the source
        import shutil

        # Build in the original project directory
        dist_dir = PROJECT_ROOT / "dist"

        # Clean any existing builds
        if dist_dir.exists():
            shutil.rmtree(dist_dir)

        # Run build
        result = subprocess.run(
            [sys.executable, "-m", "build"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            pytest.fail(f"Build failed: {result.stderr}")

        return dist_dir

    def test_build_creates_wheel(self, build_dist: Path) -> None:
        """Verify build creates a wheel file."""
        wheels = list(build_dist.glob("*.whl"))
        assert len(wheels) == 1, "Expected exactly one wheel file"
        assert "open_orchestrator" in wheels[0].name

    def test_build_creates_sdist(self, build_dist: Path) -> None:
        """Verify build creates a source distribution."""
        tarballs = list(build_dist.glob("*.tar.gz"))
        assert len(tarballs) == 1, "Expected exactly one source distribution"
        assert "open_orchestrator" in tarballs[0].name

    def test_wheel_contains_skill_file(self, build_dist: Path) -> None:
        """Verify wheel contains the SKILL.md artifact."""
        wheels = list(build_dist.glob("*.whl"))
        assert len(wheels) == 1

        with zipfile.ZipFile(wheels[0], "r") as whl:
            names = whl.namelist()
            skill_files = [n for n in names if "SKILL.md" in n]
            assert len(skill_files) >= 1, "Wheel should contain SKILL.md"

    def test_wheel_contains_cli_module(self, build_dist: Path) -> None:
        """Verify wheel contains the CLI module."""
        wheels = list(build_dist.glob("*.whl"))
        assert len(wheels) == 1

        with zipfile.ZipFile(wheels[0], "r") as whl:
            names = whl.namelist()
            cli_files = [n for n in names if "cli.py" in n]
            assert len(cli_files) >= 1, "Wheel should contain cli.py"

    def test_wheel_has_entry_points(self, build_dist: Path) -> None:
        """Verify wheel has console script entry points defined."""
        wheels = list(build_dist.glob("*.whl"))
        assert len(wheels) == 1

        with zipfile.ZipFile(wheels[0], "r") as whl:
            # Entry points are in the .dist-info/entry_points.txt or METADATA
            names = whl.namelist()
            dist_info = [n for n in names if ".dist-info" in n]
            assert len(dist_info) > 0, "Wheel should have .dist-info directory"

            # Check for entry_points.txt
            entry_points_files = [n for n in names if "entry_points.txt" in n]
            assert len(entry_points_files) == 1, "Wheel should have entry_points.txt"

            # Read entry points content
            with whl.open(entry_points_files[0]) as ep_file:
                content = ep_file.read().decode("utf-8")
                assert "owt" in content, "Entry points should define 'owt' command"
                assert "open_orchestrator.cli:main" in content


class TestPackageImport:
    """Tests for package import and basic functionality."""

    def test_package_imports(self) -> None:
        """Verify the package can be imported."""
        import open_orchestrator

        assert open_orchestrator is not None

    def test_version_accessible(self) -> None:
        """Verify version is accessible from package."""
        from open_orchestrator.__version__ import __version__

        assert __version__ == "0.1.0"

    def test_cli_module_imports(self) -> None:
        """Verify CLI module can be imported."""
        from open_orchestrator import cli

        assert hasattr(cli, "main")
        assert callable(cli.main)

    def test_core_modules_import(self) -> None:
        """Verify core modules can be imported."""
        from open_orchestrator.core import project_detector, tmux_manager, worktree

        assert worktree is not None
        assert tmux_manager is not None
        assert project_detector is not None


class TestMakefileTargets:
    """Tests for Makefile build and publish targets."""

    @pytest.fixture
    def makefile_content(self) -> str:
        """Read the Makefile content."""
        makefile_path = PROJECT_ROOT / "Makefile"
        return makefile_path.read_text()

    def test_build_target_exists(self, makefile_content: str) -> None:
        """Verify build target exists in Makefile."""
        assert "build:" in makefile_content
        assert "python -m build" in makefile_content

    def test_build_clean_target_exists(self, makefile_content: str) -> None:
        """Verify build-clean target exists in Makefile."""
        assert "build-clean:" in makefile_content

    def test_dist_check_target_exists(self, makefile_content: str) -> None:
        """Verify dist-check target exists in Makefile."""
        assert "dist-check:" in makefile_content
        assert "twine check" in makefile_content

    def test_publish_test_target_exists(self, makefile_content: str) -> None:
        """Verify publish-test target exists in Makefile."""
        assert "publish-test:" in makefile_content
        assert "testpypi" in makefile_content

    def test_publish_target_exists(self, makefile_content: str) -> None:
        """Verify publish target exists in Makefile."""
        assert "publish:" in makefile_content
        assert "twine upload" in makefile_content


class TestGitHubWorkflow:
    """Tests for GitHub Actions publish workflow."""

    @pytest.fixture
    def workflow_content(self) -> str:
        """Read the publish workflow content."""
        workflow_path = PROJECT_ROOT / ".github" / "workflows" / "publish.yml"
        return workflow_path.read_text()

    def test_workflow_exists(self) -> None:
        """Verify publish workflow exists."""
        workflow_path = PROJECT_ROOT / ".github" / "workflows" / "publish.yml"
        assert workflow_path.exists(), "publish.yml workflow should exist"

    def test_workflow_has_release_trigger(self, workflow_content: str) -> None:
        """Verify workflow triggers on release."""
        assert "release:" in workflow_content
        assert "types: [published]" in workflow_content

    def test_workflow_has_manual_trigger(self, workflow_content: str) -> None:
        """Verify workflow has manual trigger option."""
        assert "workflow_dispatch:" in workflow_content

    def test_workflow_uses_trusted_publishing(self, workflow_content: str) -> None:
        """Verify workflow uses OIDC trusted publishing."""
        assert "id-token: write" in workflow_content
        assert "pypa/gh-action-pypi-publish" in workflow_content

    def test_workflow_has_build_job(self, workflow_content: str) -> None:
        """Verify workflow has build job."""
        assert "build:" in workflow_content
        assert "python -m build" in workflow_content
        assert "twine check" in workflow_content

    def test_workflow_has_testpypi_job(self, workflow_content: str) -> None:
        """Verify workflow has TestPyPI publish job."""
        assert "publish-testpypi:" in workflow_content
        assert "test.pypi.org" in workflow_content

    def test_workflow_has_pypi_job(self, workflow_content: str) -> None:
        """Verify workflow has PyPI publish job."""
        assert "publish-pypi:" in workflow_content

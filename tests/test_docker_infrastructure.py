"""Tests for Docker test environment infrastructure.

Validates Docker configuration files, pytest setup, and coverage reporting
to ensure the test infrastructure works correctly.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# Use tomllib from standard library in Python 3.11+, fall back to tomli for 3.10
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]


class TestDockerConfiguration:
    """Tests for Docker and Docker Compose configuration."""

    def test_docker_compose_yaml_syntax(self) -> None:
        """Verify docker-compose.test.yml has valid YAML syntax."""
        # Arrange
        compose_file = Path("docker-compose.test.yml")

        # Act & Assert
        assert compose_file.exists(), "docker-compose.test.yml file not found"

        with compose_file.open("r", encoding="utf-8") as f:
            compose_config = yaml.safe_load(f)

        assert compose_config is not None, "docker-compose.test.yml is empty"
        assert "services" in compose_config, "docker-compose.test.yml missing 'services' key"

    def test_docker_compose_has_test_runner_service(self) -> None:
        """Verify docker-compose.test.yml defines test-runner service."""
        # Arrange
        compose_file = Path("docker-compose.test.yml")

        # Act
        with compose_file.open("r", encoding="utf-8") as f:
            compose_config = yaml.safe_load(f)

        # Assert
        assert "test-runner" in compose_config["services"], "test-runner service not defined"
        test_runner = compose_config["services"]["test-runner"]

        assert "build" in test_runner, "test-runner missing build configuration"
        assert test_runner["build"]["dockerfile"] == "Dockerfile.test", "test-runner not using Dockerfile.test"
        assert "volumes" in test_runner, "test-runner missing volume mounts"
        assert "environment" in test_runner, "test-runner missing environment variables"

    def test_docker_compose_has_test_interactive_service(self) -> None:
        """Verify docker-compose.test.yml defines test-interactive service."""
        # Arrange
        compose_file = Path("docker-compose.test.yml")

        # Act
        with compose_file.open("r", encoding="utf-8") as f:
            compose_config = yaml.safe_load(f)

        # Assert
        assert "test-interactive" in compose_config["services"], "test-interactive service not defined"
        test_interactive = compose_config["services"]["test-interactive"]

        assert "build" in test_interactive, "test-interactive missing build configuration"
        assert test_interactive["command"] == "/bin/bash", "test-interactive should run bash shell"
        assert test_interactive.get("tty") is True, "test-interactive should have tty enabled"
        assert test_interactive.get("stdin_open") is True, "test-interactive should have stdin_open enabled"

    def test_dockerfile_exists_and_has_required_components(self) -> None:
        """Verify Dockerfile.test exists and contains required components."""
        # Arrange
        dockerfile = Path("Dockerfile.test")

        # Act & Assert
        assert dockerfile.exists(), "Dockerfile.test not found"

        content = dockerfile.read_text(encoding="utf-8")

        # Verify Python base image
        assert "FROM python:" in content, "Dockerfile.test missing Python base image"
        assert any(v in content for v in ["3.10", "3.11", "3.12"]), "Dockerfile.test should use Python 3.10+"

        # Verify system dependencies
        assert "git" in content, "Dockerfile.test missing git dependency"
        assert "tmux" in content, "Dockerfile.test missing tmux dependency"

        # Verify Python environment setup
        assert "pip install" in content, "Dockerfile.test missing pip install command"
        assert any(dep in content for dep in ["[dev]", "dev"]), "Dockerfile.test should install dev dependencies"

        # Verify git configuration
        assert "git config" in content, "Dockerfile.test missing git configuration"

    def test_dockerfile_default_command_runs_pytest_with_coverage(self) -> None:
        """Verify Dockerfile.test default command runs pytest with coverage."""
        # Arrange
        dockerfile = Path("Dockerfile.test")

        # Act
        content = dockerfile.read_text(encoding="utf-8")

        # Assert
        assert "CMD" in content, "Dockerfile.test missing CMD instruction"
        assert "pytest" in content, "Dockerfile.test CMD should run pytest"
        assert "--cov" in content, "Dockerfile.test CMD should include coverage reporting"
        assert "--cov-report" in content, "Dockerfile.test CMD should specify coverage report format"


class TestPytestConfiguration:
    """Tests for pytest configuration in pyproject.toml."""

    def test_pyproject_toml_has_pytest_config(self) -> None:
        """Verify pyproject.toml contains pytest configuration."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        # Assert
        assert "tool" in config, "pyproject.toml missing [tool] section"
        assert "pytest" in config["tool"], "pyproject.toml missing [tool.pytest] section"
        assert "ini_options" in config["tool"]["pytest"], "pyproject.toml missing [tool.pytest.ini_options]"

    def test_pytest_markers_are_defined(self) -> None:
        """Verify pytest markers are defined in pyproject.toml."""
        # Arrange
        pyproject = Path("pyproject.toml")
        required_markers = ["gh_cli", "tmux", "textual", "slow"]

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        markers = config["tool"]["pytest"]["ini_options"]["markers"]

        # Assert
        marker_names = [marker.split(":")[0] for marker in markers]

        for required_marker in required_markers:
            assert required_marker in marker_names, f"pytest marker '{required_marker}' not defined"

    def test_pytest_markers_are_recognized_by_pytest(self) -> None:
        """Verify pytest recognizes the configured markers."""
        # Arrange & Act
        result = subprocess.run(
            ["pytest", "--markers"],
            capture_output=True,
            text=True,
            check=False,
        )

        # Assert
        assert result.returncode == 0, "pytest --markers command failed"

        markers_output = result.stdout

        required_markers = ["gh_cli", "tmux", "textual", "slow"]
        for marker in required_markers:
            assert marker in markers_output, f"pytest marker '{marker}' not recognized by pytest"

    def test_pytest_dev_dependencies_installed(self) -> None:
        """Verify pytest and required plugins are installed."""
        # Arrange
        required_packages = [
            "pytest",
            "pytest-cov",
            "pytest-textual-snapshot",
            "pytest-asyncio",
        ]

        # Act & Assert
        for package in required_packages:
            result = subprocess.run(
                ["python", "-c", f"import {package.replace('-', '_').replace('pytest_', 'pytest.')}"],
                capture_output=True,
                check=False,
            )

            # Some packages have different import names
            if result.returncode != 0:
                result = subprocess.run(
                    ["python", "-c", f"import {package.replace('-', '_')}"],
                    capture_output=True,
                    check=False,
                )

            assert result.returncode == 0, f"Required package '{package}' is not installed"

    def test_pyproject_toml_has_textual_snapshot_dependency(self) -> None:
        """Verify pytest-textual-snapshot is in dev dependencies."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        dev_deps = config["project"]["optional-dependencies"]["dev"]

        # Assert
        assert any("pytest-textual-snapshot" in dep for dep in dev_deps), "pytest-textual-snapshot not in dev dependencies"


class TestCoverageConfiguration:
    """Tests for coverage configuration in pyproject.toml."""

    def test_pyproject_toml_has_coverage_config(self) -> None:
        """Verify pyproject.toml contains coverage configuration."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        # Assert
        assert "tool" in config, "pyproject.toml missing [tool] section"
        assert "coverage" in config["tool"], "pyproject.toml missing [tool.coverage] section"
        assert "run" in config["tool"]["coverage"], "pyproject.toml missing [tool.coverage.run] section"
        assert "report" in config["tool"]["coverage"], "pyproject.toml missing [tool.coverage.report] section"

    def test_coverage_targets_correct_source_directory(self) -> None:
        """Verify coverage configuration targets src/open_orchestrator."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        coverage_config = config["tool"]["coverage"]["run"]

        # Assert
        assert "source" in coverage_config, "coverage.run missing 'source' configuration"
        assert "src/open_orchestrator" in coverage_config["source"], "coverage should target src/open_orchestrator"

    def test_coverage_fail_under_threshold_set(self) -> None:
        """Verify coverage fail_under threshold is set to 90%."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        coverage_report = config["tool"]["coverage"]["report"]

        # Assert
        assert "fail_under" in coverage_report, "coverage.report missing 'fail_under' threshold"
        assert coverage_report["fail_under"] == 90.0, "coverage fail_under should be 90.0 for 90% target"

    def test_coverage_html_output_configured(self) -> None:
        """Verify coverage HTML report output is configured."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        # Assert
        assert "html" in config["tool"]["coverage"], "pyproject.toml missing [tool.coverage.html] section"
        coverage_html = config["tool"]["coverage"]["html"]
        assert "directory" in coverage_html, "coverage.html missing 'directory' configuration"
        assert coverage_html["directory"] == "htmlcov", "coverage HTML directory should be 'htmlcov'"

    def test_coverage_xml_output_configured(self) -> None:
        """Verify coverage XML report output is configured."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        # Assert
        assert "xml" in config["tool"]["coverage"], "pyproject.toml missing [tool.coverage.xml] section"
        coverage_xml = config["tool"]["coverage"]["xml"]
        assert "output" in coverage_xml, "coverage.xml missing 'output' configuration"
        assert coverage_xml["output"] == "coverage.xml", "coverage XML output should be 'coverage.xml'"

    def test_coverage_branch_coverage_enabled(self) -> None:
        """Verify branch coverage is enabled."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        coverage_run = config["tool"]["coverage"]["run"]

        # Assert
        assert "branch" in coverage_run, "coverage.run missing 'branch' configuration"
        assert coverage_run["branch"] is True, "branch coverage should be enabled"

    def test_coverage_omits_test_files(self) -> None:
        """Verify coverage configuration omits test files."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act
        with pyproject.open("rb") as f:
            config = tomllib.load(f)

        coverage_run = config["tool"]["coverage"]["run"]

        # Assert
        assert "omit" in coverage_run, "coverage.run missing 'omit' configuration"
        omit_patterns = coverage_run["omit"]

        assert any("test" in pattern for pattern in omit_patterns), "coverage should omit test files"


class TestDockerEnvironmentIntegration:
    """Integration tests for Docker environment."""

    @pytest.mark.slow
    def test_pytest_discovers_test_files(self) -> None:
        """Verify pytest discovers test files in the tests/ directory."""
        # Arrange & Act
        result = subprocess.run(
            ["pytest", "--collect-only", "-q"],
            capture_output=True,
            text=True,
            check=False,
        )

        # Assert
        assert result.returncode == 0, "pytest test discovery failed"
        assert "test session starts" in result.stdout or len(result.stdout) > 0, "pytest should discover tests"

    @pytest.mark.slow
    def test_coverage_report_generates_successfully(self) -> None:
        """Verify coverage report generates without errors."""
        # Arrange
        test_file = Path("tests/test_docker_infrastructure.py")

        # Act
        result = subprocess.run(
            [
                "pytest",
                str(test_file),
                "--cov=src/open_orchestrator",
                "--cov-report=term",
                "-v",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        # Assert
        assert result.returncode == 0, f"pytest with coverage failed: {result.stderr}"
        assert "coverage" in result.stdout.lower() or "cov" in result.stdout.lower(), "coverage report not generated"


class TestDockerConfigurationEdgeCases:
    """Tests for edge cases and error conditions in Docker configuration."""

    def test_docker_compose_requires_dockerfile_test(self) -> None:
        """Verify docker-compose.test.yml references Dockerfile.test that exists."""
        # Arrange
        compose_file = Path("docker-compose.test.yml")
        dockerfile = Path("Dockerfile.test")

        # Act
        with compose_file.open("r", encoding="utf-8") as f:
            compose_config = yaml.safe_load(f)

        # Assert
        assert dockerfile.exists(), "Dockerfile.test referenced in docker-compose.test.yml does not exist"

        for service in compose_config["services"].values():
            if "build" in service and "dockerfile" in service["build"]:
                referenced_dockerfile = Path(service["build"]["dockerfile"])
                assert referenced_dockerfile.exists(), f"Dockerfile {referenced_dockerfile} does not exist"

    def test_pyproject_toml_valid_toml_syntax(self) -> None:
        """Verify pyproject.toml has valid TOML syntax."""
        # Arrange
        pyproject = Path("pyproject.toml")

        # Act & Assert
        assert pyproject.exists(), "pyproject.toml not found"

        try:
            with pyproject.open("rb") as f:
                tomllib.load(f)
        except Exception as e:
            pytest.fail(f"pyproject.toml has invalid TOML syntax: {e}")

    def test_coverage_configuration_validates_without_errors(self) -> None:
        """Verify coverage configuration is valid."""
        # Arrange & Act
        result = subprocess.run(
            ["coverage", "debug", "config"],
            capture_output=True,
            text=True,
            check=False,
        )

        # Assert
        # Coverage debug config returns 0 on success, non-zero on errors
        assert result.returncode == 0, f"coverage configuration is invalid: {result.stderr}"

    def test_docker_compose_environment_variables_properly_formatted(self) -> None:
        """Verify environment variables in docker-compose.test.yml are properly formatted."""
        # Arrange
        compose_file = Path("docker-compose.test.yml")

        # Act
        with compose_file.open("r", encoding="utf-8") as f:
            compose_config = yaml.safe_load(f)

        # Assert
        for service_name, service in compose_config["services"].items():
            if "environment" in service:
                env_vars = service["environment"]

                for env_var in env_vars:
                    if isinstance(env_var, str):
                        # Environment variables should be in KEY=VALUE or KEY=${VAR:-default} format
                        assert "=" in env_var or env_var.isupper(), (
                            f"Environment variable '{env_var}' in service '{service_name}' has invalid format"
                        )

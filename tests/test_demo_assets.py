"""
Tests for demo assets (GIF, VHS tape).

Validates that the demo GIF meets requirements:
- Exists at /assets/demo.gif
- File size under 5MB
- Valid GIF format
- README references the GIF correctly
"""

from pathlib import Path

import pytest

# Project root (relative to tests directory)
PROJECT_ROOT = Path(__file__).parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
README_PATH = PROJECT_ROOT / "README.md"


class TestDemoGIF:
    """Tests for demo GIF file requirements."""

    def test_gif_exists(self):
        """Test that demo.gif exists in assets directory."""
        gif_path = ASSETS_DIR / "demo.gif"
        assert gif_path.exists(), f"Demo GIF not found at {gif_path}"

    def test_gif_size_under_5mb(self):
        """Test that GIF file size is under 5MB."""
        gif_path = ASSETS_DIR / "demo.gif"
        if not gif_path.exists():
            pytest.skip("Demo GIF not found")

        size_bytes = gif_path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)

        assert size_mb < 5, f"GIF size {size_mb:.2f}MB exceeds 5MB limit"

    def test_gif_valid_format(self):
        """Test that the file is a valid GIF format."""
        gif_path = ASSETS_DIR / "demo.gif"
        if not gif_path.exists():
            pytest.skip("Demo GIF not found")

        # Read first bytes to check GIF magic number
        with open(gif_path, "rb") as f:
            header = f.read(6)

        # GIF files start with "GIF87a" or "GIF89a"
        assert header[:3] == b"GIF", f"Invalid GIF header: {header[:3]}"
        assert header[3:6] in (b"87a", b"89a"), f"Invalid GIF version: {header[3:6]}"

    def test_gif_minimum_dimensions(self):
        """Test that GIF has minimum dimensions for readability."""
        gif_path = ASSETS_DIR / "demo.gif"
        if not gif_path.exists():
            pytest.skip("Demo GIF not found")

        try:
            from PIL import Image

            with Image.open(gif_path) as img:
                width, height = img.size
                assert width >= 800, f"GIF width {width}px below 800px minimum"
                assert height >= 400, f"GIF height {height}px below 400px minimum"
        except ImportError:
            pytest.skip("Pillow not installed")


class TestVHSTape:
    """Tests for VHS tape file."""

    def test_tape_file_exists(self):
        """Test that demo.tape exists in assets directory."""
        tape_path = ASSETS_DIR / "demo.tape"
        assert tape_path.exists(), f"VHS tape not found at {tape_path}"

    def test_tape_has_required_commands(self):
        """Test that tape file contains required VHS commands."""
        tape_path = ASSETS_DIR / "demo.tape"
        if not tape_path.exists():
            pytest.skip("VHS tape not found")

        content = tape_path.read_text()

        # Check for essential VHS commands
        assert "Output" in content, "Tape missing Output directive"
        assert "Set Width" in content, "Tape missing Width setting"
        assert "Set Height" in content, "Tape missing Height setting"

    def test_tape_output_path_correct(self):
        """Test that tape outputs to correct location."""
        tape_path = ASSETS_DIR / "demo.tape"
        if not tape_path.exists():
            pytest.skip("VHS tape not found")

        content = tape_path.read_text()

        # Should output to assets/demo.gif
        assert "assets/demo.gif" in content, "Tape should output to assets/demo.gif"


class TestReadmeGifIntegration:
    """Tests for README.md GIF integration."""

    def test_readme_contains_gif_reference(self):
        """Test that README.md contains GIF reference."""
        assert README_PATH.exists(), f"README.md not found at {README_PATH}"

        content = README_PATH.read_text()

        # Check for GIF reference with correct path
        assert "![" in content and "demo.gif" in content, "README missing demo.gif image reference"

    def test_readme_gif_reference_correct_format(self):
        """Test that README uses correct markdown format for GIF."""
        if not README_PATH.exists():
            pytest.skip("README.md not found")

        content = README_PATH.read_text()

        # Should use relative path format
        assert "![Open Orchestrator Demo](./assets/demo.gif)" in content, (
            "README should use format: ![Open Orchestrator Demo](./assets/demo.gif)"
        )

    def test_readme_gif_above_overview(self):
        """Test that GIF appears before Overview section."""
        if not README_PATH.exists():
            pytest.skip("README.md not found")

        content = README_PATH.read_text()

        gif_pos = content.find("demo.gif")
        overview_pos = content.find("## Overview")

        if gif_pos == -1:
            pytest.fail("GIF reference not found in README")

        if overview_pos == -1:
            pytest.skip("Overview section not found")

        assert gif_pos < overview_pos, "GIF should appear before Overview section"

    def test_readme_valid_markdown(self):
        """Test that README markdown is valid (basic check)."""
        if not README_PATH.exists():
            pytest.skip("README.md not found")

        content = README_PATH.read_text()

        # Basic markdown validation
        # Check for balanced brackets in image links
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            if "![" in line:
                # Simple check: should have matching ]( and )
                assert "](" in line, f"Line {i}: Incomplete image markdown syntax"
                # Count brackets for basic balance check
                open_brackets = line.count("[")
                close_brackets = line.count("]")
                # Basic balance check (not perfect but catches common errors)
                assert open_brackets <= close_brackets + 1, f"Line {i}: Unbalanced brackets"


class TestAssetsDirectory:
    """Tests for assets directory structure."""

    def test_assets_directory_exists(self):
        """Test that assets directory exists."""
        assert ASSETS_DIR.exists(), f"Assets directory not found at {ASSETS_DIR}"

    def test_assets_directory_is_directory(self):
        """Test that assets is a directory, not a file."""
        if not ASSETS_DIR.exists():
            pytest.skip("Assets directory not found")

        assert ASSETS_DIR.is_dir(), f"{ASSETS_DIR} should be a directory"

    def test_assets_not_empty(self):
        """Test that assets directory is not empty."""
        if not ASSETS_DIR.exists():
            pytest.skip("Assets directory not found")

        files = list(ASSETS_DIR.iterdir())
        assert len(files) > 0, "Assets directory is empty"
